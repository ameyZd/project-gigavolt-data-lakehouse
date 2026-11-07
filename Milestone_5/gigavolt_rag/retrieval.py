"""Hybrid retrieval: route the question, query the chosen databases in parallel, merge context.

Stage 1 (parallel threads)          Stage 2 (multi-hop, optional)
  MariaDB  -> ERP facts               fault/component codes found in MongoDB telemetry
  MongoDB  -> telemetry patterns  ->  are appended to a 2nd ChromaDB query, so incident
  ChromaDB -> PDF passages            reports / ECOs about those exact codes are pulled in.

A failure in one source never aborts the question: its error is recorded and the answer is
generated from the sources that did respond (graceful degradation).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .router import RetrievalPlan, plan_retrieval
from .sources.base import SOURCE_PREFIX, ContextItem, SourceResult

MAX_ITEMS = {"sql": 12, "document": 8, "vector": 7}


@dataclass
class RetrievalResult:
    plan: RetrievalPlan
    items: list[ContextItem]
    sources: dict[str, SourceResult]
    timings_ms: dict[str, float] = field(default_factory=dict)

    def by_citation(self) -> dict[str, ContextItem]:
        return {item.citation_id: item for item in self.items}

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "sources": {name: result.to_dict() for name, result in self.sources.items()},
            "items": [item.to_dict() for item in self.items],
            "timings_ms": {k: round(v, 3) for k, v in self.timings_ms.items()},
        }


class HybridRetriever:
    def __init__(self, settings: Settings, sql=None, vector=None, document=None):
        self.settings = settings
        if sql is None or vector is None or document is None:
            from .sources.document_source import DocumentSource
            from .sources.sql_source import SQLSource
            from .sources.vector_source import VectorSource

            sql = sql or SQLSource(settings)
            vector = vector or VectorSource(settings)
            document = document or DocumentSource(settings)
        self.sql, self.vector, self.document = sql, vector, document
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="retrieval")

    def warmup(self) -> dict[str, str]:
        """Open connections and load the embedding model once (excluded from per-question latency)."""
        status = {}
        for name, source in (("sql", self.sql), ("vector", self.vector), ("document", self.document)):
            started = time.perf_counter()
            try:
                source.warmup()
                status[name] = f"ok ({(time.perf_counter() - started):.2f} s)"
            except Exception as error:
                status[name] = f"FAILED: {error.__class__.__name__}: {error}"
        return status

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _safe(source_type: str, func, *args) -> SourceResult:
        started = time.perf_counter()
        try:
            return func(*args)
        except Exception as error:
            return SourceResult(source_type=source_type, error=f"{error.__class__.__name__}: {error}",
                                elapsed_ms=(time.perf_counter() - started) * 1000)

    def retrieve(self, question: str) -> RetrievalResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        plan = plan_retrieval(question)
        timings["route"] = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        futures = {}
        if plan.use_sql:
            futures["sql"] = self._pool.submit(self._safe, "sql", self.sql.retrieve, plan)
        if plan.use_document:
            futures["document"] = self._pool.submit(self._safe, "document", self.document.retrieve, plan)
        if plan.use_vector:
            futures["vector"] = self._pool.submit(self._safe, "vector", self.vector.retrieve_query, plan.vector_query,
                                                  self.settings.vector_top_k, plan.vector_doc_types, "primary")
        sources: dict[str, SourceResult] = {name: future.result() for name, future in futures.items()}
        for name in ("sql", "document", "vector"):
            if name not in sources:
                sources[name] = SourceResult(source_type=name, skipped_reason="not needed for this question type")
        timings["stage1_parallel"] = (time.perf_counter() - t1) * 1000
        for name, result in sources.items():
            timings[name] = result.elapsed_ms
        if sources["vector"].details:
            timings["vector_embed"] = sources["vector"].details.get("embed_ms", 0.0)
            timings["vector_search"] = sources["vector"].details.get("search_ms", 0.0)

        # ---- Stage 2: multi-hop vector expansion from telemetry codes ----
        timings["multi_hop"] = 0.0
        if plan.multi_hop and plan.use_vector and sources["document"].items:
            from .sources.document_source import DocumentSource

            new_terms = [t for t in DocumentSource.expansion_terms(sources["document"].items)
                         if t not in plan.vector_query]
            if new_terms:
                t2 = time.perf_counter()
                hop = self._safe("vector", self.vector.retrieve_query,
                                 "Gigavolt engineering change order or incident report about " + " ".join(new_terms),
                                 3, None, "multi_hop")
                timings["multi_hop"] = (time.perf_counter() - t2) * 1000
                known = {i.primary_record for i in sources["vector"].items}
                added = [i for i in hop.items if i.primary_record not in known]
                sources["vector"].items.extend(added)
                sources["vector"].details["multi_hop"] = {
                    "terms": new_terms, "added": [i.primary_record for i in added],
                    "elapsed_ms": round(timings["multi_hop"], 3), "error": hop.error,
                }

        # ---- Merge, cap, and assign citation IDs (S1.., D1.., V1..) ----
        t3 = time.perf_counter()
        items: list[ContextItem] = []
        for name in ("sql", "document", "vector"):
            selected = sources[name].items[: MAX_ITEMS[name]]
            sources[name].items = selected
            for number, item in enumerate(selected, start=1):
                item.citation_id = f"{SOURCE_PREFIX[name]}{number}"
                items.append(item)
        timings["merge"] = (time.perf_counter() - t3) * 1000
        timings["retrieval_total"] = (time.perf_counter() - t0) * 1000
        return RetrievalResult(plan=plan, items=items, sources=sources, timings_ms=timings)

"""End-to-end RAG pipeline: question -> hybrid retrieval -> prompt -> LLM -> provenance check.

Timings are captured for every stage so latency can be profiled (retrieval vs. generation).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import OUTPUT_DIR, Settings
from .llm import LLMClient, LLMResponse, extractive_answer
from .prompts import build_prompt
from .provenance import ProvenanceReport, validate_answer
from .retrieval import HybridRetriever, RetrievalResult
from .sources.base import jsonable

MAX_QUESTION_CHARS = 1000


@dataclass
class RAGResult:
    question: str
    answer: str
    retrieval: RetrievalResult | None
    provenance: ProvenanceReport | None
    llm: LLMResponse | None
    timings_ms: dict[str, float]
    mode: str                      # "llm" | "extractive_fallback" | "no_context" | "invalid_question"
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def total_ms(self) -> float:
        return self.timings_ms.get("total", 0.0)

    def to_dict(self) -> dict[str, Any]:
        return jsonable({
            "created_at": self.created_at,
            "question": self.question,
            "answer": self.answer,
            "mode": self.mode,
            "warnings": self.warnings,
            "llm": None if not self.llm else {
                "provider": self.llm.provider, "model": self.llm.model, "elapsed_ms": round(self.llm.elapsed_ms, 3),
                "input_tokens": self.llm.input_tokens, "output_tokens": self.llm.output_tokens,
                "error": self.llm.error, "attempts": self.llm.attempts, "notes": self.llm.notes,
            },
            "timings_ms": {k: round(v, 3) for k, v in self.timings_ms.items()},
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        })


class RAGPipeline:
    def __init__(self, settings: Settings | None = None, retriever: HybridRetriever | None = None,
                 llm: LLMClient | None = None):
        self.settings = settings or Settings.from_env()
        self.retriever = retriever or HybridRetriever(self.settings)
        self.llm = llm or LLMClient(self.settings)

    def warmup(self) -> dict[str, str]:
        return self.retriever.warmup()

    def answer(self, question: str) -> RAGResult:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        question = re.sub(r"\s+", " ", (question or "")).strip()
        if len(question) < 3:
            return RAGResult(question, "Please ask a question about Gigavolt equipment, telemetry, documents or suppliers.",
                             None, None, None, {"total": 0.0}, "invalid_question")
        warnings: list[str] = []
        if len(question) > MAX_QUESTION_CHARS:
            question = question[:MAX_QUESTION_CHARS]
            warnings.append(f"Question truncated to {MAX_QUESTION_CHARS} characters.")

        retrieval = self.retriever.retrieve(question)
        timings.update({f"retrieval.{k}": v for k, v in retrieval.timings_ms.items()})
        for name, source in retrieval.sources.items():
            if source.error:
                warnings.append(f"{name} source error: {source.error[:160]}")
            for error in source.details.get("errors", []) if source.details else []:
                if not source.error:
                    warnings.append(f"{name} partial error: {error[:160]}")

        if not retrieval.items:
            answer = ("Answer: The retrieved Gigavolt data does not contain information that answers this question, "
                      "so no answer was generated (nothing to cite).\nKey findings:\n- No relevant records were "
                      "returned by MariaDB, MongoDB or ChromaDB.\nGaps: The question may be outside the Gigavolt "
                      "knowledge base.")
            timings["total"] = (time.perf_counter() - t0) * 1000
            return RAGResult(question, answer, retrieval, ProvenanceReport(), None, timings, "no_context", warnings)

        t1 = time.perf_counter()
        system, user = build_prompt(retrieval)
        timings["prompt_build"] = (time.perf_counter() - t1) * 1000

        llm_response = self.llm.generate(system, user)
        timings["llm_generation"] = llm_response.elapsed_ms
        mode = "llm"
        answer = llm_response.text.strip()
        if not llm_response.ok:
            mode = "extractive_fallback"
            if self.llm.provider != "extractive":
                warnings.append(f"LLM unavailable ({llm_response.error}); returned extractive cited answer instead.")
            answer = extractive_answer(question, retrieval.items)

        t2 = time.perf_counter()
        provenance = validate_answer(answer, retrieval.items)
        timings["provenance_check"] = (time.perf_counter() - t2) * 1000
        if provenance.invalid_citations:
            warnings.append(f"Answer cited tags not in context: {', '.join(provenance.invalid_citations)}")
        if provenance.uncited:
            warnings.append(f"{len(provenance.uncited)} statement(s) without citations (shown with ⚠).")
        timings["total"] = (time.perf_counter() - t0) * 1000
        timings["retrieval_share_pct"] = 100.0 * timings.get("retrieval.retrieval_total", 0.0) / max(timings["total"], 1e-6)
        return RAGResult(question, answer, retrieval, provenance, llm_response, timings, mode, warnings)


def slugify(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "question"


def save_result(result: RAGResult, directory: Path | None = None) -> Path:
    directory = directory or (OUTPUT_DIR / "runs")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"{stamp}_{slugify(result.question)}.json"
    data = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(data, encoding="utf-8")
    (OUTPUT_DIR / "last_result.json").write_text(data, encoding="utf-8")
    return path

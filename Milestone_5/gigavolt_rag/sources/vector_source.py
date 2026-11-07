"""ChromaDB retrieval over the technical-PDF HNSW collection built by scripts/00_build_vector_store.py.

Same design as Milestone 4, rebuilt inside Milestone_5/data: collection ``gigavolt_unstructured_v1`` (cosine,
M=16, construction_ef=200, search_ef=64) and the ``all-MiniLM-L6-v2`` embedding model.
The model is loaded once at warm-up so per-question cost is one ~10-30 ms CPU encode.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from ..config import Settings
from .base import ContextItem, SourceResult

DB_LABEL = "ChromaDB (HNSW)"


class VectorSource:
    source_type = "vector"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._collection = None
        self._lock = threading.Lock()
        self.indexed_at = self._read_index_time()

    def _read_index_time(self) -> str | None:
        try:
            manifest = json.loads(self.settings.embedding_manifest.read_text(encoding="utf-8"))
            return f"indexed {manifest.get('generated_at_utc', '')[:19]}Z"
        except Exception:
            return None

    def _load_collection(self) -> None:
        with self._lock:
            if self._collection is None:
                os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")  # silence Chroma usage telemetry
                try:
                    import chromadb
                except ImportError as error:  # pragma: no cover
                    raise RuntimeError("chromadb missing: pip install -r requirements.txt") from error
                if not self.settings.chroma_path.exists():
                    raise RuntimeError(f"ChromaDB path not found: {self.settings.chroma_path} "
                                       "(run: python scripts/00_build_vector_store.py)")
                client = chromadb.PersistentClient(path=str(self.settings.chroma_path))
                self._collection = client.get_collection(self.settings.chroma_collection, embedding_function=None)

    def _load(self) -> None:
        self._load_collection()
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as error:  # pragma: no cover
                    raise RuntimeError("sentence-transformers missing: pip install -r requirements.txt") from error
                self._model = SentenceTransformer(self.settings.embedding_model, device="cpu")

    @property
    def collection(self):
        self._load_collection()
        return self._collection

    def warmup(self) -> None:
        self._load()
        self.embed("warm-up query for thermal runaway procedures")
        self.search_vector(self.embed("battery cell supplier"), top_k=1)

    def health(self) -> dict[str, Any]:
        self._load_collection()
        metadata = self._collection.metadata or {}
        return {
            "collection": self.settings.chroma_collection,
            "vectors": self._collection.count(),
            "hnsw": {k: v for k, v in metadata.items() if k.startswith("hnsw:")},
            "embedding_model": self.settings.embedding_model,
        }

    def embed(self, text: str) -> list[float]:
        self._load()
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def search_vector(self, vector: list[float], top_k: int, doc_types: list[str] | None = None) -> dict[str, Any]:
        where = {"document_type": {"$in": doc_types}} if doc_types else None
        kwargs: dict[str, Any] = dict(
            query_embeddings=[vector],
            n_results=max(1, min(top_k, self.collection.count())),
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        return self.collection.query(**kwargs)

    def _to_items(self, raw: dict[str, Any], max_distance: float, stage: str) -> list[ContextItem]:
        items: list[ContextItem] = []
        for chunk_id, text, meta, distance in zip(raw["ids"][0], raw["documents"][0], raw["metadatas"][0], raw["distances"][0]):
            if distance > max_distance:
                continue
            items.append(ContextItem(
                source_type="vector",
                database=DB_LABEL,
                collection=self.settings.chroma_collection,
                record_ids=[chunk_id],
                title=f"{meta.get('source_file')} p.{meta.get('page_number')}",
                text=text,
                kind="record",
                document_name=meta.get("source_file"),
                page=int(meta.get("page_number")) if meta.get("page_number") is not None else None,
                timestamp=self.indexed_at,
                score=round(1.0 - float(distance), 4),
                verify={"engine": "chromadb", "ids": [chunk_id], "stage": stage},
            ))
        return items

    def retrieve_query(self, query: str, top_k: int, doc_types: list[str] | None, stage: str) -> SourceResult:
        started = time.perf_counter()
        result = SourceResult(source_type="vector")
        try:
            t0 = time.perf_counter()
            vector = self.embed(query)
            embed_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            raw = self.search_vector(vector, top_k, doc_types)
            if doc_types and len(raw["ids"][0]) < 2:  # filter too narrow -> fall back to the whole corpus
                raw = self.search_vector(vector, top_k, None)
                doc_types = None
            search_ms = (time.perf_counter() - t1) * 1000
            result.items = self._to_items(raw, self.settings.vector_max_distance, stage)
            result.details = {
                "stage": stage,
                "query": query,
                "doc_type_filter": doc_types,
                "embed_ms": round(embed_ms, 3),
                "search_ms": round(search_ms, 3),
                "candidates": len(raw["ids"][0]),
                "kept_after_distance_filter": len(result.items),
                "max_cosine_distance": self.settings.vector_max_distance,
            }
        except Exception as error:
            result.error = f"{error.__class__.__name__}: {error}"
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

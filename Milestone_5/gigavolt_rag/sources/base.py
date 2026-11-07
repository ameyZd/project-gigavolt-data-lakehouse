"""Shared data structures for every retrieval source.

A ``ContextItem`` is the unit of provenance: one cited fact bundle that can be traced back
to a specific database, record and timestamp, and re-fetched later with ``verify``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

SOURCE_PREFIX = {"sql": "S", "vector": "V", "document": "D", "graph": "G"}
SOURCE_LABEL = {
    "sql": "SQL · MariaDB",
    "vector": "Vector · ChromaDB HNSW",
    "document": "Document · MongoDB",
    "graph": "Graph (not used in M5)",
}


def jsonable(value: Any) -> Any:
    """Convert DB-native values (Decimal, date, datetime, ObjectId...) into JSON-safe values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class ContextItem:
    source_type: str              # "sql" | "vector" | "document"
    database: str                 # e.g. "MariaDB gigavolt_silver"
    collection: str               # table / view / collection name
    record_ids: list[str]         # primary keys of the exact source records
    title: str                    # short human label
    text: str                     # the evidence shown to the LLM
    kind: str = "record"          # "record" | "aggregate" | "absence"
    document_name: str | None = None   # PDF file name for vector chunks
    page: int | None = None
    timestamp: str | None = None       # event/record time (or index time for documents)
    score: float | None = None         # similarity for vector hits
    verify: dict[str, Any] = field(default_factory=dict)  # how to re-fetch the evidence
    citation_id: str = ""

    @property
    def primary_record(self) -> str:
        return self.record_ids[0] if self.record_ids else "n/a"

    def citation_line(self) -> str:
        """Compact provenance string: database type, record ID, document name, timestamp."""
        parts = [SOURCE_LABEL.get(self.source_type, self.source_type), self.collection]
        ids = ", ".join(self.record_ids[:4]) + (f" (+{len(self.record_ids) - 4} more)" if len(self.record_ids) > 4 else "")
        parts.append(f"record {ids}" if ids else "record n/a")
        if self.document_name:
            parts.append(f"{self.document_name}" + (f" p.{self.page}" if self.page else ""))
        if self.timestamp:
            parts.append(self.timestamp)
        return " | ".join(parts)

    def to_prompt_block(self, max_chars: int = 1400) -> str:
        meta = [f"source={self.database}.{self.collection}", f"type={self.kind}"]
        meta.append("record_id=" + (", ".join(self.record_ids[:6]) or "n/a"))
        if self.document_name:
            meta.append(f"document={self.document_name}" + (f" page {self.page}" if self.page else ""))
        if self.timestamp:
            meta.append(f"timestamp={self.timestamp}")
        body = self.text if len(self.text) <= max_chars else self.text[: max_chars - 3].rstrip() + "..."
        return f"[{self.citation_id}] {' | '.join(meta)}\n{body}"

    def to_dict(self) -> dict[str, Any]:
        return jsonable(asdict(self))


@dataclass
class SourceResult:
    source_type: str
    items: list[ContextItem] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None
    skipped_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)  # sub-timings, queries run, etc.

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
            "skipped_reason": self.skipped_reason,
            "item_count": len(self.items),
            "details": jsonable(self.details),
        }


def dumps(value: Any) -> str:
    return json.dumps(jsonable(value), indent=2, ensure_ascii=False)

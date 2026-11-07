"""Citation validation and statement -> source provenance mapping.

After the LLM answers, every statement is checked:
1. Does it carry at least one citation tag?            (uncited  -> flagged)
2. Does every tag exist in the retrieved context?      (invented -> flagged as invalid)
3. Do the numbers in the statement appear in the cited records? (unsupported -> flagged)

The result is a provenance map: statement -> [database type, record IDs, document, timestamp].
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .sources.base import SOURCE_LABEL, ContextItem

TAG_GROUP_RE = re.compile(r"\[((?:[SVDG]\d{1,3})(?:\s*[,;]\s*[SVDG]?\d{1,3})*)\]")
SINGLE_TAG_RE = re.compile(r"[SVDG]\d{1,3}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
NUMBER_RE = re.compile(r"(?<![\w\-./:#])(\d+(?:,\d{3})*(?:\.\d+)?)(?![\w\-/:])")
HEADING_RE = re.compile(r"^\s*(#+\s.*|\*\*[^*]+\*\*:?|[A-Z][A-Za-z /&-]{1,40}:)\s*$")
NON_FACT_RE = re.compile(r"^(gaps:\s*none\.?|none\.?)$", re.IGNORECASE)
QUOTED_RE = re.compile(r"(?<![A-Za-z])'[^'\n]{3,240}'(?![A-Za-z])|\"[^\"\n]{3,320}\"")
ABBREVIATIONS_RE = re.compile(r"\b(e\.g|i\.e|approx|vs|no|fig|p)\.\s", re.IGNORECASE)


def _sentences(line: str) -> list[str]:
    """Split a line into sentences without breaking inside quoted text or common abbreviations."""
    protected: list[str] = []

    def hold(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    masked = QUOTED_RE.sub(hold, line)
    masked = ABBREVIATIONS_RE.sub(lambda m: m.group(0).replace(". ", ".\x01"), masked)
    pieces = SENTENCE_SPLIT_RE.split(masked)
    restored = []
    for piece in pieces:
        piece = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], piece).replace(".\x01", ". ")
        restored.append(piece)
    return restored


@dataclass
class Statement:
    text: str
    citations: list[str]
    valid_citations: list[str]
    invalid_citations: list[str]
    numbers: list[str]
    unsupported_numbers: list[str]

    @property
    def cited(self) -> bool:
        return bool(self.valid_citations)


@dataclass
class ProvenanceReport:
    statements: list[Statement] = field(default_factory=list)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def factual(self) -> list[Statement]:
        return self.statements

    @property
    def coverage_pct(self) -> float:
        if not self.statements:
            return 0.0
        return 100.0 * sum(1 for s in self.statements if s.cited) / len(self.statements)

    @property
    def invalid_citations(self) -> list[str]:
        return sorted({c for s in self.statements for c in s.invalid_citations})

    @property
    def uncited(self) -> list[str]:
        return [s.text for s in self.statements if not s.cited]

    @property
    def unsupported_numbers(self) -> list[tuple[str, str]]:
        return [(s.text, n) for s in self.statements for n in s.unsupported_numbers]

    @property
    def numeric_grounding_pct(self) -> float:
        total = sum(len(s.numbers) for s in self.statements)
        if not total:
            return 100.0
        bad = sum(len(s.unsupported_numbers) for s in self.statements)
        return 100.0 * (total - bad) / total

    @property
    def used_citations(self) -> list[str]:
        order: list[str] = []
        for statement in self.statements:
            for tag in statement.valid_citations:
                if tag not in order:
                    order.append(tag)
        return order

    @property
    def passed(self) -> bool:
        return bool(self.statements) and self.coverage_pct == 100.0 and not self.invalid_citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_pct": round(self.coverage_pct, 1),
            "numeric_grounding_pct": round(self.numeric_grounding_pct, 1),
            "passed": self.passed,
            "invalid_citations": self.invalid_citations,
            "uncited_statements": self.uncited,
            "unsupported_numbers": [{"statement": s, "number": n} for s, n in self.unsupported_numbers],
            "statements": [asdict(s) for s in self.statements],
            "sources": self.sources,
        }


def expand_tags(group: str) -> list[str]:
    """'S1, S2; D3' -> ['S1','S2','D3'];  'S1, 2' -> ['S1','S2'] (prefix carried forward)."""
    tags, prefix = [], ""
    for part in re.split(r"\s*[,;]\s*", group.strip()):
        match = re.fullmatch(r"([SVDG]?)(\d{1,3})", part)
        if not match:
            continue
        prefix = match.group(1) or prefix
        if prefix:
            tags.append(f"{prefix}{match.group(2)}")
    return tags


def split_statements(answer: str) -> list[str]:
    statements: list[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line or HEADING_RE.match(line):
            continue
        line = re.sub(r"^([-*•]|\d+[.)])\s+", "", line)
        line = re.sub(r"^(Answer|Key findings|Gaps|Summary)\s*:\s*", "", line, flags=re.IGNORECASE)
        if not line or NON_FACT_RE.match(line) or line.lower().startswith("sources"):
            continue
        for piece in _sentences(line):
            piece = piece.strip()
            if not piece:
                continue
            # a fragment that is only citation tags belongs to the previous sentence
            if statements and not TAG_GROUP_RE.sub("", piece).strip(" .;,"):
                statements[-1] = f"{statements[-1]} {piece}"
            else:
                statements.append(piece)
    return statements


def _numbers(text: str) -> list[str]:
    cleaned = TAG_GROUP_RE.sub(" ", text)
    return [n for n in NUMBER_RE.findall(cleaned) if not re.fullmatch(r"[0-9]", n)]  # single digits are too noisy


def _number_supported(number: str, evidence: str) -> bool:
    if number in evidence:
        return True
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return False
    for candidate in NUMBER_RE.findall(evidence):
        try:
            if abs(float(candidate.replace(",", "")) - value) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def validate_answer(answer: str, items: list[ContextItem]) -> ProvenanceReport:
    by_id = {item.citation_id: item for item in items}
    report = ProvenanceReport()
    for text in split_statements(answer):
        tags: list[str] = []
        for group in TAG_GROUP_RE.findall(text):
            for tag in expand_tags(group):
                if tag not in tags:
                    tags.append(tag)
        valid = [t for t in tags if t in by_id]
        invalid = [t for t in tags if t not in by_id]
        evidence = " ".join(by_id[t].text + " " + " ".join(by_id[t].record_ids) for t in valid)
        numbers = _numbers(text)
        unsupported = [n for n in numbers if valid and not _number_supported(n, evidence)]
        report.statements.append(Statement(text, tags, valid, invalid, numbers, unsupported))
    for tag in report.used_citations:
        item = by_id[tag]
        report.sources[tag] = {
            "database_type": SOURCE_LABEL.get(item.source_type, item.source_type),
            "collection": item.collection,
            "record_ids": item.record_ids,
            "document_name": item.document_name,
            "page": item.page,
            "timestamp": item.timestamp,
            "kind": item.kind,
            "citation": item.citation_line(),
        }
    return report

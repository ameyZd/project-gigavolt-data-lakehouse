"""Pure-Python parsing and normalization of the BMS telemetry feed (no MongoDB needed).

The instructor's ``telemetry_data.json`` is JSON Lines (one object per line). This module
also accepts a classic JSON array so the loader works with either export format.

Each source record keeps its original nested structure (``telemetry`` sub-document and
``flagged_components`` array) and gains a few derived fields that make indexing and
retrieval cheap:

* ``_id``            = ``log_id``  (idempotent upserts, natural provenance key)
* ``timestamp``      = real BSON date (string in the source)
* ``model_code``     = canonical model ("X-7000", "C-1200", "G-3500") for joins with ERP/docs
* ``fault_family``   = "INV" from "ERR_INV_404"
* ``is_fault``       = True when a fault code is present or status is WARNING/FAULT/CRITICAL_FAULT
* ``severity_rank``  = numeric severity for sorting (Info=0 ... Fatal=5)
* ``provenance``     = source file, line number, file hash, ingestion time
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SEVERITY_RANK = {"info": 0, "warning": 1, "moderate": 2, "high": 3, "critical": 4, "fatal": 5}
FAULT_STATUSES = {"WARNING", "FAULT", "CRITICAL_FAULT"}
KNOWN_STATUSES = {"STANDBY", "RAMP_UP", "OPERATIONAL", "WARNING", "FAULT", "CRITICAL_FAULT"}
TELEMETRY_NUMERIC_FIELDS = ("voltage_v", "current_a", "temperature_c", "soc_percent", "voltage_delta_mv")
FAULT_CODE_RE = re.compile(r"^ERR_([A-Z]+)_(\d{3})$")
MODEL_RE = re.compile(r"\b([XCG])-?(\d{3,5})\b", re.IGNORECASE)


def canonical_model(model_text: str | None) -> str | None:
    """Map "Gigavolt Megapack X-7000" -> "X-7000", "PowerStation G-3500" -> "G-3500"."""
    if not model_text:
        return None
    match = MODEL_RE.search(model_text)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"
    lowered = model_text.lower()
    if "megapack" in lowered:
        return "X-7000"
    if "powerstation" in lowered:
        return "G-3500"
    return model_text.strip()


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp missing or not a string")
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class ParsedFile:
    records: list[tuple[int, dict[str, Any]]]
    format: str
    sha256: str
    parse_errors: list[dict[str, Any]] = field(default_factory=list)


def read_telemetry_file(path: Path) -> ParsedFile:
    """Read NDJSON or a JSON array; return (line_number, record) pairs plus parse errors."""
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError("JSON array expected")
        return ParsedFile([(index + 1, record) for index, record in enumerate(data)], "json_array", digest)
    if stripped.startswith("{"):
        try:  # a single (possibly pretty-printed) JSON document, e.g. {"logs": [...]}
            whole = json.loads(stripped)
        except json.JSONDecodeError:
            whole = None  # "Extra data" -> it is JSON Lines; parse line by line below
        if isinstance(whole, dict):
            for key in ("logs", "records", "data", "telemetry_logs"):
                if isinstance(whole.get(key), list):
                    return ParsedFile(
                        [(index + 1, item) for index, item in enumerate(whole[key])], f"wrapped_{key}", digest
                    )
            return ParsedFile([(1, whole)], "single_object", digest)

    records: list[tuple[int, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append({"line": line_number, "error": str(error), "raw": line[:200]})
            continue
        records.append((line_number, obj))
    return ParsedFile(records, "ndjson", digest, errors)


def _to_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric reading")
    if isinstance(value, (int, float)):
        return value
    number = float(str(value).strip())
    return int(number) if number.is_integer() else number


def normalize_record(
    record: dict[str, Any],
    *,
    source_file: str,
    source_line: int,
    source_sha256: str,
    ingested_at: datetime,
) -> tuple[dict[str, Any], list[str]]:
    """Validate and enrich one telemetry record.

    Returns ``(document, warnings)``. Raises ``ValueError`` for records that must be quarantined.
    """
    if not isinstance(record, dict):
        raise ValueError("record is not a JSON object")
    warnings: list[str] = []

    log_id = str(record.get("log_id") or "").strip()
    if not log_id:
        raise ValueError("log_id missing")
    equipment_id = str(record.get("equipment_id") or "").strip().upper()
    if not equipment_id:
        raise ValueError("equipment_id missing")
    timestamp = parse_timestamp(record.get("timestamp"))

    status = str(record.get("status") or "").strip().upper()
    if not status:
        raise ValueError("status missing")
    if status not in KNOWN_STATUSES:
        warnings.append(f"unrecognized status '{status}' kept as-is")

    severity = str(record.get("severity") or "Info").strip()
    severity_rank = SEVERITY_RANK.get(severity.lower())
    if severity_rank is None:
        warnings.append(f"unrecognized severity '{severity}' ranked as 0")
        severity_rank = 0

    fault_code = record.get("fault_code")
    fault_code = str(fault_code).strip().upper() if fault_code not in (None, "", "null", "NULL") else None
    fault_family = None
    if fault_code:
        match = FAULT_CODE_RE.match(fault_code)
        if match:
            fault_family = match.group(1)
        else:
            warnings.append(f"fault_code '{fault_code}' does not match ERR_<FAMILY>_<NNN>")

    telemetry_in = record.get("telemetry") or {}
    if not isinstance(telemetry_in, dict):
        raise ValueError("telemetry is not an object")
    telemetry: dict[str, Any] = {}
    for key, value in telemetry_in.items():
        try:
            telemetry[key] = _to_number(value)
        except (TypeError, ValueError):
            raise ValueError(f"telemetry.{key} is not numeric: {value!r}") from None
    for key in TELEMETRY_NUMERIC_FIELDS:
        if key not in telemetry:
            warnings.append(f"telemetry.{key} missing")

    components = record.get("flagged_components") or []
    if isinstance(components, str):
        components = [part.strip() for part in components.split(",") if part.strip()]
    if not isinstance(components, list):
        raise ValueError("flagged_components is not an array")
    components = [str(item).strip().upper() for item in components if str(item).strip()]

    model = str(record.get("model") or "").strip() or None
    document: dict[str, Any] = {key: value for key, value in record.items()}  # keep unknown fields
    document.update(
        {
            "_id": log_id,
            "log_id": log_id,
            "timestamp": timestamp,
            "equipment_id": equipment_id,
            "model": model,
            "model_code": canonical_model(model),
            "status": status,
            "fault_code": fault_code,
            "fault_family": fault_family,
            "severity": severity,
            "severity_rank": severity_rank,
            "is_fault": bool(fault_code) or status in FAULT_STATUSES,
            "telemetry": telemetry,
            "flagged_components": components,
            "notes": str(record.get("notes") or "").strip(),
            "provenance": {
                "source_file": source_file,
                "source_line": source_line,
                "source_sha256": source_sha256,
                "ingested_at": ingested_at,
                "loader": "Milestone_5/scripts/01_load_telemetry_mongodb.py",
            },
        }
    )
    return document, warnings


def iter_normalized(path: Path, ingested_at: datetime | None = None) -> Iterator[tuple[str, Any]]:
    """Yield ("ok", doc, warnings) / ("reject", info) tuples for a telemetry file."""
    ingested_at = ingested_at or datetime.now(timezone.utc)
    parsed = read_telemetry_file(path)
    for error in parsed.parse_errors:
        yield "reject", {"line": error["line"], "reason": f"invalid JSON: {error['error']}", "raw": error["raw"]}
    for line_number, record in parsed.records:
        try:
            document, warnings = normalize_record(
                record,
                source_file=path.name,
                source_line=line_number,
                source_sha256=parsed.sha256,
                ingested_at=ingested_at,
            )
        except (ValueError, TypeError) as error:
            yield "reject", {"line": line_number, "reason": str(error), "raw": json.dumps(record)[:300]}
            continue
        yield "ok", (document, warnings)


# MongoDB $jsonSchema validator used by the loader. Enumerations are intentionally NOT
# enforced so a new status/severity value from the field is stored rather than rejected;
# the Python normalizer reports such values as warnings instead.
TELEMETRY_JSON_SCHEMA: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Gigavolt BMS telemetry event",
        "required": [
            "_id", "log_id", "timestamp", "equipment_id", "status",
            "severity", "severity_rank", "is_fault", "telemetry", "flagged_components", "provenance",
        ],
        "properties": {
            "_id": {"bsonType": "string"},
            "log_id": {"bsonType": "string", "pattern": "^TLM-"},
            "timestamp": {"bsonType": "date"},
            "equipment_id": {"bsonType": "string", "pattern": "^SN-"},
            "model": {"bsonType": ["string", "null"]},
            "model_code": {"bsonType": ["string", "null"]},
            "status": {"bsonType": "string"},
            "fault_code": {"bsonType": ["string", "null"]},
            "fault_family": {"bsonType": ["string", "null"]},
            "severity": {"bsonType": "string"},
            "severity_rank": {"bsonType": "int", "minimum": 0, "maximum": 5},
            "is_fault": {"bsonType": "bool"},
            "telemetry": {
                "bsonType": "object",
                "properties": {
                    key: {"bsonType": ["int", "long", "double", "decimal", "null"]}
                    for key in TELEMETRY_NUMERIC_FIELDS
                },
            },
            "flagged_components": {"bsonType": "array", "items": {"bsonType": "string"}},
            "notes": {"bsonType": "string"},
            "provenance": {
                "bsonType": "object",
                "required": ["source_file", "source_line", "source_sha256", "ingested_at"],
            },
        },
    }
}

# name -> (keys, options). Keys are lists of (field, direction) pairs.
TELEMETRY_INDEXES: list[tuple[str, list[tuple[str, Any]], dict[str, Any]]] = [
    ("ix_timestamp", [("timestamp", -1)], {}),
    ("ix_equipment_timestamp", [("equipment_id", 1), ("timestamp", -1)], {}),
    ("ix_fault_code_timestamp", [("fault_code", 1), ("timestamp", -1)], {}),
    ("ix_model_fault_timestamp", [("model_code", 1), ("is_fault", 1), ("timestamp", -1)], {}),
    ("ix_severity_timestamp", [("severity_rank", -1), ("timestamp", -1)], {}),
    ("ix_flagged_components", [("flagged_components", 1)], {}),
    ("ix_temperature", [("telemetry.temperature_c", -1)], {}),
    ("ix_notes_text", [("notes", "text")], {"default_language": "english"}),
]

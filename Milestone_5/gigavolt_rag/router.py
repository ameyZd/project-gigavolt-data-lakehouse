"""Rule-based query router: extract entities, classify intent, choose sources.

Why rules instead of an LLM classifier?  Routing runs before every question, so it must be
fast (< 1 ms), deterministic, and explainable in the demo. The router decides which of the
three databases to query and with which filters; the plan's ``rationale`` lines are printed
so a reviewer can see *why* each source was used or skipped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

SERIAL_RE = re.compile(r"\bSN-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
EQUIPMENT_RE = re.compile(r"\bEQ-\d{3,6}\b", re.IGNORECASE)
DISPATCH_RE = re.compile(r"\bDISP-\d{3,6}\b", re.IGNORECASE)
CLAIM_RE = re.compile(r"\bCLM-\d{3,6}\b", re.IGNORECASE)
PART_RE = re.compile(r"\bPRT-\d{3,6}\b", re.IGNORECASE)
FAULT_RE = re.compile(r"\bERR_[A-Z]+_\d{3}\b", re.IGNORECASE)
COMPONENT_PREFIXES = ("CEL", "INV", "PCB", "FUS", "BMS", "CON", "RES", "MOD", "SEN", "FLT", "HTR", "GSK", "SRV", "CHG")
COMPONENT_RE = re.compile(r"\b(?:%s)-[A-Z0-9]+(?:-[A-Z0-9]+)?\b" % "|".join(COMPONENT_PREFIXES), re.IGNORECASE)
MODEL_RE = re.compile(r"\b(?:model\s+)?([XCG])\s?-?\s?(3000|5000|7000|9000|12000|1200|3500)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20[12]\d)\b")

MODEL_ALIASES = {"megapack": "X-7000", "powerstation": "G-3500"}

# keyword -> telemetry component-code prefix (links plain-English parts to telemetry flags)
COMPONENT_KEYWORDS = {
    "cell": "CEL-", "cells": "CEL-", "lithium": "CEL-", "inverter": "INV-", "thyristor": "INV-",
    "gate driver": "PCB-", "gate drive": "PCB-", "fuse": "FUS-", "fuses": "FUS-", "bms": "BMS-",
    "contactor": "CON-", "pre-charge": "RES-", "precharge": "RES-", "module": "MOD-", "sensor": "SEN-",
    "filter": "FLT-", "heater": "HTR-", "gasket": "GSK-", "seal": "GSK-", "charger": "CHG-",
}

INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "unit_history": ("history", "service", "dispatch", "maintenance", "repair", "technician", "visit", "serviced"),
    "warranty": ("warranty", "claim", "claims", "rma", "denied", "approved"),
    "failure_analysis": (
        "fail", "failure", "failures", "fault", "faults", "error", "alarm", "trip", "root cause", "cause",
        "caused", "why", "thermal runaway", "overheat", "high-temperature", "high temperature", "hot",
        "degradation", "incident", "problem", "issue", "flashover", "failure mode", "failure modes",
    ),
    "supply_chain": (
        "supplier", "suppliers", "vendor", "vendors", "sourcing", "single-source", "single source", "tier",
        "procure", "procurement", "manufacturer", "manufacturers", "supply chain", "second source", "supplies",
        "supplied by", "who makes",
    ),
    "procedure_lookup": (
        "how do", "how to", "procedure", "sop", "steps", "step", "process", "specification", "spec", "datasheet",
        "manual", "torque", "calibrat", "emergency", "safety", "decommission", "commissioning", "firmware",
        "requirement", "required", "evidence", "limit", "cutoff", "resistance",
    ),
    "telemetry": (
        "telemetry", "sensor", "reading", "readings", "voltage", "temperature", "soc", "state of charge",
        "log", "logs", "event", "events", "bms", "heartbeat",
    ),
}
HIGH_TEMP_KEYWORDS = ("high-temperature", "high temperature", "hot", "heat", "thermal", "overheat", "temperature")

ALL_VECTOR_DOC_TYPES = (
    "manual", "standard_operating_procedure", "engineering_change_order",
    "specification", "incident_report", "supplier_report",
)


@dataclass
class Entities:
    serial_numbers: list[str] = field(default_factory=list)
    equipment_ids: list[str] = field(default_factory=list)
    dispatch_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    part_ids: list[str] = field(default_factory=list)
    fault_codes: list[str] = field(default_factory=list)
    component_codes: list[str] = field(default_factory=list)
    component_prefixes: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    high_temperature: bool = False

    @property
    def unit_ids(self) -> list[str]:
        return self.serial_numbers + self.equipment_ids

    def any(self) -> bool:
        return any(asdict(self).values())


@dataclass
class RetrievalPlan:
    question: str
    intents: list[str]
    entities: Entities
    use_sql: bool
    use_vector: bool
    use_document: bool
    sql_tasks: list[str]
    mongo_tasks: list[str]
    vector_query: str
    vector_doc_types: list[str] | None
    multi_hop: bool
    rationale: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entities"] = asdict(self.entities)
        return data


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _has(text: str, keywords: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(k), text) for k in keywords)


def extract_entities(question: str) -> Entities:
    text = question.strip()
    lowered = text.lower()
    serials = [s.upper() for s in SERIAL_RE.findall(text)]
    models = [f"{m[0].upper()}-{m[1]}" for m in MODEL_RE.findall(text)]
    for alias, model in MODEL_ALIASES.items():
        if alias in lowered:
            models.append(model)
    prefixes = [prefix for keyword, prefix in COMPONENT_KEYWORDS.items() if re.search(r"\b" + re.escape(keyword) + r"\b", lowered)]
    return Entities(
        serial_numbers=_unique(serials),
        equipment_ids=_unique([e.upper() for e in EQUIPMENT_RE.findall(text)]),
        dispatch_ids=_unique([d.upper() for d in DISPATCH_RE.findall(text)]),
        claim_ids=_unique([c.upper() for c in CLAIM_RE.findall(text)]),
        part_ids=_unique([p.upper() for p in PART_RE.findall(text)]),
        fault_codes=_unique([f.upper() for f in FAULT_RE.findall(text)]),
        component_codes=_unique([c.upper() for c in COMPONENT_RE.findall(text)]),
        component_prefixes=_unique(prefixes),
        models=_unique(models),
        years=sorted({int(y) for y in YEAR_RE.findall(text)}),
        high_temperature=_has(lowered, HIGH_TEMP_KEYWORDS),
    )


def classify_intents(question: str, entities: Entities) -> list[str]:
    lowered = question.lower()
    intents = [name for name, words in INTENT_KEYWORDS.items() if _has(lowered, words)]
    if entities.unit_ids or entities.dispatch_ids or entities.claim_ids:
        intents.append("unit_history")
    if entities.fault_codes:
        intents.append("failure_analysis")
    if entities.component_codes and "supply_chain" not in intents and _has(lowered, ("who", "which", "source")):
        intents.append("supply_chain")
    if not intents:
        intents.append("general")
    order = ["unit_history", "warranty", "failure_analysis", "supply_chain", "telemetry", "procedure_lookup", "general"]
    return sorted(set(intents), key=order.index)


def plan_retrieval(question: str) -> RetrievalPlan:
    entities = extract_entities(question)
    intents = classify_intents(question, entities)
    rationale: list[str] = []
    sql_tasks: list[str] = []
    mongo_tasks: list[str] = []
    doc_types: list[str] | None = None
    multi_hop = False

    # ---------- SQL (MariaDB) ----------
    if entities.unit_ids:
        sql_tasks.append("unit_lookup")
        rationale.append(f"SQL: exact equipment/serial lookup for {', '.join(entities.unit_ids)} "
                         "(equipment metadata, warranty status, service history, warranty claims).")
    if entities.dispatch_ids or entities.claim_ids:
        sql_tasks.append("record_lookup")
        rationale.append("SQL: direct lookup of the dispatch/claim IDs named in the question.")
    if entities.models and ({"failure_analysis", "warranty", "unit_history", "telemetry"} & set(intents)):
        sql_tasks.extend(["model_fleet_summary", "model_service_summary"])
        if "warranty" in intents or "failure_analysis" in intents:
            sql_tasks.append("model_warranty_summary")
        rationale.append(f"SQL: fleet, field-service and warranty aggregates filtered by model {', '.join(entities.models)}.")
    if "supply_chain" in intents or entities.part_ids:
        sql_tasks.append("parts_suppliers")
        rationale.append("SQL: parts catalog -> supplier/manufacturer lookup from ERP parts_inventory.")
    if "warranty" in intents and not entities.unit_ids and not entities.models:
        sql_tasks.append("warranty_overview")
        rationale.append("SQL: warranty claim status/denial-reason overview.")

    # ---------- Document store (MongoDB telemetry) ----------
    if entities.serial_numbers:
        mongo_tasks.append("unit_events")
        rationale.append(f"MongoDB: telemetry timeline + fault events for {', '.join(entities.serial_numbers)}.")
    if entities.fault_codes:
        mongo_tasks.append("fault_code_events")
        rationale.append(f"MongoDB: fleet-wide events for error code(s) {', '.join(entities.fault_codes)}.")
    if entities.models and ({"failure_analysis", "telemetry", "warranty"} & set(intents)):
        mongo_tasks.append("model_fault_summary")
        hot = " incl. high-temperature event counts" if entities.high_temperature else ""
        rationale.append(f"MongoDB: fault-code aggregation for model {', '.join(entities.models)}{hot}.")
    if entities.component_codes or ("supply_chain" in intents and entities.component_prefixes):
        mongo_tasks.append("component_events")
        rationale.append("MongoDB: telemetry events that flagged the components in question "
                         "(links supplier parts to field failures).")
    if not mongo_tasks and "telemetry" in intents and "procedure_lookup" not in intents:
        mongo_tasks.append("text_search")
        rationale.append("MongoDB: full-text search over telemetry notes (no unit/model/code named).")

    # ---------- Vector (ChromaDB) ----------
    use_vector = True  # cheap (~20 ms) and almost always adds documentary grounding
    expansions = entities.models + entities.fault_codes + entities.component_codes + entities.serial_numbers
    vector_query = question if not expansions else f"{question} ({' '.join(_unique(expansions))})"
    if "supply_chain" in intents and not ({"failure_analysis", "procedure_lookup"} & set(intents)):
        doc_types = ["supplier_report", "specification", "incident_report"]
        rationale.append("Vector: semantic search restricted to supplier reports, datasheets and incident reports.")
    else:
        rationale.append("Vector: semantic search over all 25 technical PDFs (manuals, SOPs, ECOs, incidents, specs).")
    if "failure_analysis" in intents or entities.serial_numbers:
        multi_hop = True
        rationale.append("Multi-hop: fault codes / components found in telemetry feed a second vector search.")

    use_sql = bool(sql_tasks)
    use_document = bool(mongo_tasks)
    if not use_sql:
        rationale.append("SQL skipped: no equipment ID, model, warranty or supplier signal in the question.")
    if not use_document:
        rationale.append("MongoDB skipped: no unit, model, error code or telemetry signal in the question.")

    return RetrievalPlan(
        question=question,
        intents=intents,
        entities=entities,
        use_sql=use_sql,
        use_vector=use_vector,
        use_document=use_document,
        sql_tasks=_unique(sql_tasks),
        mongo_tasks=_unique(mongo_tasks),
        vector_query=vector_query,
        vector_doc_types=doc_types,
        multi_hop=multi_hop,
        rationale=rationale,
    )

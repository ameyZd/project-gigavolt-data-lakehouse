"""MongoDB retrieval: IoT/BMS telemetry patterns from ``gigavolt_iot.bms_telemetry``.

Each task is a single indexed find/aggregate:
* unit_events          -> ix_equipment_timestamp   (unit timeline, fault groups, precursor window)
* model_fault_summary  -> ix_model_fault_timestamp (fault codes per model, hot-event counts)
* fault_code_events    -> ix_fault_code_timestamp  (fleet-wide spread of an error code)
* component_events     -> ix_flagged_components    (multikey: which faults flagged a component)
* text_search          -> ix_notes_text            (free-text over technician/BMS notes)
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..router import RetrievalPlan
from .base import ContextItem, SourceResult

DB_LABEL = "MongoDB gigavolt_iot"
SEVERITY_NAMES = ["Info", "Warning", "Moderate", "High", "Critical", "Fatal"]
MAX_STORED_IDS = 60


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def severity_name(rank: Any) -> str:
    try:
        return SEVERITY_NAMES[int(rank)]
    except (TypeError, ValueError, IndexError):
        return str(rank)


def flatten(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in value if isinstance(value, list) else [value]:
            if item and item not in out:
                out.append(item)
    return sorted(out)


class DocumentSource:
    source_type = "document"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._lock = threading.Lock()

    @property
    def collection(self):
        with self._lock:
            if self._client is None:
                try:
                    from pymongo import MongoClient
                except ImportError as error:  # pragma: no cover
                    raise RuntimeError("pymongo missing: pip install -r requirements.txt") from error
                self._client = MongoClient(self.settings.mongo_uri, serverSelectionTimeoutMS=2500,
                                           tz_aware=True, appname="gigavolt-rag", maxPoolSize=10)
        return self._client[self.settings.mongo_db][self.settings.mongo_collection]

    def warmup(self) -> None:
        self.collection.database.client.admin.command("ping")
        self.collection.find_one({}, {"_id": 1})

    def health(self) -> dict[str, Any]:
        collection = self.collection
        return {
            "documents": collection.count_documents({}),
            "fault_events": collection.count_documents({"is_fault": True}),
            "units": len(collection.distinct("equipment_id")),
            "indexes": [index["name"] for index in collection.list_indexes()],
        }

    def _item(self, record_ids: list[str], title: str, text: str, verify: dict[str, Any],
              kind: str = "record", timestamp: str | None = None) -> ContextItem:
        return ContextItem(
            source_type="document",
            database=DB_LABEL,
            collection=self.settings.mongo_collection,
            record_ids=[str(r) for r in record_ids][:MAX_STORED_IDS],
            title=title,
            text=text,
            kind=kind,
            timestamp=timestamp,
            verify={"engine": "mongodb", **verify},
        )

    def _absence(self, key: str, text: str, verify: dict[str, Any]) -> ContextItem:
        return self._item([f"lookup:{key}"], f"No telemetry for {key}", text, verify, kind="absence")

    # ------------------------------------------------------------------ tasks
    def unit_events(self, serial: str) -> list[ContextItem]:
        coll = self.collection
        status_pipeline = [
            {"$match": {"equipment_id": serial}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}, "first": {"$min": "$timestamp"},
                        "last": {"$max": "$timestamp"}, "max_temp": {"$max": "$telemetry.temperature_c"},
                        "max_delta": {"$max": "$telemetry.voltage_delta_mv"}, "model": {"$first": "$model"},
                        "ids": {"$push": "$_id"}}},
            {"$sort": {"n": -1}},
        ]
        groups = list(coll.aggregate(status_pipeline))
        if not groups:
            return [self._absence(serial, f"Telemetry lookup result: NO BMS telemetry events exist for equipment_id "
                                          f"'{serial}' in MongoDB.", {"op": "aggregate", "pipeline": status_pipeline})]
        total = sum(g["n"] for g in groups)
        first = min(g["first"] for g in groups)
        last = max(g["last"] for g in groups)
        all_ids = sorted(i for g in groups for i in g["ids"])
        model = next((g["model"] for g in groups if g.get("model")), "unknown model")
        fault_total = sum(g["n"] for g in groups if g["_id"] in ("WARNING", "FAULT", "CRITICAL_FAULT"))
        items = [self._item(
            all_ids, f"Telemetry summary {serial}",
            f"Telemetry summary for {serial} (model {model}): {total} BMS events from {iso(first)} to {iso(last)}. "
            f"Status counts: " + ", ".join(f"{g['_id']} {g['n']}" for g in groups) + ". "
            f"Warning/fault events: {fault_total}. Max temperature {max(g['max_temp'] or 0 for g in groups)} °C; "
            f"max cell voltage delta {max(g['max_delta'] or 0 for g in groups)} mV.",
            {"op": "aggregate", "pipeline": status_pipeline}, kind="aggregate", timestamp=f"{iso(first)}..{iso(last)}")]

        fault_pipeline = [
            {"$match": {"equipment_id": serial, "is_fault": True}},
            {"$sort": {"timestamp": 1}},
            {"$group": {"_id": "$fault_code", "n": {"$sum": 1}, "first": {"$min": "$timestamp"},
                        "last": {"$max": "$timestamp"}, "max_temp": {"$max": "$telemetry.temperature_c"},
                        "max_delta": {"$max": "$telemetry.voltage_delta_mv"}, "worst": {"$max": "$severity_rank"},
                        "statuses": {"$addToSet": "$status"}, "components": {"$addToSet": "$flagged_components"},
                        "first_note": {"$first": "$notes"}, "ids": {"$push": "$_id"}}},
            {"$sort": {"first": 1}},
        ]
        for group in list(coll.aggregate(fault_pipeline))[: self.settings.mongo_event_limit]:
            components = flatten(group["components"])
            items.append(self._item(
                group["ids"], f"{serial} {group['_id']}",
                f"{serial} reported fault code {group['_id'] or 'none'} {group['n']} time(s) between {iso(group['first'])} "
                f"and {iso(group['last'])}; statuses {', '.join(sorted(group['statuses']))}; worst severity "
                f"{severity_name(group['worst'])}; max temperature {group['max_temp']} °C; max voltage delta "
                f"{group['max_delta']} mV; flagged components: {', '.join(components) or 'none'}. "
                f"BMS note on first occurrence: \"{group['first_note']}\".",
                {"op": "aggregate", "pipeline": fault_pipeline}, kind="aggregate",
                timestamp=f"{iso(group['first'])}..{iso(group['last'])}"))

        critical = coll.find_one({"equipment_id": serial, "status": "CRITICAL_FAULT"}, sort=[("timestamp", 1)])
        if critical:
            window = list(coll.find(
                {"equipment_id": serial,
                 "timestamp": {"$gte": critical["timestamp"] - timedelta(minutes=10), "$lte": critical["timestamp"]}},
                {"timestamp": 1, "status": 1, "fault_code": 1, "telemetry": 1, "flagged_components": 1, "notes": 1},
            ).sort("timestamp", 1))
            steps = [f"{iso(e['timestamp'])} {e['status']} {e.get('fault_code') or ''} "
                     f"(T={e['telemetry'].get('temperature_c')} °C, V={e['telemetry'].get('voltage_v')} V, "
                     f"I={e['telemetry'].get('current_a')} A, dV={e['telemetry'].get('voltage_delta_mv')} mV"
                     f"{', flagged ' + ','.join(e['flagged_components']) if e.get('flagged_components') else ''}) "
                     f"note: \"{e.get('notes', '')}\"" for e in window]
            items.append(self._item(
                [e["_id"] for e in window], f"{serial} precursor window",
                f"Event sequence for {serial} in the 10 minutes up to its first CRITICAL_FAULT ({critical['_id']}): "
                + " -> ".join(steps),
                {"op": "find", "filter": {"_id": {"$in": [e["_id"] for e in window]}}, "sort": [["timestamp", 1]]},
                timestamp=iso(critical["timestamp"])))

        latest = coll.find_one({"equipment_id": serial}, sort=[("timestamp", -1)])
        if latest:
            t = latest.get("telemetry", {})
            items.append(self._item(
                [latest["_id"]], f"{serial} latest event",
                f"Most recent telemetry for {serial}: {latest['_id']} at {iso(latest['timestamp'])}, status {latest['status']}, "
                f"fault code {latest.get('fault_code') or 'none'}, severity {latest.get('severity')}, voltage {t.get('voltage_v')} V, "
                f"current {t.get('current_a')} A, temperature {t.get('temperature_c')} °C, SoC {t.get('soc_percent')} %, "
                f"note \"{latest.get('notes', '')}\".",
                {"op": "find", "filter": {"_id": latest["_id"]}}, timestamp=iso(latest["timestamp"])))
        return items

    def model_fault_summary(self, model_code: str, hot_c: float) -> list[ContextItem]:
        coll = self.collection
        pipeline = [
            {"$match": {"model_code": model_code, "is_fault": True}},
            {"$group": {"_id": "$fault_code", "n": {"$sum": 1}, "units": {"$addToSet": "$equipment_id"},
                        "max_temp": {"$max": "$telemetry.temperature_c"}, "avg_temp": {"$avg": "$telemetry.temperature_c"},
                        "hot": {"$sum": {"$cond": [{"$gte": ["$telemetry.temperature_c", hot_c]}, 1, 0]}},
                        "worst": {"$max": "$severity_rank"}, "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                        "components": {"$addToSet": "$flagged_components"}, "notes": {"$addToSet": "$notes"},
                        "ids": {"$push": "$_id"}}},
            {"$sort": {"n": -1}},
            {"$limit": self.settings.mongo_event_limit + 2},
        ]
        groups = list(coll.aggregate(pipeline))
        total = coll.count_documents({"model_code": model_code})
        if not total:
            return [self._absence(f"model={model_code}",
                                  f"Telemetry lookup result: NO BMS telemetry exists for model {model_code} in MongoDB "
                                  f"(telemetry covers models {', '.join(sorted(m for m in coll.distinct('model_code') if m))}).",
                                  {"op": "count", "filter": {"model_code": model_code}})]
        fault_events = sum(g["n"] for g in groups)
        hot_events = sum(g["hot"] for g in groups)
        items = [self._item(
            [f"agg:model_code={model_code}"], f"{model_code} telemetry overview",
            f"Telemetry overview for model {model_code}: {total} BMS events, {fault_events} of them warning/fault events "
            f"across {len({u for g in groups for u in g['units']})} unit(s); {hot_events} fault events occurred at or above "
            f"{hot_c:g} °C. Distinct fault codes: {', '.join(str(g['_id']) for g in groups)}.",
            {"op": "aggregate", "pipeline": pipeline}, kind="aggregate")]
        for g in groups[: self.settings.mongo_event_limit]:
            items.append(self._item(
                g["ids"], f"{model_code} {g['_id']}",
                f"Model {model_code} fault code {g['_id']}: {g['n']} event(s) on unit(s) {', '.join(sorted(g['units']))} between "
                f"{iso(g['first'])} and {iso(g['last'])}; worst severity {severity_name(g['worst'])}; max temperature "
                f"{g['max_temp']} °C (average {round(g['avg_temp'] or 0, 1)} °C); {g['hot']} of these events at or above "
                f"{hot_c:g} °C; flagged components: {', '.join(flatten(g['components'])) or 'none'}. "
                f"BMS notes: " + " | ".join(f'"{n}"' for n in sorted(g["notes"])[:3]) + ".",
                {"op": "aggregate", "pipeline": pipeline}, kind="aggregate",
                timestamp=f"{iso(g['first'])}..{iso(g['last'])}"))
        return items

    def fault_code_events(self, code: str) -> list[ContextItem]:
        pipeline = [
            {"$match": {"fault_code": code}},
            {"$group": {"_id": "$equipment_id", "model": {"$first": "$model"}, "n": {"$sum": 1},
                        "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                        "max_temp": {"$max": "$telemetry.temperature_c"}, "worst": {"$max": "$severity_rank"},
                        "components": {"$addToSet": "$flagged_components"}, "notes": {"$addToSet": "$notes"},
                        "ids": {"$push": "$_id"}}},
            {"$sort": {"n": -1}},
        ]
        groups = list(self.collection.aggregate(pipeline))
        if not groups:
            return [self._absence(code, f"Telemetry lookup result: NO events with fault_code {code} exist in MongoDB.",
                                  {"op": "aggregate", "pipeline": pipeline})]
        return [self._item(
            g["ids"], f"{code} on {g['_id']}",
            f"Fault code {code} on unit {g['_id']} ({g['model']}): {g['n']} event(s) between {iso(g['first'])} and "
            f"{iso(g['last'])}; worst severity {severity_name(g['worst'])}; max temperature {g['max_temp']} °C; flagged "
            f"components: {', '.join(flatten(g['components'])) or 'none'}; notes: "
            + " | ".join(f'"{n}"' for n in sorted(g["notes"])[:2]) + ".",
            {"op": "aggregate", "pipeline": pipeline}, kind="aggregate",
            timestamp=f"{iso(g['first'])}..{iso(g['last'])}") for g in groups[: self.settings.mongo_event_limit]]

    def component_events(self, codes: list[str], prefixes: list[str]) -> list[ContextItem]:
        terms: list[Any] = list(codes) + [{"$regex": f"^{p}"} for p in prefixes]
        if not terms:
            return []
        or_filter = [{"flagged_components": t} for t in terms]
        pipeline = [
            {"$match": {"$or": or_filter}},
            {"$unwind": "$flagged_components"},
            {"$match": {"$or": or_filter}},
            {"$group": {"_id": "$flagged_components", "n": {"$sum": 1}, "fault_codes": {"$addToSet": "$fault_code"},
                        "units": {"$addToSet": "$equipment_id"}, "models": {"$addToSet": "$model"},
                        "worst": {"$max": "$severity_rank"}, "max_temp": {"$max": "$telemetry.temperature_c"},
                        "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                        "notes": {"$addToSet": "$notes"}, "ids": {"$push": "$_id"}}},
            {"$sort": {"worst": -1, "n": -1}},
        ]
        groups = list(self.collection.aggregate(pipeline))
        label = ", ".join(codes + [p + "*" for p in prefixes])
        if not groups:
            return [self._absence(label, f"Telemetry lookup result: NO events flagged components matching {label}.",
                                  {"op": "aggregate", "pipeline": pipeline})]
        return [self._item(
            g["ids"], f"Component {g['_id']} in telemetry",
            f"Component {g['_id']} was flagged in {g['n']} telemetry event(s) on unit(s) {', '.join(sorted(g['units']))} "
            f"({', '.join(sorted(m for m in g['models'] if m))}) between {iso(g['first'])} and {iso(g['last'])}; fault codes "
            f"{', '.join(sorted(c for c in g['fault_codes'] if c)) or 'none'}; worst severity {severity_name(g['worst'])}; "
            f"max temperature {g['max_temp']} °C; notes: " + " | ".join(f'"{n}"' for n in sorted(g["notes"])[:2]) + ".",
            {"op": "aggregate", "pipeline": pipeline}, kind="aggregate",
            timestamp=f"{iso(g['first'])}..{iso(g['last'])}") for g in groups[: self.settings.mongo_event_limit]]

    def text_search(self, question: str) -> list[ContextItem]:
        cursor = (self.collection.find({"$text": {"$search": question}}, {"score": {"$meta": "textScore"}, "telemetry": 1,
                                                                          "equipment_id": 1, "timestamp": 1, "status": 1,
                                                                          "fault_code": 1, "notes": 1, "model": 1})
                  .sort([("score", {"$meta": "textScore"})]).limit(5))
        items = []
        for event in cursor:
            items.append(self._item(
                [event["_id"]], f"Telemetry note {event['_id']}",
                f"Telemetry event {event['_id']} on {event['equipment_id']} ({event.get('model')}) at {iso(event['timestamp'])}: "
                f"status {event['status']}, fault code {event.get('fault_code') or 'none'}, temperature "
                f"{event['telemetry'].get('temperature_c')} °C, note \"{event.get('notes', '')}\".",
                {"op": "find", "filter": {"_id": event["_id"]}}, timestamp=iso(event["timestamp"])))
        return items

    # ------------------------------------------------------------------ entry point
    def retrieve(self, plan: RetrievalPlan) -> SourceResult:
        started = time.perf_counter()
        result = SourceResult(source_type="document")
        task_ms: dict[str, float] = {}
        errors: list[str] = []
        for task in plan.mongo_tasks:
            task_started = time.perf_counter()
            try:
                if task == "unit_events":
                    for serial in plan.entities.serial_numbers[:3]:
                        result.items.extend(self.unit_events(serial))
                elif task == "model_fault_summary":
                    hot = self.settings.high_temp_threshold_c
                    for model in plan.entities.models[:2]:
                        result.items.extend(self.model_fault_summary(model, hot))
                elif task == "fault_code_events":
                    for code in plan.entities.fault_codes[:3]:
                        result.items.extend(self.fault_code_events(code))
                elif task == "component_events":
                    result.items.extend(self.component_events(plan.entities.component_codes,
                                                              plan.entities.component_prefixes))
                elif task == "text_search":
                    result.items.extend(self.text_search(plan.question))
            except Exception as error:
                errors.append(f"{task}: {error.__class__.__name__}: {error}")
            task_ms[task] = round((time.perf_counter() - task_started) * 1000, 3)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        result.details = {"task_ms": task_ms, "tasks": plan.mongo_tasks}
        if errors:
            result.details["errors"] = errors
            if not result.items:
                result.error = "; ".join(errors)
        return result

    # ------------------------------------------------------------------ multi-hop helper
    @staticmethod
    def expansion_terms(items: list[ContextItem]) -> list[str]:
        """Fault codes and component codes mentioned in telemetry evidence (feeds 2nd vector hop)."""
        import re

        terms: list[str] = []
        for item in items:
            for match in re.findall(r"\b(ERR_[A-Z]+_\d{3}|[A-Z]{3}-[A-Z0-9]+-[A-Z0-9]+)\b", item.text):
                if match not in terms:
                    terms.append(match)
        return terms[:8]

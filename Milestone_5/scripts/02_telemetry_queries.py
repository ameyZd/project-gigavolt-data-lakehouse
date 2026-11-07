#!/usr/bin/env python3
"""Diagnostic MongoDB queries for battery failures, each with index-usage evidence.

For every query the script prints: the result, elapsed time, the index the query planner
chose (``IXSCAN ix_...``) and documents examined vs. returned. This is the evidence that the
document store is indexed by timestamp, equipment_id and error (fault) code.

Usage:  python scripts/02_telemetry_queries.py [--unit SN-X7-0094] [--fault ERR_INV_404]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import Settings  # noqa: E402


def find_keys(node: Any, key: str) -> list[Any]:
    """Recursively collect values for ``key`` anywhere inside an explain() document."""
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(find_keys(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(find_keys(item, key))
    return found


def plan_summary(explain: dict[str, Any]) -> str:
    indexes = sorted({str(name) for name in find_keys(explain, "indexName")})
    stages = find_keys(explain, "stage")
    examined = find_keys(explain, "totalDocsExamined")
    keys = find_keys(explain, "totalKeysExamined")
    returned = find_keys(explain, "nReturned")
    access = "IXSCAN " + ", ".join(indexes) if indexes else ("COLLSCAN" if "COLLSCAN" in stages else "n/a")
    detail = []
    if keys:
        detail.append(f"keys examined={keys[0]}")
    if examined:
        detail.append(f"docs examined={examined[0]}")
    if returned:
        detail.append(f"returned={returned[0]}")
    return f"{access}" + (f" ({', '.join(detail)})" if detail else "")


def fmt_ts(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else str(value)


def header(number: int, title: str, purpose: str) -> None:
    print("\n" + "=" * 96)
    print(f"Q{number}. {title}")
    print(f"    Purpose: {purpose}")
    print("-" * 96)


def run_find(collection, number, title, purpose, filter_, sort, projection, limit=10) -> None:
    header(number, title, purpose)
    print(f"    filter={filter_}  sort={sort}")
    started = time.perf_counter()
    rows = list(collection.find(filter_, projection).sort(sort).limit(limit))
    elapsed = (time.perf_counter() - started) * 1000
    for row in rows:
        t = row.get("telemetry", {})
        print(f"    {row['_id']}  {fmt_ts(row['timestamp'])}  {row['equipment_id']:<12} {row['status']:<15}"
              f" {str(row.get('fault_code') or '-'):<13} {row.get('severity', ''):<9}"
              f" T={t.get('temperature_c')}°C dV={t.get('voltage_delta_mv')}mV"
              f" {','.join(row.get('flagged_components') or []) or '-'}")
    explain = collection.find(filter_, projection).sort(sort).limit(limit).explain()
    print(f"    -> {len(rows)} rows in {elapsed:.2f} ms | plan: {plan_summary(explain)}")


def run_aggregate(db, collection, number, title, purpose, pipeline, printer) -> None:
    header(number, title, purpose)
    started = time.perf_counter()
    rows = list(collection.aggregate(pipeline))
    elapsed = (time.perf_counter() - started) * 1000
    for row in rows:
        printer(row)
    explain = db.command("explain", {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}},
                         verbosity="executionStats")
    print(f"    -> {len(rows)} groups in {elapsed:.2f} ms | plan: {plan_summary(explain)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unit", default="SN-X7-0094")
    parser.add_argument("--fault", default="ERR_INV_404")
    parser.add_argument("--model", default="X-7000")
    parser.add_argument("--hot", type=float, default=60.0, help="temperature threshold in °C")
    args = parser.parse_args()

    from pymongo import MongoClient

    settings = Settings.from_env()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    db = client[settings.mongo_db]
    collection = db[settings.mongo_collection]
    print(f"MongoDB {client.server_info()['version']} | {settings.mongo_db}.{settings.mongo_collection} | "
          f"{collection.count_documents({})} documents")
    projection = {"timestamp": 1, "equipment_id": 1, "status": 1, "fault_code": 1, "severity": 1,
                  "telemetry": 1, "flagged_components": 1}

    run_find(collection, 1, f"Unit timeline for {args.unit}",
             "Everything one battery reported, newest first (equipment_id + timestamp index).",
             {"equipment_id": args.unit}, [("timestamp", -1)], projection, limit=8)

    run_find(collection, 2, f"Error-code lookup: {args.fault}",
             "Which units threw this fault code and when (fault_code + timestamp index).",
             {"fault_code": args.fault}, [("timestamp", -1)], projection, limit=8)

    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    run_find(collection, 3, "Time-window scan: faults between 2026-09-01 and 2026-09-03",
             "Incident-window triage across the whole fleet (timestamp index).",
             {"timestamp": {"$gte": start, "$lt": start + timedelta(days=2)}, "is_fault": True},
             [("timestamp", 1)], projection, limit=10)

    def print_fault_group(row):
        print(f"    {row['_id']:<13} events={row['events']:<3} units={','.join(sorted(row['units']))}"
              f"  maxT={row['max_temp']}°C  worst={row['worst_severity']}"
              f"  {fmt_ts(row['first'])} -> {fmt_ts(row['last'])}  components={','.join(sorted(row['components'])) or '-'}")

    run_aggregate(db, collection, 4, f"Battery-failure diagnostics for model {args.model}",
                  "Group fault events by error code for one model (model_code + is_fault index).",
                  [
                      {"$match": {"model_code": args.model, "is_fault": True}},
                      {"$unwind": {"path": "$flagged_components", "preserveNullAndEmptyArrays": True}},
                      {"$group": {
                          "_id": "$fault_code",
                          "events_ids": {"$addToSet": "$_id"},
                          "units": {"$addToSet": "$equipment_id"},
                          "max_temp": {"$max": "$telemetry.temperature_c"},
                          "worst_rank": {"$max": "$severity_rank"},
                          "first": {"$min": "$timestamp"},
                          "last": {"$max": "$timestamp"},
                          "components": {"$addToSet": "$flagged_components"},
                      }},
                      {"$addFields": {
                          "events": {"$size": "$events_ids"},
                          "worst_severity": {"$arrayElemAt": [
                              ["Info", "Warning", "Moderate", "High", "Critical", "Fatal"], "$worst_rank"]},
                      }},
                      {"$sort": {"events": -1}},
                  ], print_fault_group)

    run_find(collection, 5, f"Thermal stress: readings at or above {args.hot}°C",
             "Hot-running packs across the fleet (index on nested telemetry.temperature_c).",
             {"telemetry.temperature_c": {"$gte": args.hot}}, [("telemetry.temperature_c", -1)], projection, limit=8)

    run_find(collection, 6, "Cell-level component alarms (flagged_components starts with CEL-)",
             "Links field faults to supplier-sourced cells (multikey index on an array field).",
             {"flagged_components": {"$regex": "^CEL-"}}, [("flagged_components", 1)], projection, limit=8)

    header(7, f"Precursor window before each CRITICAL_FAULT on {args.unit}",
           "What the pack reported in the 10 minutes before failure (root-cause context).")
    started = time.perf_counter()
    criticals = list(collection.find({"equipment_id": args.unit, "status": "CRITICAL_FAULT"}).sort("timestamp", 1))
    for event in criticals[:3]:
        window = list(collection.find(
            {"equipment_id": args.unit,
             "timestamp": {"$gte": event["timestamp"] - timedelta(minutes=10), "$lte": event["timestamp"]}},
            projection).sort("timestamp", 1))
        print(f"    CRITICAL {event['_id']} at {fmt_ts(event['timestamp'])}:")
        for row in window:
            print(f"       {row['_id']} {fmt_ts(row['timestamp'])} {row['status']:<15} {row.get('fault_code') or '-':<12}"
                  f" T={row['telemetry'].get('temperature_c')}°C  {','.join(row.get('flagged_components') or []) or '-'}")
    if not criticals:
        print("    (no CRITICAL_FAULT events for this unit)")
    elapsed = (time.perf_counter() - started) * 1000
    explain = collection.find({"equipment_id": args.unit, "timestamp": {"$lte": datetime.now(timezone.utc)}}).explain()
    print(f"    -> {len(criticals)} critical events in {elapsed:.2f} ms | plan: {plan_summary(explain)}")


if __name__ == "__main__":
    main()

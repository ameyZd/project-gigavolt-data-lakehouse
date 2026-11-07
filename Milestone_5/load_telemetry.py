"""Load Gigavolt BMS telemetry (newline-delimited JSON) into MongoDB and index it.

Usage:  python load_telemetry.py [data/telemetry_data.json]

Schema (one document per log entry, nested structure kept as-is):
  _id                 log_id, e.g. "TLM-90004" (natural key -> idempotent reloads)
  timestamp           real BSON date (string in the source)
  equipment_id        unit serial, e.g. "SN-X7-0094"
  model / model_code  "Gigavolt Megapack X-7000" / "X-7000" (short code matches the ERP)
  status, fault_code, severity
  telemetry {voltage_v, current_a, temperature_c, soc_percent, voltage_delta_mv}   <- nested document
  flagged_components  ["INV-SCR-9901", ...]                                        <- array (multikey index)
  notes
  source {file, line}                                                              <- provenance
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

INDEXES = [
    ([("timestamp", DESCENDING)], "ix_timestamp"),
    ([("equipment_id", ASCENDING), ("timestamp", DESCENDING)], "ix_equipment_time"),
    ([("fault_code", ASCENDING), ("timestamp", DESCENDING)], "ix_fault_time"),
    ([("model_code", ASCENDING), ("severity", ASCENDING), ("timestamp", DESCENDING)], "ix_model_severity_time"),
    ([("flagged_components", ASCENDING)], "ix_components"),
    ([("telemetry.temperature_c", DESCENDING)], "ix_temperature"),
]


def model_code(model: str) -> str:
    m = re.search(r"([A-Z])-?(\d{3,5})", model or "")
    return f"{m.group(1)}-{m.group(2)}" if m else (model or "")


def to_doc(raw: dict, file_name: str, line_no: int) -> dict:
    doc = dict(raw)
    doc["_id"] = raw["log_id"]
    doc["timestamp"] = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
    doc["model_code"] = model_code(raw.get("model"))
    doc["source"] = {"file": file_name, "line": line_no}
    return doc


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "data" / "telemetry_data.json")
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=5000)
    coll = client[os.getenv("MONGO_DB", "gigavolt")][os.getenv("MONGO_COLLECTION", "telemetry")]

    docs, bad = [], 0
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                docs.append(to_doc(json.loads(line), path.name, n))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                bad += 1
                print(f"  skipped line {n}: {exc}")

    result = coll.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs])
    for keys, name in INDEXES:
        coll.create_index(keys, name=name)

    print(f"Loaded {len(docs)} telemetry documents into {coll.full_name} "
          f"(upserted={result.upserted_count}, replaced={result.modified_count}, skipped={bad})")
    print("Indexes:", ", ".join(coll.index_information()))

    # Diagnostic queries that the RAG pipeline relies on -- shown with the index each one uses.
    demos = {
        "Latest faults for unit SN-X7-0094":
            ({"equipment_id": "SN-X7-0094", "fault_code": {"$ne": None}}, [("timestamp", -1)]),
        "All ERR_INV_404 (DC overcurrent) events":
            ({"fault_code": "ERR_INV_404"}, [("timestamp", -1)]),
        "Critical / fatal X-7000 events":
            ({"model_code": "X-7000", "severity": {"$in": ["Critical", "Fatal", "High"]}}, [("timestamp", -1)]),
    }
    for title, (flt, sort) in demos.items():
        t0 = time.perf_counter()
        rows = list(coll.find(flt, {"log_id": 1, "timestamp": 1, "fault_code": 1,
                                    "telemetry.temperature_c": 1}).sort(sort).limit(3))
        ms = (time.perf_counter() - t0) * 1000
        plan = coll.find(flt).sort(sort).explain()["queryPlanner"]["winningPlan"]
        print(f"\n{title}: {coll.count_documents(flt)} matches, {ms:.1f} ms, index={used_index(plan)}")
        for r in rows:
            print(f"   {r['log_id']}  {r['timestamp']:%Y-%m-%d %H:%M}  {r.get('fault_code')}  "
                  f"{r['telemetry']['temperature_c']} C")

    print("\nFault-code summary (aggregation pipeline):")
    for row in coll.aggregate([
        {"$match": {"fault_code": {"$ne": None}}},
        {"$group": {"_id": "$fault_code", "events": {"$sum": 1},
                    "max_temp_c": {"$max": "$telemetry.temperature_c"},
                    "units": {"$addToSet": "$equipment_id"}}},
        {"$sort": {"events": -1}},
    ]):
        print(f"   {row['_id']:<14} events={row['events']:<3} max_temp={row['max_temp_c']:<5} units={sorted(row['units'])}")


def used_index(plan: dict) -> str:
    """Walk the explain() plan tree and return the index name (or COLLSCAN)."""
    if plan.get("stage") == "IXSCAN":
        return plan.get("indexName", "IXSCAN")
    for key in ("inputStage", "queryPlan"):
        if key in plan:
            return used_index(plan[key])
    for child in plan.get("inputStages", []):
        return used_index(child)
    return plan.get("stage", "?")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Load BMS telemetry JSON into MongoDB with schema validation, provenance and indexes.

Pipeline
--------
1. Read ``data/raw/telemetry_data.json`` (JSON Lines or JSON array) - the raw file is never edited.
2. Validate + normalize every record (``gigavolt_rag/telemetry.py``). Bad records go to
   ``data/rejected/telemetry_rejects.jsonl`` instead of silently disappearing.
3. Create/refresh the ``gigavolt_iot.bms_telemetry`` collection with a ``$jsonSchema`` validator.
4. Idempotent bulk upsert keyed on ``_id = log_id`` (re-running never duplicates events).
5. Build indexes on timestamp, equipment_id, fault (error) code, model, severity,
   flagged components (multikey), nested temperature, and a text index on notes.

Usage:  python scripts/01_load_telemetry_mongodb.py [--file PATH] [--drop]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import Settings  # noqa: E402
from gigavolt_rag.telemetry import (  # noqa: E402
    TELEMETRY_INDEXES,
    TELEMETRY_JSON_SCHEMA,
    iter_normalized,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, default=M5_ROOT / "data" / "raw" / "telemetry_data.json")
    parser.add_argument("--drop", action="store_true", help="Drop and rebuild the collection first.")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"Missing {args.file}. Run: bash setup/04_load_data.sh")
    try:
        from pymongo import MongoClient, ReplaceOne
        from pymongo.errors import BulkWriteError, CollectionInvalid
    except ImportError as error:
        raise SystemExit("pymongo missing. Run: pip install -r requirements.txt") from error

    settings = Settings.from_env()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    client.admin.command("ping")
    db = client[settings.mongo_db]
    name = settings.mongo_collection

    if args.drop and name in db.list_collection_names():
        db.drop_collection(name)
        print(f"Dropped {settings.mongo_db}.{name}")
    try:
        db.create_collection(name, validator=TELEMETRY_JSON_SCHEMA, validationLevel="strict", validationAction="error")
        print(f"Created {settings.mongo_db}.{name} with $jsonSchema validator")
    except CollectionInvalid:
        db.command("collMod", name, validator=TELEMETRY_JSON_SCHEMA, validationLevel="strict", validationAction="error")
        print(f"Collection exists; validator refreshed on {settings.mongo_db}.{name}")
    collection = db[name]

    started = time.perf_counter()
    ingested_at = datetime.now(timezone.utc)
    documents, rejects, warning_counter = [], [], Counter()
    for kind, payload in iter_normalized(args.file, ingested_at=ingested_at):
        if kind == "reject":
            rejects.append(payload)
            continue
        document, warnings = payload
        documents.append(document)
        warning_counter.update(warnings)

    records_read = len(documents) + len(rejects)
    seen, duplicates = set(), 0
    for document in documents:
        if document["_id"] in seen:
            duplicates += 1
        seen.add(document["_id"])

    upserted = modified = 0
    for start in range(0, len(documents), args.batch_size):
        batch = documents[start : start + args.batch_size]
        operations = [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in batch]
        try:
            result = collection.bulk_write(operations, ordered=False)
        except BulkWriteError as error:
            details = error.details
            for write_error in details.get("writeErrors", []):
                rejects.append({"line": None, "reason": f"MongoDB validation: {write_error.get('errmsg')}",
                                "raw": str(write_error.get("op", {}).get("_id"))})
            upserted += details.get("nUpserted", 0)
            modified += details.get("nModified", 0)
            continue
        upserted += result.upserted_count
        modified += result.modified_count

    reject_dir = M5_ROOT / "data" / "rejected"
    reject_dir.mkdir(parents=True, exist_ok=True)
    reject_file = reject_dir / "telemetry_rejects.jsonl"
    reject_file.write_text("".join(json.dumps(item) + "\n" for item in rejects), encoding="utf-8")

    for index_name, keys, options in TELEMETRY_INDEXES:
        collection.create_index(keys, name=index_name, **options)
    load_seconds = time.perf_counter() - started

    total = collection.count_documents({})
    faults = collection.count_documents({"is_fault": True})
    units = collection.distinct("equipment_id")
    first = collection.find_one(sort=[("timestamp", 1)], projection={"timestamp": 1})
    last = collection.find_one(sort=[("timestamp", -1)], projection={"timestamp": 1})
    try:
        stats = db.command("collStats", name)
    except Exception:  # collStats is deprecated in newer MongoDB releases; use the aggregation stage instead
        stats = next(collection.aggregate([{"$collStats": {"storageStats": {}}}]), {}).get("storageStats", {})

    print("\n=== Telemetry load summary ===")
    print(f"Source file           : {args.file.name}")
    print(f"Records read          : {records_read}")
    print(f"Valid documents       : {len(documents)}  (duplicate log_ids in file: {duplicates})")
    print(f"Inserted / updated    : {upserted} / {modified}")
    print(f"Rejected (quarantine) : {len(rejects)}  -> {reject_file.relative_to(M5_ROOT)}")
    if warning_counter:
        print("Warnings              :")
        for warning, count in warning_counter.most_common(10):
            print(f"   {count:>4} x {warning}")
    print(f"Documents in MongoDB  : {total}  (fault/warning events: {faults})")
    print(f"Distinct equipment    : {len(units)}")
    if first and last:
        print(f"Time range (UTC)      : {first['timestamp'].isoformat()}  ->  {last['timestamp'].isoformat()}")
    print(f"Storage size          : {stats.get('storageSize', 0) / 1024:.1f} KiB, "
          f"index size {stats.get('totalIndexSize', 0) / 1024:.1f} KiB")
    print(f"Elapsed               : {load_seconds:.2f} s")
    print("\nIndexes:")
    for index in collection.list_indexes():
        print(f"   {index['name']:<26} {dict(index['key'])}")

    sample = collection.find_one({"is_fault": True}, sort=[("severity_rank", -1), ("timestamp", 1)])
    if sample:
        sample["timestamp"] = sample["timestamp"].isoformat()
        sample["provenance"]["ingested_at"] = sample["provenance"]["ingested_at"].isoformat()
        print("\nSample nested document (highest severity fault):")
        print(json.dumps(sample, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Provenance verification: re-fetch a cited record straight from its source database.

Reads the saved answer (default ``output/last_result.json``), finds the citation tag, and
re-executes the exact query stored with it:
  S# -> the SQL + parameters against MariaDB
  D# -> the find/aggregate against MongoDB
  V# -> collection.get(chunk id) from ChromaDB (prints PDF name + page to open)

Usage:  python scripts/verify_citation.py D2 [S1 V3 ...] [--result output/runs/<file>.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import Settings  # noqa: E402
from gigavolt_rag.sources.base import dumps, jsonable  # noqa: E402


def verify_sql(settings: Settings, spec: dict) -> list[dict]:
    from gigavolt_rag.sources.sql_source import SQLSource

    return SQLSource(settings).query(spec["sql"], tuple(spec.get("params", [])))


def verify_mongo(settings: Settings, spec: dict) -> list[dict]:
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    coll = client[settings.mongo_db][settings.mongo_collection]
    op = spec.get("op")
    if op == "aggregate":
        return list(coll.aggregate(spec["pipeline"]))
    if op == "count":
        return [{"count": coll.count_documents(spec["filter"])}]
    cursor = coll.find(spec["filter"])
    if spec.get("sort"):
        cursor = cursor.sort([(k, v) for k, v in spec["sort"]])
    return list(cursor.limit(50))


def verify_chroma(settings: Settings, spec: dict) -> list[dict]:
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    coll = client.get_collection(settings.chroma_collection, embedding_function=None)
    got = coll.get(ids=spec["ids"], include=["documents", "metadatas"])
    return [{"chunk_id": i, **(m or {}), "text": d} for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tags", nargs="+", help="citation tags such as S1 D2 V3")
    parser.add_argument("--result", type=Path, default=M5_ROOT / "output" / "last_result.json")
    parser.add_argument("--max-chars", type=int, default=1800)
    args = parser.parse_args()

    data = json.loads(args.result.read_text(encoding="utf-8"))
    items = {item["citation_id"]: item for item in data["retrieval"]["items"]}
    settings = Settings.from_env()
    print(f"Answer file: {args.result}\nQuestion   : {data['question']}\n")
    for tag in args.tags:
        tag = tag.strip("[]").upper()
        item = items.get(tag)
        if not item:
            print(f"[{tag}] not found in this answer's context. Available: {', '.join(items)}\n")
            continue
        spec = item["verify"]
        print("=" * 100)
        print(f"[{tag}] {item['title']}")
        print(f"   database   : {item['database']} / {item['collection']}")
        print(f"   record IDs : {', '.join(item['record_ids'][:12])}{' ...' if len(item['record_ids']) > 12 else ''}")
        if item.get("document_name"):
            print(f"   document   : {item['document_name']}  page {item.get('page')}")
        print(f"   timestamp  : {item.get('timestamp')}")
        print(f"   evidence sent to the LLM:\n      {item['text'][:600]}")
        engine = spec.get("engine")
        print(f"\n   re-executing on {engine}: {json.dumps({k: v for k, v in spec.items() if k != 'engine'})[:400]}")
        try:
            rows = {"mariadb": verify_sql, "mongodb": verify_mongo, "chromadb": verify_chroma}[engine](settings, spec)
        except Exception as error:
            print(f"   VERIFY FAILED: {error.__class__.__name__}: {error}\n")
            continue
        text = dumps(jsonable(rows))
        print(f"   live result ({len(rows)} row/doc):")
        print("      " + text[: args.max_chars].replace("\n", "\n      ") + (" ..." if len(text) > args.max_chars else ""))
        real_ids = [rid for rid in item["record_ids"] if not rid.startswith(("lookup:", "agg:"))]
        if item.get("kind") == "absence":
            verdict = "MATCH (absence confirmed)" if not rows else "MISMATCH: rows now exist"
        elif real_ids:
            verdict = f"MATCH: {len([r for r in real_ids if r in text])}/{len(real_ids)} cited record IDs present in live data"
        else:
            verdict = "MATCH (aggregate re-computed)" if rows else "MISMATCH: no rows"
        print(f"\n   VERDICT: {verdict}\n")


if __name__ == "__main__":
    main()

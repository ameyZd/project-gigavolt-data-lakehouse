"""Provenance check: re-fetch cited records straight from their source database.

Usage:  python verify.py SQL2 MDB1 VEC3        # tags from the most recent `python rag.py` run
        python verify.py --run results/runs/<file>.json SQL1
"""
import argparse
import json
import re

from hybrid_retrieval import HERE, DocumentSource, SqlSource, VectorSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--run", help="saved run JSON (default: latest in results/runs)")
    args = ap.parse_args()
    run_file = args.run or max((HERE / "results" / "runs").glob("*.json"))
    run = json.loads(open(run_file).read())
    evidence = {e["tag"]: e for e in run["evidence"]}
    print(f"Run: {run_file}\nQuestion: {run['question']}\n")

    for tag in (t.upper().strip("[]") for t in args.tags):
        ev = evidence.get(tag)
        if not ev:
            print(f"[{tag}] not in this run (available: {', '.join(evidence)})\n")
            continue
        print(f"[{tag}] {ev['database']} | record {ev['record_id']} | {ev['timestamp']}")
        print(f"  claimed in context: {ev['text'][:200]}")
        if ev["source"] == "sql":
            print(f"  live query: {ev['verify']}")
            rows = SqlSource()._q(ev["verify"])
            print(f"  -> {len(rows)} row(s)")
            for r in rows[:3]:
                print("    ", {k: str(v) for k, v in r.items()})
        elif ev["source"] == "document":
            src = DocumentSource()
            ids = re.findall(r"TLM-\d+", ev["record_id"])
            if ids:
                flt = {"_id": {"$in": ids}}
            else:   # an "absence" record: re-run the filter that proved nothing exists
                flt = json.loads(re.sub(r"(\w+):", r'"\1":', re.search(r"find\((\{.*?\})\)", ev["verify"]).group(1)))
            docs = list(src.coll.find(flt, {"source": 0}))
            print(f"  live query: db.{src.coll_name}.find({flt}) -> {len(docs)} document(s)")
            for d in docs[:3]:
                print("    ", json.dumps(d, default=str))
        else:
            got = VectorSource().coll.get(ids=[ev["record_id"]])
            meta, text = got["metadatas"][0], got["documents"][0]
            print(f"  live fetch: {meta['source_file']} page {meta['page_number']} chunk {meta['chunk_index']}")
            print(f"     {text[:400]}")
        print()


if __name__ == "__main__":
    main()

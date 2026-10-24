#!/usr/bin/env python3
"""Measure Chroma HNSW query latency and Recall@k against exact cosine search."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "chroma_db"
DEFAULT_EMBEDDINGS = ROOT / "data" / "processed" / "chunk_embeddings.npz"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "benchmarks"
COLLECTION_NAME = "gigavolt_unstructured_v1"


def percentile(values, percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    lower, upper = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMBEDDINGS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()
    try:
        import chromadb
        import numpy as np
    except ImportError as error:
        raise SystemExit("Install requirements.txt before benchmarking.") from error
    if not args.embeddings.exists():
        raise SystemExit("Missing embeddings. Run 03_generate_embeddings.py first.")

    stored = np.load(args.embeddings)
    vectors = stored["embeddings"].astype("float32")
    chunk_ids = stored["chunk_ids"].tolist()
    client = chromadb.PersistentClient(path=str(args.db_path))
    collection = client.get_collection(COLLECTION_NAME, embedding_function=None)
    if collection.count() != len(vectors):
        raise SystemExit("Database vector count does not match embedding file.")
    query_vectors = [vectors[index % len(vectors)].tolist() for index in range(args.runs + args.warmup)]
    for vector in query_vectors[: args.warmup]:
        collection.query(query_embeddings=[vector], n_results=args.top_k, include=["distances"])

    latencies, recalls = [], []
    for vector in query_vectors[args.warmup :]:
        exact = [chunk_ids[index] for index in np.argsort(-(vectors @ np.asarray(vector)))[: args.top_k]]
        started = time.perf_counter_ns()
        result = collection.query(query_embeddings=[vector], n_results=args.top_k, include=["distances"])
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        approximate = result["ids"][0]
        recalls.append(len(set(exact) & set(approximate)) / args.top_k)

    metrics = {
        "runs": args.runs,
        "warmup_runs_excluded": args.warmup,
        "top_k": args.top_k,
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95), "p99": percentile(latencies, 99), "mean": sum(latencies) / len(latencies)},
        "recall_at_k": sum(recalls) / len(recalls),
        "method": "Query vectors are precomputed; timing measures Chroma HNSW retrieval only, excluding embedding generation.",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "latency_samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run", "latency_ms", "recall_at_k"])
        writer.writerows(enumerate(zip(latencies, recalls), start=1))
    print(json.dumps(metrics, indent=2))
    print(f"Saved benchmark artifacts to {args.output_dir}")


if __name__ == "__main__":
    main()

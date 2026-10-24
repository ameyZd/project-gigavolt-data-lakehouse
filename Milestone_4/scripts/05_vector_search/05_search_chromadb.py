#!/usr/bin/env python3
"""Run a semantic ChromaDB search and print source-cited passages."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "chroma_db"
COLLECTION_NAME = "gigavolt_unstructured_v1"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--excerpt-length", type=int, default=500)
    args = parser.parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1")
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise SystemExit("Install requirements.txt before vector search.") from error

    client = chromadb.PersistentClient(path=str(args.db_path))
    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=None)
    except Exception as error:
        raise SystemExit("Vector collection missing. Run 04_ingest_chromadb.py first.") from error
    model = SentenceTransformer(args.model)
    query_vector = model.encode(args.query, normalize_embeddings=True).tolist()
    started = time.perf_counter_ns()
    result = collection.query(
        query_embeddings=[query_vector],
        n_results=min(args.top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    print(f"Query: {args.query}")
    print(f"Vector DB latency (embedding excluded): {elapsed_ms:.3f} ms\n")
    for rank, (document, metadata, distance) in enumerate(
        zip(result["documents"][0], result["metadatas"][0], result["distances"][0]), start=1
    ):
        excerpt = document[: args.excerpt_length].rstrip()
        print(f"{rank}. cosine distance={distance:.4f} | {metadata['source_file']} | page {metadata['page_number']}")
        print(f"   {excerpt}{'...' if len(document) > len(excerpt) else ''}\n")
    print("Verify each retrieved passage against the cited original PDF before using it.")


if __name__ == "__main__":
    main()

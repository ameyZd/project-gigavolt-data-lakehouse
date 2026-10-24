#!/usr/bin/env python3
"""Persist Gigavolt vectors and provenance metadata in a ChromaDB HNSW collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHUNKS = ROOT / "data" / "processed" / "chunks.jsonl"
DEFAULT_EMBEDDINGS = ROOT / "data" / "processed" / "chunk_embeddings.npz"
DEFAULT_DB_PATH = ROOT / "data" / "chroma_db"
COLLECTION_NAME = "gigavolt_unstructured_v1"


def batches(items: list[dict[str, object]], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMBEDDINGS)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--rebuild", action="store_true", help="Delete and recreate only this collection.")
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if not args.chunks.exists() or not args.embeddings.exists():
        raise SystemExit("Missing chunks or embeddings. Run stages 02 and 03 first.")
    try:
        import chromadb
        import numpy as np
    except ImportError as error:
        raise SystemExit("Install requirements.txt before ingesting ChromaDB.") from error

    chunks = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines()]
    stored = np.load(args.embeddings)
    vector_by_id = dict(zip(stored["chunk_ids"].tolist(), stored["embeddings"].tolist()))
    missing = [chunk["chunk_id"] for chunk in chunks if chunk["chunk_id"] not in vector_by_id]
    if missing:
        raise SystemExit(f"{len(missing)} chunks have no matching embedding.")

    # HNSW settings balance small-corpus recall and low latency: M=16 increases graph
    # connectivity, construction ef=200 improves build-time neighbor choice, and search
    # ef=64 raises query recall while remaining well below the 20 ms target for this corpus.
    hnsw_metadata = {
        "hnsw:space": "cosine",
        "hnsw:M": 16,
        "hnsw:construction_ef": 200,
        "hnsw:search_ef": 64,
        "description": "Gigavolt source chunks with page-level provenance",
    }
    args.db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(args.db_path))
    if args.rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"Deleted prior collection: {COLLECTION_NAME}")
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata=hnsw_metadata,
        embedding_function=None,
    )
    for group in batches(chunks, args.batch_size):
        collection.upsert(
            ids=[chunk["chunk_id"] for chunk in group],
            embeddings=[vector_by_id[chunk["chunk_id"]] for chunk in group],
            documents=[chunk["text"] for chunk in group],
            metadatas=[
                {
                    "source_file": chunk["source_file"],
                    "page_number": int(chunk["page_number"]),
                    "document_type": chunk["document_type"],
                    "document_id": chunk["document_id"],
                }
                for chunk in group
            ],
        )
    print(f"ChromaDB path: {args.db_path}")
    print(f"Collection: {COLLECTION_NAME}; vectors: {collection.count()}")
    print("HNSW: cosine distance, M=16, construction_ef=200, search_ef=64")
    print("Metadata: chunk ID, document ID/type, source filename, and source page")


if __name__ == "__main__":
    main()

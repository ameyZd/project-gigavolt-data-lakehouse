#!/usr/bin/env python3
"""Optional semantic embedding index; preserves the same chunk provenance as BM25."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "processed" / "chunks.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "embeddings.npz"
DEFAULT_METADATA = ROOT / "data" / "processed" / "embedding_metadata.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    args = parser.parse_args()
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise SystemExit("Install requirements-optional.txt before using embeddings.") from error
    if not args.input.exists():
        raise SystemExit(f"Missing {args.input}. Run 02_chunk_documents.py first.")

    chunks = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    model = SentenceTransformer(args.model)
    vectors = model.encode([chunk["text"] for chunk in chunks], normalize_embeddings=True, show_progress_bar=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, vectors=vectors, chunk_ids=[chunk["chunk_id"] for chunk in chunks])
    metadata = {
        "model": args.model,
        "chunk_count": len(chunks),
        "embedding_dimension": int(vectors.shape[1]),
        "vector_file": args.output.name,
        "source_chunk_file": args.input.name,
    }
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(chunks)} {vectors.shape[1]}-dimension embeddings to {args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create normalized, batched SentenceTransformer embeddings for document chunks."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "processed" / "chunks.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "chunk_embeddings.npz"
DEFAULT_METADATA = ROOT / "data" / "processed" / "embedding_manifest.json"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if not args.input.exists():
        raise SystemExit(f"Missing {args.input}. Run 02_chunk_documents.py first.")
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise SystemExit("Install requirements.txt before creating embeddings.") from error

    chunks = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    if not chunks:
        raise SystemExit("No chunks available for embedding.")
    model = SentenceTransformer(args.model)
    vectors = model.encode(
        [chunk["text"] for chunk in chunks],
        batch_size=args.batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        chunk_ids=np.array([chunk["chunk_id"] for chunk in chunks]),
        embeddings=vectors.astype("float32"),
    )
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "normalization": "L2 normalized; cosine similarity is appropriate",
        "batch_size": args.batch_size,
        "chunk_count": len(chunks),
        "embedding_dimension": int(vectors.shape[1]),
        "embedding_file": args.output.name,
        "source_chunk_file": args.input.name,
    }
    args.metadata.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Embedded {len(chunks)} chunks in batches of {args.batch_size}.")
    print(f"Model: {args.model}; dimensions: {vectors.shape[1]}")
    print(f"Vectors: {args.output}")


if __name__ == "__main__":
    main()

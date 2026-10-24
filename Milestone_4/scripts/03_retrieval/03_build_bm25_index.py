#!/usr/bin/env python3
"""Build a dependency-free BM25 index over Gigavolt document chunks."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "processed" / "chunks.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "bm25_index.json"
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--k1", type=float, default=1.5)
    parser.add_argument("--b", type=float, default=0.75)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Missing {args.input}. Run 02_chunk_documents.py first.")
    chunks = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    if not chunks:
        raise SystemExit("No chunks were found.")

    postings: dict[str, list[list[object]]] = defaultdict(list)
    lengths: dict[str, int] = {}
    documents: dict[str, dict[str, object]] = {}
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        tokens = tokenize(str(chunk["text"]))
        lengths[chunk_id] = len(tokens)
        documents[chunk_id] = chunk
        for term, frequency in Counter(tokens).items():
            postings[term].append([chunk_id, frequency])

    index = {
        "algorithm": "BM25",
        "parameters": {"k1": args.k1, "b": args.b},
        "document_count": len(chunks),
        "average_document_length": sum(lengths.values()) / len(lengths),
        "document_lengths": lengths,
        "postings": dict(postings),
        "documents": documents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Indexed {len(chunks)} chunks and {len(postings)} terms.")
    print(f"Index: {args.output}")


if __name__ == "__main__":
    main()

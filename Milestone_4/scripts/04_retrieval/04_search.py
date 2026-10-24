#!/usr/bin/env python3
"""Search the local BM25 index and return ranked, source-cited excerpts."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / "data" / "processed" / "bm25_index.json"
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language question or search terms")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--excerpt-length", type=int, default=500)
    args = parser.parse_args()
    if not args.index.exists():
        raise SystemExit(f"Missing {args.index}. Run 03_build_bm25_index.py first.")

    index = json.loads(args.index.read_text(encoding="utf-8"))
    total_documents = index["document_count"]
    average_length = index["average_document_length"]
    k1 = index["parameters"]["k1"]
    b = index["parameters"]["b"]
    scores: dict[str, float] = defaultdict(float)

    for term, query_frequency in Counter(tokenize(args.query)).items():
        rows = index["postings"].get(term, [])
        if not rows:
            continue
        inverse_document_frequency = math.log(1 + (total_documents - len(rows) + 0.5) / (len(rows) + 0.5))
        for chunk_id, term_frequency in rows:
            length = index["document_lengths"][chunk_id]
            denominator = term_frequency + k1 * (1 - b + b * length / average_length)
            scores[chunk_id] += query_frequency * inverse_document_frequency * term_frequency * (k1 + 1) / denominator

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[: args.top_k]
    if not ranked:
        print("No matching terms found. Try a more specific query or inspect the source PDFs.")
        return
    print(f"Query: {args.query}\n")
    for rank, (chunk_id, score) in enumerate(ranked, start=1):
        chunk = index["documents"][chunk_id]
        excerpt = chunk["text"][: args.excerpt_length].rstrip()
        print(f"{rank}. score={score:.3f} | {chunk['source_file']} | page {chunk['page_number']}")
        print(f"   {excerpt}{'...' if len(chunk['text']) > len(excerpt) else ''}\n")
    print("Verify each result against the cited original PDF before using it as an answer.")


if __name__ == "__main__":
    main()

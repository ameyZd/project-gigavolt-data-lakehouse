#!/usr/bin/env python3
"""Create overlapping, provenance-preserving chunks from page-level JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "processed" / "documents.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "chunks.jsonl"


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            boundary = max(text.rfind(". ", start, end), text.rfind("; ", start, end))
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=180)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Missing {args.input}. Run 01_extract_pdf_text.py first.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    page_count = 0
    with args.input.open(encoding="utf-8") as source, args.output.open("w", encoding="utf-8") as target:
        for line in source:
            page = json.loads(line)
            page_count += 1
            for position, chunk_text in enumerate(split_text(page["text"], args.chunk_size, args.overlap), start=1):
                chunk_count += 1
                chunk = {
                    "chunk_id": f"{page['document_id']}:p{page['page_number']}:c{position}",
                    "document_id": page["document_id"],
                    "document_type": page["document_type"],
                    "source_file": page["source_file"],
                    "page_number": page["page_number"],
                    "chunk_size_characters": args.chunk_size,
                    "overlap_characters": args.overlap,
                    "text": chunk_text,
                }
                target.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Created {chunk_count} chunks from {page_count} pages.")
    print(f"Chunks: {args.output}")


if __name__ == "__main__":
    main()

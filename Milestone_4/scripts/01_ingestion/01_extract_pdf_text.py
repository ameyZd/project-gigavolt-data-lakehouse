#!/usr/bin/env python3
"""Extract each source PDF into page-level JSONL records with provenance."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = ROOT / "data" / "raw" / "Unstructured Data"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed"


def normalize_text(value: str) -> str:
    """Make PDF extraction readable without changing the source artifact."""
    return re.sub(r"\s+", " ", value or "").strip()


def document_type(filename: str) -> str:
    prefix = filename.split("-", 2)[1] if "-" in filename else "OTHER"
    return {
        "ECO": "engineering_change_order",
        "INC": "incident_report",
        "MAN": "manual",
        "SOP": "standard_operating_procedure",
        "SPEC": "specification",
        "SUP": "supplier_report",
    }.get(prefix, "other")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    pdfs = sorted(args.source_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {args.source_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    text_path = args.output_dir / "documents.jsonl"
    manifest_path = args.output_dir / "extraction_manifest.json"
    records: list[dict[str, object]] = []
    empty_pages: list[dict[str, object]] = []

    for pdf_path in pdfs:
        reader = PdfReader(pdf_path)
        for page_number, page in enumerate(reader.pages, start=1):
            text = normalize_text(page.extract_text() or "")
            record = {
                "document_id": pdf_path.stem,
                "document_type": document_type(pdf_path.name),
                "source_file": pdf_path.name,
                "page_number": page_number,
                "text": text,
            }
            records.append(record)
            if not text:
                empty_pages.append({"source_file": pdf_path.name, "page_number": page_number})

    with text_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(args.source_dir),
        "document_count": len(pdfs),
        "page_count": len(records),
        "empty_text_pages": empty_pages,
        "output": text_path.name,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(records)} pages from {len(pdfs)} PDFs.")
    print(f"Text: {text_path}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()

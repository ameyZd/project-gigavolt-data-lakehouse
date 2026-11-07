#!/usr/bin/env python3
"""Build Milestone 5's own ChromaDB vector store for the technical PDFs (self-contained).

One command, five stages, all inside Milestone_5/data/ (nothing is read from other milestones):

  1. download   https://622.gomillion.org/data/UnstructuredData.zip  -> data/raw/  (sha256 recorded)
  2. extract    page-level text with pypdf                          -> data/processed/documents.jsonl
  3. chunk      900-char chunks, 180-char overlap, sentence-aware   -> data/processed/chunks.jsonl
  4. embed      all-MiniLM-L6-v2, L2-normalized (384-d)             -> data/processed/embedding_manifest.json
  5. load       ChromaDB HNSW collection gigavolt_unstructured_v1   -> data/chroma_db/
                (cosine, M=16, construction_ef=200, search_ef=64)

Every chunk keeps its provenance (source_file, page_number, document_type, document_id), which the
RAG pipeline turns into [V#] citations.

Usage:  python scripts/00_build_vector_store.py            # build (re-uses an existing download)
        python scripts/00_build_vector_store.py --rebuild  # drop and recreate the collection
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
DATA = M5_ROOT / "data"
RAW_DIR = DATA / "raw"
PDF_DIR = RAW_DIR / "unstructured_pdfs"
PROCESSED = DATA / "processed"
CHROMA_DIR = DATA / "chroma_db"

ZIP_URL = "https://622.gomillion.org/data/UnstructuredData.zip"
COLLECTION = "gigavolt_unstructured_v1"
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE, OVERLAP = 900, 180
HNSW = {"hnsw:space": "cosine", "hnsw:M": 16, "hnsw:construction_ef": 200, "hnsw:search_ef": 64,
        "description": "Gigavolt technical PDF chunks with page-level provenance (Milestone 5)"}
DOC_TYPES = {"ECO": "engineering_change_order", "INC": "incident_report", "MAN": "manual",
             "SOP": "standard_operating_procedure", "SPEC": "specification", "SUP": "supplier_report"}


def stage(n: int, text: str) -> None:
    print(f"\n[{n}/5] {text}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(force: bool) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RAW_DIR / "UnstructuredData.zip"
    if force or not zip_path.exists():
        import requests  # uses certifi's CA bundle, so it works on any OS Python

        response = requests.get(ZIP_URL, headers={"User-Agent": "gigavolt-m5-builder"}, timeout=60)
        response.raise_for_status()
        tmp = zip_path.with_suffix(".part")
        tmp.write_bytes(response.content)
        tmp.replace(zip_path)  # never leave a half-written zip behind
        print(f"  downloaded {zip_path.stat().st_size:,} bytes from {ZIP_URL}")
    else:
        print(f"  re-using {zip_path} (use --redownload to fetch again)")
    if PDF_DIR.exists():
        shutil.rmtree(PDF_DIR)
    PDF_DIR.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = Path(member.filename).name          # flatten folders; ignores any '../' path tricks
            if member.is_dir() or not name.lower().endswith(".pdf") or name.startswith("."):
                continue
            with archive.open(member) as src, (PDF_DIR / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"  {len(pdfs)} PDFs in {PDF_DIR}  (zip sha256 {sha256(zip_path)[:16]}...)")
    if not pdfs:
        raise SystemExit("No PDFs found in the zip.")
    return zip_path


def document_type(filename: str) -> str:
    parts = filename.split("-", 2)
    return DOC_TYPES.get(parts[1] if len(parts) > 1 else "", "other")


def extract() -> list[dict]:
    from pypdf import PdfReader

    pages = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        for number, page in enumerate(PdfReader(pdf).pages, start=1):
            pages.append({"document_id": pdf.stem, "document_type": document_type(pdf.name), "source_file": pdf.name,
                          "page_number": number, "text": re.sub(r"\s+", " ", page.extract_text() or "").strip()})
    PROCESSED.mkdir(parents=True, exist_ok=True)
    with (PROCESSED / "documents.jsonl").open("w", encoding="utf-8") as out:
        out.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in pages)
    empty = sum(1 for p in pages if not p["text"])
    print(f"  {len(pages)} pages extracted ({empty} empty)")
    return pages


def split_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):  # prefer to cut at a sentence boundary in the second half of the window
            boundary = max(text.rfind(". ", start, end), text.rfind("; ", start, end))
            if boundary > start + CHUNK_SIZE // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return [c for c in chunks if c]


def chunk(pages: list[dict]) -> list[dict]:
    chunks = []
    for page in pages:
        for position, text in enumerate(split_text(page["text"]), start=1):
            chunks.append({"chunk_id": f"{page['document_id']}:p{page['page_number']}:c{position}",
                           "document_id": page["document_id"], "document_type": page["document_type"],
                           "source_file": page["source_file"], "page_number": page["page_number"], "text": text})
    with (PROCESSED / "chunks.jsonl").open("w", encoding="utf-8") as out:
        out.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks)
    counts: dict[str, int] = {}
    for c in chunks:
        counts[c["document_type"]] = counts.get(c["document_type"], 0) + 1
    print(f"  {len(chunks)} chunks ({CHUNK_SIZE} chars, {OVERLAP} overlap): {counts}")
    return chunks


def embed(chunks: list[dict], zip_path: Path):
    from sentence_transformers import SentenceTransformer

    started = time.perf_counter()
    model = SentenceTransformer(MODEL, device="cpu")
    vectors = model.encode([c["text"] for c in chunks], batch_size=32, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=True)
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "embedding_dimension": int(vectors.shape[1]), "normalization": "L2 (cosine)",
        "chunk_count": len(chunks), "chunk_size_characters": CHUNK_SIZE, "overlap_characters": OVERLAP,
        "source_url": ZIP_URL, "source_zip_sha256": sha256(zip_path), "collection": COLLECTION, "hnsw": HNSW,
    }
    (PROCESSED / "embedding_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"  {len(chunks)} x {vectors.shape[1]}-d vectors in {time.perf_counter() - started:.1f} s")
    return vectors


def load(chunks: list[dict], vectors, rebuild: bool) -> None:
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if rebuild:
        try:
            client.delete_collection(COLLECTION)
            print(f"  dropped existing collection {COLLECTION}")
        except Exception:
            pass
    collection = client.get_or_create_collection(name=COLLECTION, metadata=HNSW, embedding_function=None)
    for start in range(0, len(chunks), 64):
        group = chunks[start:start + 64]
        collection.upsert(
            ids=[c["chunk_id"] for c in group],
            embeddings=vectors[start:start + 64].tolist(),
            documents=[c["text"] for c in group],
            metadatas=[{"source_file": c["source_file"], "page_number": int(c["page_number"]),
                        "document_type": c["document_type"], "document_id": c["document_id"]} for c in group],
        )
    print(f"  {CHROMA_DIR}  collection={COLLECTION}  vectors={collection.count()}")
    print("  HNSW: cosine, M=16, construction_ef=200, search_ef=64")
    probe = collection.query(query_embeddings=[vectors[0].tolist()], n_results=1, include=["metadatas", "distances"])
    meta = probe["metadatas"][0][0]
    print(f"  self-check: nearest to chunk 1 -> {meta['source_file']} p.{meta['page_number']} "
          f"(distance {probe['distances'][0][0]:.4f})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate the Chroma collection")
    parser.add_argument("--redownload", action="store_true", help="download the zip again")
    args = parser.parse_args()
    try:
        import chromadb, pypdf, sentence_transformers  # noqa: F401,E401
    except ImportError as error:
        sys.exit(f"Missing package ({error.name}). Run: pip install -r requirements.txt")

    started = time.perf_counter()
    stage(1, "Download technical PDFs");  zip_path = download(args.redownload)
    stage(2, "Extract page text");        pages = extract()
    stage(3, "Chunk with overlap");       chunks = chunk(pages)
    stage(4, f"Embed with {MODEL}");      vectors = embed(chunks, zip_path)
    stage(5, "Load ChromaDB (HNSW)");     load(chunks, vectors, args.rebuild)
    print(f"\nVector store ready in {time.perf_counter() - started:.1f} s. Next: python scripts/03_check_sources.py")


if __name__ == "__main__":
    main()

"""Rebuild the Milestone 4 vector store (ChromaDB + HNSW) on this instance.

Same design as Milestone 4: page-level PDF extraction -> sentence-aware chunks
(900 chars, 180 overlap) -> all-MiniLM-L6-v2 embeddings (384-d, cosine) -> ChromaDB.
Chroma's built-in ONNX version of MiniLM is used, so no PyTorch install is needed.

Usage:  python build_vector_store.py [data/pdfs]
"""
import os
import re
import sys
import time
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from dotenv import load_dotenv
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

CHUNK_SIZE, OVERLAP = 900, 180
HNSW = {"hnsw:space": "cosine", "hnsw:M": 16, "hnsw:construction_ef": 200, "hnsw:search_ef": 64}
DOC_TYPES = {"ECO": "engineering_change_order", "INC": "incident_report", "MAN": "manual",
             "SOP": "standard_operating_procedure", "SPEC": "specification", "SUP": "supply_chain_report"}


def chunk_text(text: str) -> list[str]:
    """Greedy sentence packing up to CHUNK_SIZE chars, carrying ~OVERLAP chars of trailing sentences."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks, current = [], []
    for sent in sentences:
        if current and len(" ".join(current + [sent])) > CHUNK_SIZE:
            chunks.append(" ".join(current))
            carry = []
            for prev in reversed(current):          # overlap = whole trailing sentences
                if len(" ".join([prev] + carry)) > OVERLAP:
                    break
                carry.insert(0, prev)
            current = carry
        current.append(sent)
    if current:
        chunks.append(" ".join(current))
    return chunks


def main() -> None:
    pdf_dir = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "data" / "pdfs")
    pdfs = sorted(pdf_dir.rglob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found under {pdf_dir}")

    ids, texts, metas = [], [], []
    indexed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for pdf in pdfs:
        doc_id = pdf.stem
        kind = DOC_TYPES.get(doc_id.split("-")[1] if doc_id.count("-") else "", "document")
        for page_no, page in enumerate(PdfReader(pdf).pages, 1):
            text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
            for c_no, chunk in enumerate(chunk_text(text), 1):
                ids.append(f"{doc_id}:p{page_no}:c{c_no}")
                texts.append(chunk)
                metas.append({"document_id": doc_id, "document_type": kind, "source_file": pdf.name,
                              "page_number": page_no, "chunk_index": c_no, "indexed_at": indexed_at})

    client = chromadb.PersistentClient(path=str(HERE / os.getenv("CHROMA_DIR", "chroma_db")))
    name = os.getenv("CHROMA_COLLECTION", "gigavolt_docs")
    try:
        client.delete_collection(name)
    except Exception:
        pass
    coll = client.create_collection(name, metadata=HNSW, embedding_function=DefaultEmbeddingFunction())

    t0 = time.perf_counter()
    for i in range(0, len(ids), 64):
        coll.add(ids=ids[i:i + 64], documents=texts[i:i + 64], metadatas=metas[i:i + 64])
    print(f"Indexed {coll.count()} chunks from {len(pdfs)} PDFs into '{name}' "
          f"in {time.perf_counter() - t0:.1f}s  (HNSW: {HNSW})")

    t0 = time.perf_counter()
    hit = coll.query(query_texts=["emergency response for thermal runaway"], n_results=1)
    print(f"Smoke test ({(time.perf_counter() - t0) * 1000:.0f} ms): {hit['ids'][0][0]}")


if __name__ == "__main__":
    main()

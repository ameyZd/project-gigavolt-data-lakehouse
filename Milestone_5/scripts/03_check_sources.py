#!/usr/bin/env python3
"""Health check for every data source the RAG pipeline depends on (run first in the demo).

  MariaDB  : connects as the SELECT-only RAG user, counts rows in each vw_rag_* view, server version
  MongoDB  : document count, fault events, units, index list
  ChromaDB : vector count + HNSW settings (built by 00_build_vector_store.py)
  LLM      : (optional --llm) a tiny test request to confirm the LLM (Groq key / local Ollama) works

Usage:  python scripts/03_check_sources.py [--llm]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import Settings  # noqa: E402
from gigavolt_rag.display import c  # noqa: E402


def check(name: str, func) -> bool:
    started = time.perf_counter()
    try:
        info = func()
    except Exception as error:
        print(c(f"✘ {name:<34} {error.__class__.__name__}: {error}", "red"))
        return False
    elapsed = (time.perf_counter() - started) * 1000
    print(c(f"✔ {name:<34} ({elapsed:.0f} ms)", "green"))
    for key, value in info.items():
        print(f"     {key:<26} {value}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", action="store_true", help="also send a tiny test prompt to the LLM")
    args = parser.parse_args()
    settings = Settings.from_env()

    from gigavolt_rag.sources.document_source import DocumentSource
    from gigavolt_rag.sources.sql_source import SQLSource
    from gigavolt_rag.sources.vector_source import VectorSource

    results = [
        check(f"MariaDB  {settings.mariadb_user}@{settings.mariadb_database}", SQLSource(settings).health),
        check(f"MongoDB  {settings.mongo_db}.{settings.mongo_collection}", DocumentSource(settings).health),
        check(f"ChromaDB {settings.chroma_collection}", VectorSource(settings).health),
    ]
    print(c("•  Graph DB (Neo4j)                   not required - removed from M5 by instructor (Nov 2)", "dim"))
    if args.llm:
        from gigavolt_rag.llm import LLMClient

        def llm_ping():
            client = LLMClient(settings)
            reply = client.generate("Reply with the single word OK.", "Health check.")
            if not reply.ok:
                raise RuntimeError(reply.error)
            return {"provider/model": f"{reply.provider} / {reply.model}", "latency": f"{reply.elapsed_ms:.0f} ms",
                    "reply": reply.text.strip()[:40]}

        results.append(check(f"LLM      {settings.llm_provider}", llm_ping))
    print()
    print(c("All required sources are online." if all(results) else "Fix the failed source(s) above before running ask.py.",
            "green" if all(results) else "red", "bold"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

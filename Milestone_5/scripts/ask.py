#!/usr/bin/env python3
"""Ask the Gigavolt Knowledge Engine a natural-language question.

Examples
--------
  python scripts/ask.py "Which suppliers provide lithium cells for our commercial energy storage units?"
  python scripts/ask.py --interactive            # load models once, then ask many questions (use for the demo)
  python scripts/ask.py --provider extractive "..."   # no LLM call, fully cited extractive answer

Every run is saved to output/runs/<timestamp>_<question>.json and output/last_result.json
(used by scripts/verify_citation.py).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import Settings  # noqa: E402
from gigavolt_rag.display import c, render_terminal  # noqa: E402
from gigavolt_rag.llm import LLMClient  # noqa: E402
from gigavolt_rag.pipeline import RAGPipeline, save_result  # noqa: E402


def build(provider: str | None) -> RAGPipeline:
    settings = Settings.from_env()
    pipeline = RAGPipeline(settings, llm=LLMClient(settings, provider))
    started = time.perf_counter()
    print(c("Warming up (connections + embedding model; one-time cost, not part of query latency) ...", "dim"))
    for name, status in pipeline.warmup().items():
        print(c(f"   {name:<9} {status}", "dim" if status.startswith("ok") else "red"))
    print(c(f"   ready in {time.perf_counter() - started:.1f} s | LLM: {pipeline.llm.provider} / {pipeline.llm.model}", "dim"))
    return pipeline


def ask(pipeline: RAGPipeline, question: str, show_context: bool, save: bool) -> None:
    result = pipeline.answer(question)
    print(render_terminal(result, show_context=show_context, target_s=pipeline.settings.latency_target_s))
    if save and result.retrieval is not None:
        path = save_result(result)
        print(c(f"\n   saved {path.relative_to(M5_ROOT)}  (verify a citation: python scripts/verify_citation.py S1)", "dim"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*", help="question text (omit with --interactive)")
    parser.add_argument("--interactive", "-i", action="store_true")
    parser.add_argument("--provider", choices=["anthropic", "openai", "ollama", "extractive"])
    parser.add_argument("--show-context", action="store_true", help="print the evidence text of each record")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if not question and not args.interactive:
        parser.error("give a question or use --interactive")
    pipeline = build(args.provider)
    try:
        if question:
            ask(pipeline, question, args.show_context, not args.no_save)
        if args.interactive:
            print(c("\nInteractive mode. Type a question and press Enter (blank line or 'exit' to quit).", "bold"))
            while True:
                try:
                    question = input(c("\nGigavolt> ", "green", "bold")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if question.lower() in {"", "exit", "quit"}:
                    break
                ask(pipeline, question, args.show_context, not args.no_save)
    finally:
        pipeline.retriever.close()


if __name__ == "__main__":
    main()

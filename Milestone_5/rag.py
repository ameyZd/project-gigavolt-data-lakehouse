"""End-to-end Gigavolt RAG pipeline: question -> hybrid retrieval -> prompt -> LLM -> cited answer.

Usage:
  python rag.py "Which suppliers provide lithium cells?"     # one question
  python rag.py --show-context "..."                        # also print the assembled prompt
  python rag.py                                             # interactive mode (models stay warm)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from hybrid_retrieval import HERE, Evidence, HybridRetriever

console = Console()
TAG_RE = re.compile(r"\[((?:SQL|MDB|VEC)\d+)\]")


class RagPipeline:
    def __init__(self) -> None:
        self.retriever = HybridRetriever()
        self.system_prompt = (HERE / "prompts" / "system_prompt.txt").read_text()
        self.template = (HERE / "prompts" / "answer_template.txt").read_text()
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
        self.http = requests.Session()   # keep-alive: saves a TLS handshake per question

    # ---------------------------------------------------------------- prompt assembly
    @staticmethod
    def format_context(evidence: list[Evidence]) -> str:
        return "\n\n".join(f"[{e.tag}] ({e.database} | record {e.record_id} | {e.document} | {e.timestamp})\n{e.text}"
                           for e in evidence)

    def build_prompt(self, question: str, route_desc: str, evidence: list[Evidence]) -> str:
        return self.template.format(question=question, route=route_desc, context=self.format_context(evidence))

    # ---------------------------------------------------------------- generation
    def call_llm(self, prompt: str) -> tuple[str, str]:
        """Returns (answer, status). Never raises: falls back to an extractive answer on any failure."""
        if not self.api_key:
            return "", "no LLM_API_KEY set"
        body = {"model": self.model, "temperature": 0.1, "max_tokens": 2000,
                "messages": [{"role": "system", "content": self.system_prompt},
                             {"role": "user", "content": prompt}]}
        if "gpt-oss" in self.model:   # reasoning model: hidden thinking tokens share max_tokens
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        for attempt in range(2):
            try:
                resp = self.http.post(f"{self.base_url}/chat/completions", json=body, timeout=20,
                                      headers={"Authorization": f"Bearer {self.api_key}"})
                if resp.status_code == 429 and attempt == 0:      # free-tier rate limit: wait once, retry
                    time.sleep(min(float(resp.headers.get("retry-after", 5)), 15))
                    continue
                if resp.status_code >= 400:   # surface the provider's message (e.g. model not found)
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                choice = resp.json()["choices"][0]
                text = re.sub(r"<think>.*?</think>", "", choice["message"]["content"] or "", flags=re.S).strip()
                status = "ok" if attempt == 0 else "ok (after rate-limit retry)"
                if choice.get("finish_reason") == "length":    # never pass off a cut-off answer as complete
                    text += "\n\n*(Answer truncated at the token limit.)*"
                    status += ", TRUNCATED"
                return text, status
            except Exception as exc:
                err = f"{type(exc).__name__}: {str(exc)[:160]}"
        return "", err

    @staticmethod
    def extractive_answer(evidence: list[Evidence], reason: str) -> str:
        lines = [f"**Summary:** LLM unavailable ({reason}); showing the retrieved evidence verbatim instead."]
        lines += [f"- [{e.tag}] {e.text[:220]}" for e in evidence[:8]]
        return "\n".join(lines)

    # ---------------------------------------------------------------- provenance check
    @staticmethod
    def check_citations(answer: str, evidence: list[Evidence]) -> dict:
        valid = {e.tag for e in evidence}
        used = TAG_RE.findall(answer)
        statements = [ln.strip() for ln in answer.splitlines()            # skip blank lines / bare headings
                      if len(re.sub(r"\[[A-Z]+\d+\]|[*#\-:\s]", "", ln)) > 15
                      and not re.fullmatch(r"[*#\s]*(Summary|Recommended next step)[*:\s]*", ln.strip())]
        uncited = [s for s in statements if not set(TAG_RE.findall(s)) & valid]
        return {"statements": len(statements), "cited_statements": len(statements) - len(uncited),
                "uncited": uncited, "unknown_tags": sorted(set(used) - valid),
                "used_tags": sorted(set(used) & valid, key=lambda t: (t[:3], int(t[3:])))}

    # ---------------------------------------------------------------- main entry
    def ask(self, question: str) -> dict:
        t0 = time.perf_counter()
        route, evidence, timings, errors = self.retriever.retrieve(question)
        t1 = time.perf_counter()
        prompt = self.build_prompt(question, route.describe(), evidence)
        timings["prompt_ms"] = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        if not evidence:
            answer, status = "No relevant records were found in any data source, so I cannot answer this.", "skipped (no context)"
        else:
            answer, status = self.call_llm(prompt)
            if not answer:
                answer = self.extractive_answer(evidence, status)
        timings["generation_ms"] = (time.perf_counter() - t2) * 1000
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return {"question": question, "route": route.describe(), "route_reasons": route.reasons,
                "sources_used": route.sources, "errors": errors, "llm": f"{self.model} - {status}",
                "answer": answer, "citations": self.check_citations(answer, evidence),
                "evidence": [asdict(e) for e in evidence], "timings_ms": {k: round(v, 1) for k, v in timings.items()},
                "prompt": prompt}


# ---------------------------------------------------------------------------- display
def show(result: dict, show_context: bool = False) -> None:
    console.rule(f"[bold]Q: {result['question']}")
    console.print(Panel("\n".join(result["route_reasons"]), title=f"Query routing -> {result['sources_used']}",
                        border_style="cyan"))
    if result["errors"]:
        console.print(f"[yellow]Source errors (pipeline continued without them): {result['errors']}")
    if show_context:
        console.print(Panel(result["prompt"], title="Assembled prompt (context sent to the LLM)", border_style="dim"))
    console.print(Panel(Markdown(result["answer"]), title=f"Answer  ({result['llm']})", border_style="green"))

    used = set(result["citations"]["used_tags"])
    table = Table(title="Source provenance (cited records in bold)", show_lines=False)
    for col in ("Tag", "Database", "Record ID", "Document / table", "Timestamp"):
        table.add_column(col, overflow="fold")
    for e in result["evidence"]:
        style = "bold" if e["tag"] in used else "dim"
        table.add_row(e["tag"], e["database"], e["record_id"], e["document"], e["timestamp"], style=style)
    console.print(table)

    c = result["citations"]
    console.print(f"Citation check: {c['cited_statements']}/{c['statements']} statements cited, "
                  f"{len(c['used_tags'])} records used, unknown tags: {c['unknown_tags'] or 'none'}")
    t = result["timings_ms"]
    parts = " | ".join(f"{k[:-3]} {t[k]:.0f}" for k in ("sql_ms", "document_ms", "vector_ms") if k in t)
    console.print(f"[bold]Latency:[/] retrieval {t['retrieval_ms']:.0f} ms ({parts}) + generation "
                  f"{t['generation_ms']:.0f} ms = [bold]{t['total_ms'] / 1000:.2f} s total[/]")


def save(result: dict) -> Path:
    out = HERE / "results" / "runs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*")
    ap.add_argument("--show-context", action="store_true", help="print the full prompt sent to the LLM")
    args = ap.parse_args()

    with console.status("Connecting to MariaDB, MongoDB, ChromaDB and loading the embedding model..."):
        rag = RagPipeline()
    if rag.retriever.init_errors:
        console.print(f"[yellow]Unavailable sources: {rag.retriever.init_errors}")

    questions = [" ".join(args.question)] if args.question else []
    while True:
        try:
            q = questions.pop(0) if questions else (None if args.question else console.input("\n[bold]Ask Gigavolt> "))
        except (EOFError, KeyboardInterrupt):
            break
        if q is None or q.strip().lower() in {"", "exit", "quit"}:
            break
        result = rag.ask(q.strip())
        show(result, args.show_context)
        console.print(f"[dim]Saved run to {save(result).relative_to(HERE)}")


if __name__ == "__main__":
    main()

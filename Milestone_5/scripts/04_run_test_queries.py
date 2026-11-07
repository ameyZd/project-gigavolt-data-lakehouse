#!/usr/bin/env python3
"""Run the evaluation questions and save answers with full source-provenance mapping.

Outputs (committed as the "test query results" deliverable):
  output/test_results/Q01_<slug>.md / .json   answer, statement->source map, cited records, latency
  output/test_results/README.md                summary table across all questions

Usage:  python scripts/04_run_test_queries.py [--only Q01 Q03] [--provider extractive]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import OUTPUT_DIR, Settings  # noqa: E402
from gigavolt_rag.display import render_markdown, render_terminal  # noqa: E402
from gigavolt_rag.llm import LLMClient  # noqa: E402
from gigavolt_rag.pipeline import RAGPipeline, slugify  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", type=Path, default=M5_ROOT / "tests" / "test_queries.json")
    parser.add_argument("--only", nargs="*", help="query IDs to run, e.g. Q01 Q03")
    parser.add_argument("--provider", choices=["groq", "ollama", "anthropic", "openai", "extractive"])
    parser.add_argument("--quiet", action="store_true", help="do not print each full answer")
    args = parser.parse_args()

    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    if args.only:
        queries = [q for q in queries if q["id"] in set(args.only)]
    settings = Settings.from_env()
    pipeline = RAGPipeline(settings, llm=LLMClient(settings, args.provider))
    print("Warm-up:", pipeline.warmup())
    out_dir = OUTPUT_DIR / "test_results"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    if settings.llm_min_interval_s and pipeline.llm.provider != "extractive":
        print(f"Pacing: {settings.llm_min_interval_s:g} s between questions (free-tier rate limit; not counted in latency)")
    for query in queries:
        pipeline.llm.cooldown()
        result = pipeline.answer(query["question"])
        name = f"{query['id']}_{slugify(query['question'], 40)}"
        (out_dir / f"{name}.md").write_text(render_markdown(result, f"{query['id']} · {query['category']}"), encoding="utf-8")
        (out_dir / f"{name}.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        if not args.quiet:
            print(render_terminal(result, target_s=settings.latency_target_s))
        used = []
        if result.retrieval:
            used = [n for n in ("sql", "document", "vector") if result.retrieval.sources[n].items]
        expected = set(query.get("expect_sources", []))
        report = result.provenance
        rows.append({
            "id": query["id"],
            "category": query["category"],
            "question": query["question"],
            "mode": result.mode,
            "sources_used": used,
            "expected_met": expected.issubset(set(used)),
            "items": {n: len(result.retrieval.sources[n].items) for n in ("sql", "document", "vector")} if result.retrieval else {},
            "coverage": round(report.coverage_pct, 1) if report and report.statements else None,
            "invalid": len(report.invalid_citations) if report else 0,
            "numeric": round(report.numeric_grounding_pct, 1) if report and report.statements else None,
            "retrieval_ms": round(result.timings_ms.get("retrieval.retrieval_total", 0.0), 1),
            "llm_ms": round(result.timings_ms.get("llm_generation", 0.0), 1),
            "total_s": round(result.total_ms / 1000, 2),
            "file": f"{name}.md",
        })
        print(f"{query['id']}: {result.mode}, {rows[-1]['total_s']} s, coverage {rows[-1]['coverage']}%, sources {used}")

    label = {"sql": "SQL", "document": "Doc", "vector": "Vec"}
    lines = [
        "# Milestone 5 test query results",
        "",
        f"LLM: `{pipeline.llm.provider} / {pipeline.llm.model}` · Latency target: {settings.latency_target_s:g} s",
        "",
        "| ID | Category | Mode | Sources used (records) | Citation coverage | Invalid tags | Numeric grounding | Retrieval ms | LLM ms | Total s | Detail |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        used = ", ".join(f"{label[n]}({r['items'].get(n, 0)})" for n in r["sources_used"]) or "none"
        cov = f"{r['coverage']}%" if r["coverage"] is not None else "n/a"
        num = f"{r['numeric']}%" if r["numeric"] is not None else "n/a"
        lines.append(f"| {r['id']} | {r['category']} | {r['mode']} | {used} | {cov} | {r['invalid']} | {num} | "
                     f"{r['retrieval_ms']} | {r['llm_ms']} | {r['total_s']} | [{r['file']}]({r['file']}) |")
    answered = [r for r in rows if r["coverage"] is not None]
    if answered:
        full = sum(1 for r in answered if r["coverage"] == 100.0 and r["invalid"] == 0)
        lines += ["", f"**{full}/{len(answered)} answers fully cited with zero invalid citation tags.**"]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nSaved {len(rows)} results to {out_dir.relative_to(M5_ROOT)}/ (see README.md there)")
    pipeline.retriever.close()


if __name__ == "__main__":
    main()

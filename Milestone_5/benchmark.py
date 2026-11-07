"""Run the test questions through the full RAG pipeline and report latency + provenance.

Usage:  python benchmark.py [--rounds 3] [--pause 10]

Writes (commit these - they are Milestone 5 deliverables):
  results/test_query_results.md   every test question, its answer and the source provenance mapping
  results/benchmark.md            P50/P95/P99 latency, retrieval-vs-generation breakdown per stage
  results/benchmark.csv           raw timings for every run

--pause waits between questions (not counted in latency) to stay under the free LLM tier's rate limit.
"""
import argparse
import csv
import statistics
import time

from rich.console import Console

from hybrid_retrieval import HERE
from rag import RagPipeline

TEST_QUESTIONS = [
    "What are common failure modes for Model X-7000 batteries in high-temperature environments?",
    "Which suppliers provide lithium cells for our commercial energy storage units?",
    "Show me service history for equipment unit SN-4829 and related warranty claims",
    "Show service history and warranty claims for equipment EQ-6269",
    "What does fault code ERR_INV_404 mean and which units reported it?",
    "Why is unit SN-C12-0055 raising cooling faults and what should the technician check?",
    "Which parts have a single-source supplier risk?",
    "What are the emergency response steps for a confirmed thermal runaway event?",
]
STAGES = ["routing_ms", "sql_ms", "document_ms", "vector_ms", "retrieval_ms", "prompt_ms", "generation_ms", "total_ms"]


def pct(values: list[float], p: float) -> float:
    """Percentile with linear interpolation (same as numpy's default)."""
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (k - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--pause", type=float, default=20.0)
    args = ap.parse_args()
    console = Console()
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)

    with console.status("Warming up (connections + embedding model, done once at startup)..."):
        rag = RagPipeline()
    rows, first_round = [], []
    n_total = args.rounds * len(TEST_QUESTIONS)
    for rnd in range(1, args.rounds + 1):
        for qi, q in enumerate(TEST_QUESTIONS, 1):
            res = rag.ask(q)
            t = res["timings_ms"]
            c = res["citations"]
            rows.append({"round": rnd, "q": qi, **{s: t.get(s, 0.0) for s in STAGES},
                         "rate_limit_wait_ms": t.get("rate_limit_wait_ms", 0.0),
                         "llm_status": res["llm"], "evidence": len(res["evidence"]),
                         "cited": f"{c['cited_statements']}/{c['statements']}", "cited_n": c["cited_statements"],
                         "statements_n": c["statements"], "unknown_tags": len(c["unknown_tags"]),
                         "uncited_lines": " || ".join(c["uncited"])[:500]})
            if rnd == 1:
                first_round.append(res)
            console.print(f"[{len(rows):>2}/{n_total}] Q{qi} total {t['total_ms']:.0f} ms "
                          f"(retrieval {t['retrieval_ms']:.0f}, generation {t['generation_ms']:.0f}) "
                          f"cited {rows[-1]['cited']}  {res['llm']}")
            time.sleep(args.pause)

    with (out_dir / "benchmark.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # ------------------------------------------------------------- latency report
    answered = [r for r in rows if " - ok" in r["llm_status"]]
    waited = [r for r in answered if r["rate_limit_wait_ms"] > 0]
    all_rows, rows = rows, answered or rows     # latency stats use runs where the LLM actually answered
    lines = ["# Milestone 5 - RAG latency benchmark", "",
             f"- LLM answered {len(answered)}/{len(all_rows)} runs"
             + ("" if len(answered) == len(all_rows) else
                "; the others hit the free-tier quota and used the evidence-only fallback (excluded below)"),
             f"- Runs that waited on the free-tier quota (HTTP 429): {len(waited)}"
             + (" - that sleep is in `benchmark.csv` (`rate_limit_wait_ms`) and excluded from latency" if waited else ""),
             f"- Citation coverage: {sum(r['cited_n'] for r in rows)}/{sum(r['statements_n'] for r in rows)} answer "
             f"statements carry a valid source tag; invented tags: {sum(r['unknown_tags'] for r in rows)}",
             f"- Date: {time.strftime('%Y-%m-%d %H:%M')}  |  LLM: `{rag.model}` via `{rag.base_url}`",
             f"- Runs: {len(all_rows)} ({args.rounds} rounds x {len(TEST_QUESTIONS)} questions); "
             "connections and the embedding model are warmed once at startup (like a running service)",
             "- Retrieval sources run in parallel threads, so `retrieval` is less than the sum of SQL + document + vector",
             f"- Pause of {args.pause}s between questions (free-tier rate limit) is NOT included in latency", "",
             "## End-to-end and per-stage latency (ms)", "",
             "| Stage | Mean | P50 | P95 | P99 | Max | Share of total (mean) |",
             "|---|---|---|---|---|---|---|"]
    mean_total = statistics.mean(r["total_ms"] for r in rows)
    for s in STAGES:
        v = [r[s] for r in rows if r[s] > 0] or [0.0]
        lines.append(f"| {s[:-3]} | {statistics.mean(v):.0f} | {pct(v, 50):.0f} | {pct(v, 95):.0f} | "
                     f"{pct(v, 99):.0f} | {max(v):.0f} | {statistics.mean(v) / mean_total * 100:.0f}% |")
    totals = [r["total_ms"] for r in rows]
    under = sum(t < 5000 for t in totals)
    lines += ["", f"**Median end-to-end latency: {pct(totals, 50) / 1000:.2f} s  |  P95 {pct(totals, 95) / 1000:.2f} s  |  "
              f"{under}/{len(totals)} runs under the 5-second target ({under / len(totals) * 100:.0f}%).**", "",
              "## Per question (median of rounds, ms)", "", "| # | Question | Retrieval | Generation | Total | Cited |",
              "|---|---|---|---|---|---|"]
    for qi, q in enumerate(TEST_QUESTIONS, 1):
        qr = [r for r in rows if r["q"] == qi] or [r for r in all_rows if r["q"] == qi]
        lines.append(f"| {qi} | {q} | {statistics.median(r['retrieval_ms'] for r in qr):.0f} | "
                     f"{statistics.median(r['generation_ms'] for r in qr):.0f} | "
                     f"{statistics.median(r['total_ms'] for r in qr):.0f} | {qr[0]['cited']} |")
    (out_dir / "benchmark.md").write_text("\n".join(lines) + "\n")

    # ------------------------------------------------------------- answers + provenance mapping
    md = ["# Milestone 5 - Test query results with source provenance", "",
          "Each answer sentence carries citation tags. The table under it maps every tag to the exact source record "
          "(database type, record ID, document/table, timestamp) and the command that re-fetches it.", ""]
    for qi, res in enumerate(first_round, 1):
        c, t = res["citations"], res["timings_ms"]
        md += [f"## Q{qi}. {res['question']}", "", f"*Route:* `{res['route']}`  ", f"*LLM:* {res['llm']}  ",
               f"*Latency:* retrieval {t['retrieval_ms']:.0f} ms + generation {t['generation_ms']:.0f} ms = "
               f"**{t['total_ms'] / 1000:.2f} s**  ",
               f"*Citation check:* {c['cited_statements']}/{c['statements']} statements cited; "
               f"unknown tags: {c['unknown_tags'] or 'none'}", "", "### Answer", "", res["answer"], "",
               "### Provenance", "", "| Tag | Cited | Database | Record ID | Document / table | Timestamp | Verify |",
               "|---|---|---|---|---|---|---|"]
        for e in res["evidence"]:
            md.append(f"| {e['tag']} | {'yes' if e['tag'] in c['used_tags'] else ''} | {e['database']} | "
                      f"{e['record_id']} | {e['document']} | {e['timestamp']} | `{e['verify'].replace('|', '/')}` |")
        md.append("")
    (out_dir / "test_query_results.md").write_text("\n".join(md))

    start = lines.index("## End-to-end and per-stage latency (ms)")
    console.print("\n".join(lines[:4] + lines[start:start + len(STAGES) + 6]))
    console.print("[green]Wrote results/benchmark.md, results/benchmark.csv, results/test_query_results.md")


if __name__ == "__main__":
    main()

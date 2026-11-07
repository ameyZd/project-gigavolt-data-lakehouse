#!/usr/bin/env python3
"""End-to-end latency benchmark with per-stage profiling (P50 / P95 / P99).

Runs every evaluation question ``--repeats`` times after ``--warmup`` un-timed passes and
measures each pipeline stage from question submission to the validated, cited answer.

Outputs in output/benchmark/:
  latency_raw.csv        one row per run, every stage in ms
  latency_summary.json   n, mean, std, min, P50, P95, P99, max per stage + throughput
  latency_report.md      tables for the write-up / video
  latency_profile.png    P50 & P95 per stage + end-to-end distribution vs the 5 s target

Usage:  python scripts/05_benchmark_latency.py [--repeats 3] [--warmup 1] [--provider groq]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))

from gigavolt_rag.config import OUTPUT_DIR, Settings  # noqa: E402
from gigavolt_rag.llm import LLMClient  # noqa: E402
from gigavolt_rag.pipeline import RAGPipeline  # noqa: E402

STAGES = [
    ("retrieval.route", "Query routing"),
    ("retrieval.sql", "MariaDB SQL"),
    ("retrieval.document", "MongoDB telemetry"),
    ("retrieval.vector_embed", "Query embedding"),
    ("retrieval.vector_search", "ChromaDB HNSW search"),
    ("retrieval.multi_hop", "Multi-hop vector hop"),
    ("retrieval.retrieval_total", "Retrieval total"),
    ("prompt_build", "Prompt assembly"),
    ("llm_generation", "LLM generation"),
    ("provenance_check", "Provenance check"),
    ("total", "End-to-end"),
]


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (same definition as numpy's default)."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * pct / 100.0
    low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else 0.0,
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values) if values else 0.0,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else 0.0,
    }


def draw_chart(summary: dict, totals_s: list[float], target_s: float, llm_label: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    p50_color, p95_color, target_color = "#2a78d6", "#104281", "#d03b3b"
    stages = [(key, label) for key, label in STAGES if key != "total" and summary.get(key, {}).get("n")]
    labels = [label for _, label in stages][::-1]
    p50 = [summary[key]["p50"] for key, _ in stages][::-1]
    p95 = [summary[key]["p95"] for key, _ in stages][::-1]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.6), gridspec_kw={"width_ratios": [1.45, 1]}, dpi=160)
    fig.patch.set_facecolor(surface)
    for ax in (ax1, ax2):
        ax.set_facecolor(surface)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(grid)
        ax.tick_params(colors=ink2, labelsize=9)

    y = range(len(labels))
    ax1.barh(y, p50, height=0.55, color=p50_color, label="P50 (median)", zorder=2)
    ax1.scatter(p95, y, marker="|", s=260, linewidths=2.2, color=p95_color, label="P95", zorder=3)
    ax1.set_xscale("symlog", linthresh=1)
    ax1.set_yticks(list(y), labels, color=ink)
    ax1.set_xlabel("milliseconds (symmetric log scale)", color=ink2, fontsize=9)
    ax1.grid(axis="x", color=grid, linewidth=0.8, zorder=0)
    for yi, median, high in zip(y, p50, p95):
        ax1.text(max(median, high, 0.05) * 1.25 + 0.08, yi, f"{median:,.1f}", va="center", fontsize=8, color=ink2)
    ax1.legend(frameon=False, fontsize=9, loc="upper right", labelcolor=ink2)
    ax1.set_title("Where the time goes: per-stage latency", loc="left", fontsize=12, color=ink, fontweight="bold")

    upper = max(target_s * 1.15, max(totals_s) * 1.1)
    width = 0.25 if upper <= 8 else 0.5
    edges = [i * width for i in range(int(upper / width) + 2)]
    ax2.hist(totals_s, bins=edges, color=p50_color, edgecolor=surface, linewidth=1, zorder=2)
    ax2.axvline(target_s, color=target_color, linewidth=2, linestyle="--", zorder=3)
    ax2.text(target_s, ax2.get_ylim()[1] * 0.995, f" {target_s:g} s target", color=target_color, fontsize=9, va="top",
             bbox={"facecolor": surface, "edgecolor": "none", "pad": 1.5}, zorder=4)
    total = summary["total"]
    ax2.axvline(total["p50"] / 1000, color=ink2, linewidth=1.2, zorder=3)
    ax2.text(total["p50"] / 1000, ax2.get_ylim()[1] * 0.995, f" P50 {total['p50'] / 1000:.2f} s", color=ink, fontsize=9,
             va="top", bbox={"facecolor": surface, "edgecolor": "none", "pad": 1.5}, zorder=4)
    ax2.set_xlim(0, upper)
    ax2.set_xlabel("question -> complete cited answer (seconds)", color=ink2, fontsize=9)
    ax2.set_ylabel("runs", color=ink2, fontsize=9)
    ax2.grid(axis="y", color=grid, linewidth=0.8, zorder=0)
    ax2.set_title("End-to-end latency distribution", loc="left", fontsize=12, color=ink, fontweight="bold")

    fig.suptitle(f"Gigavolt hybrid RAG · {total['n']} runs · P50 {total['p50'] / 1000:.2f} s · "
                 f"P95 {total['p95'] / 1000:.2f} s · P99 {total['p99'] / 1000:.2f} s · {llm_label}",
                 x=0.01, ha="left", fontsize=10, color=ink2)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, facecolor=surface)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", type=Path, default=M5_ROOT / "tests" / "test_queries.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--provider", choices=["groq", "ollama", "anthropic", "openai", "extractive"])
    parser.add_argument("--include-edge-cases", action="store_true", help="also time the out-of-scope question")
    args = parser.parse_args()

    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    if not args.include_edge_cases:
        queries = [q for q in queries if "out-of-scope" not in q["category"]]
    settings = Settings.from_env()
    pipeline = RAGPipeline(settings, llm=LLMClient(settings, args.provider))
    cold_started = time.perf_counter()
    warm_status = pipeline.warmup()
    cold_start_s = time.perf_counter() - cold_started
    print(f"Cold start (connections + model load): {cold_start_s:.2f} s  {warm_status}")

    paced = settings.llm_min_interval_s > 0 and pipeline.llm.provider != "extractive"
    if paced:
        print(f"Pacing: {settings.llm_min_interval_s:g} s between LLM calls (free-tier rate limit). "
              "Waits happen BEFORE each timed question and are excluded from every latency number.")
    for _ in range(args.warmup):
        # With a rate-limited free tier, one warm-up question is enough to open the HTTPS connection.
        for query in (queries[:1] if paced else queries):
            pipeline.llm.cooldown()
            pipeline.answer(query["question"])
    print(f"Warm-up passes done ({args.warmup}). Timing {len(queries)} questions x {args.repeats} repeats ...")

    rows = []
    paused_s = 0.0
    wall_started = time.perf_counter()
    for repeat in range(1, args.repeats + 1):
        for query in queries:
            paused_s += pipeline.llm.cooldown()
            result = pipeline.answer(query["question"])
            row = {"repeat": repeat, "id": query["id"], "mode": result.mode,
                   "coverage_pct": round(result.provenance.coverage_pct, 1) if result.provenance else None}
            row.update({key: round(result.timings_ms.get(key, 0.0), 3) for key, _ in STAGES})
            rows.append(row)
            print(f"  r{repeat} {query['id']}: {row['total'] / 1000:.2f} s (retrieval {row['retrieval.retrieval_total']:.0f} ms,"
                  f" LLM {row['llm_generation']:.0f} ms) {result.mode}")
    wall_s = time.perf_counter() - wall_started - paused_s  # rate-limit pauses are not pipeline time

    summary = {key: summarize([r[key] for r in rows if key in r]) for key, _ in STAGES}
    llm_runs = [r for r in rows if r["mode"] == "llm"]
    if llm_runs:
        summary["llm_generation"] = summarize([r["llm_generation"] for r in llm_runs])
    totals = [r["total"] for r in rows]
    retrieval = [r["retrieval.retrieval_total"] for r in rows]
    generation = [r["llm_generation"] for r in rows]
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "llm": f"{pipeline.llm.provider} / {pipeline.llm.model}",
        "questions": len(queries),
        "repeats": args.repeats,
        "runs": len(rows),
        "cold_start_s": round(cold_start_s, 3),
        "throughput_qps_sequential": round(len(rows) / wall_s, 3),
        "under_target_pct": round(100.0 * sum(1 for t in totals if t < settings.latency_target_s * 1000) / len(totals), 1),
        "retrieval_share_of_e2e_pct": round(100.0 * sum(retrieval) / max(sum(totals), 1e-9), 1),
        "generation_share_of_e2e_pct": round(100.0 * sum(generation) / max(sum(totals), 1e-9), 1),
        "modes": {mode: sum(1 for r in rows if r["mode"] == mode) for mode in {r["mode"] for r in rows}},
        "target_s": settings.latency_target_s,
    }

    out = OUTPUT_DIR / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "latency_raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (out / "latency_summary.json").write_text(json.dumps({"meta": meta, "stages_ms": summary}, indent=2), encoding="utf-8")

    lines = [
        "# Milestone 5 latency benchmark", "",
        f"- LLM: `{meta['llm']}`  ·  {meta['questions']} questions × {meta['repeats']} repeats = **{meta['runs']} timed runs** "
        f"(after {args.warmup} warm-up pass)",
        f"- End-to-end **P50 {summary['total']['p50'] / 1000:.2f} s · P95 {summary['total']['p95'] / 1000:.2f} s · "
        f"P99 {summary['total']['p99'] / 1000:.2f} s** (target < {settings.latency_target_s:g} s); "
        f"{meta['under_target_pct']}% of runs under target",
        f"- Retrieval share of end-to-end time: {meta['retrieval_share_of_e2e_pct']}% · LLM generation share: "
        f"{meta['generation_share_of_e2e_pct']}%",
        f"- Sequential throughput: {meta['throughput_qps_sequential']} questions/s · one-time cold start "
        f"{meta['cold_start_s']} s (excluded)",
        "", "| Stage | n | Mean ms | P50 ms | P95 ms | P99 ms | Max ms |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in STAGES:
        s = summary[key]
        lines.append(f"| {label} | {s['n']} | {s['mean']:.1f} | {s['p50']:.1f} | {s['p95']:.1f} | {s['p99']:.1f} | {s['max']:.1f} |")
    lines += ["", "Notes: Stage 1 runs MariaDB, MongoDB and ChromaDB in parallel threads, so retrieval total is "
              "less than the sum of the three source timings. With "
              f"{meta['runs']} runs, P99 is close to the maximum. "
              "Embedding-model load and DB connections are a one-time warm-up cost.", "",
              "![latency profile](latency_profile.png)", ""]
    (out / "latency_report.md").write_text("\n".join(lines), encoding="utf-8")
    draw_chart(summary, [t / 1000 for t in totals], settings.latency_target_s, meta["llm"], out / "latency_profile.png")

    print("\n" + "\n".join(lines[:6]))
    print(f"\nSaved to {out}/: latency_raw.csv, latency_summary.json, latency_report.md, latency_profile.png")
    pipeline.retriever.close()


if __name__ == "__main__":
    main()

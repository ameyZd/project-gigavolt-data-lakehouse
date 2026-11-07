"""Terminal and Markdown renderers for a RAGResult (used by ask.py, test runner and the video demo)."""

from __future__ import annotations

import os
import sys
import textwrap

from .pipeline import RAGResult
from .sources.base import SOURCE_LABEL

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
COLORS = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36"}
SOURCE_COLOR = {"sql": "blue", "document": "magenta", "vector": "cyan"}
STAGES = [
    ("retrieval.route", "Query routing"),
    ("retrieval.sql", "MariaDB SQL"),
    ("retrieval.document", "MongoDB telemetry"),
    ("retrieval.vector_embed", "Query embedding (MiniLM)"),
    ("retrieval.vector_search", "ChromaDB HNSW search"),
    ("retrieval.stage1_parallel", "Stage 1 wall time (parallel)"),
    ("retrieval.multi_hop", "Multi-hop vector expansion"),
    ("retrieval.retrieval_total", "RETRIEVAL TOTAL"),
    ("prompt_build", "Prompt assembly"),
    ("llm_generation", "LLM GENERATION"),
    ("provenance_check", "Provenance validation"),
    ("total", "END-TO-END"),
]


def c(text: str, *styles: str) -> str:
    if not USE_COLOR:
        return text
    codes = ";".join(COLORS[s] for s in styles if s in COLORS)
    return f"\033[{codes}m{text}\033[0m"


def rule(title: str) -> str:
    return c(f"\n━━ {title} " + "━" * max(4, 88 - len(title)), "bold")


def wrap(text: str, indent: str = "   ", width: int = 110) -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def render_terminal(result: RAGResult, show_context: bool = False, target_s: float = 5.0) -> str:
    out: list[str] = [rule("QUESTION"), wrap(result.question)]
    retrieval = result.retrieval
    if retrieval:
        plan = retrieval.plan
        out.append(rule("1. QUERY ROUTING"))
        out.append(f"   intents : {', '.join(plan.intents)}")
        ents = {k: v for k, v in plan.to_dict()["entities"].items() if v}
        out.append(f"   entities: {ents if ents else '{}'}")
        used = [name for name, flag in (("MariaDB", plan.use_sql), ("MongoDB", plan.use_document),
                                        ("ChromaDB", plan.use_vector)) if flag]
        out.append(f"   sources : {', '.join(used)}")
        for line in plan.rationale:
            out.append(c(wrap("• " + line, "     "), "dim"))

        out.append(rule("2. HYBRID RETRIEVAL (context assembled for the LLM)"))
        for name in ("sql", "document", "vector"):
            source = retrieval.sources[name]
            color = SOURCE_COLOR[name]
            label = SOURCE_LABEL[name]
            if source.skipped_reason:
                out.append(c(f"   {label:<26} skipped - {source.skipped_reason}", "dim"))
                continue
            status = c("ERROR " + (source.error or ""), "red") if source.error and not source.items else ""
            out.append(c(f"   {label:<26} {len(source.items)} record(s) in {source.elapsed_ms:7.1f} ms {status}", color, "bold"))
            for item in source.items:
                marker = c(" (absence)", "yellow") if item.kind == "absence" else ""
                score = f" sim={item.score:.3f}" if item.score is not None else ""
                out.append(f"     [{c(item.citation_id, color, 'bold')}] {item.citation_line()}{score}{marker}")
                if show_context:
                    out.append(c(wrap(item.text[:600], "          "), "dim"))
            hop = source.details.get("multi_hop") if source.details else None
            if hop:
                out.append(c(f"     multi-hop terms {hop['terms']} added {hop['added'] or 'no new chunks'}", "dim"))

    llm_label = f"{result.llm.provider} / {result.llm.model}" if result.llm else "n/a"
    out.append(rule(f"3. GENERATED ANSWER  [{result.mode} | {llm_label}]"))
    for line in result.answer.splitlines():
        out.append(wrap(line) if line.strip() else "")

    report = result.provenance
    if report is not None and report.statements:
        out.append(rule("4. PROVENANCE CHECK (statement -> source records)"))
        for index, statement in enumerate(report.statements, start=1):
            ok = statement.cited and not statement.invalid_citations
            mark = c("✔", "green") if ok else c("⚠", "yellow")
            short = statement.text if len(statement.text) < 95 else statement.text[:92] + "..."
            out.append(f"   {mark} {index:>2}. {short}")
            if statement.invalid_citations:
                out.append(c(f"         invalid tags (not in context): {statement.invalid_citations}", "red"))
            if statement.unsupported_numbers:
                out.append(c(f"         numbers not found in cited records: {statement.unsupported_numbers}", "yellow"))
        out.append("   Sources cited:")
        for tag, meta in report.sources.items():
            out.append(f"     [{tag}] {meta['citation']}")
        cov = report.coverage_pct
        cov_text = c(f"{cov:.0f}%", "green" if cov == 100 else "yellow")
        out.append(f"   Citation coverage: {cov_text} of {len(report.statements)} statements | invalid tags: "
                   f"{len(report.invalid_citations)} | numeric grounding: {report.numeric_grounding_pct:.0f}%")

    out.append(rule("5. LATENCY PROFILE"))
    for key, label in STAGES:
        if key in result.timings_ms:
            value = result.timings_ms[key]
            bold = ("bold",) if label.isupper() else ()
            out.append(c(f"   {label:<32} {value:9.1f} ms", *bold))
    total_s = result.total_ms / 1000
    verdict = c(f"{total_s:.2f} s  ✔ under {target_s:g} s target", "green", "bold") if total_s < target_s else \
        c(f"{total_s:.2f} s  ✘ over {target_s:g} s target", "red", "bold")
    out.append(f"   Question -> complete answer      {verdict}")
    if result.llm and result.llm.input_tokens:
        out.append(c(f"   tokens in/out: {result.llm.input_tokens}/{result.llm.output_tokens}", "dim"))
    for warning in result.warnings:
        out.append(c(f"   ! {warning}", "yellow"))
    return "\n".join(out)


def render_markdown(result: RAGResult, title: str | None = None) -> str:
    lines = [f"# {title or result.question}", "", f"**Question:** {result.question}", ""]
    llm = f"{result.llm.provider} / {result.llm.model}" if result.llm else "n/a"
    lines += [f"**Mode:** {result.mode} &nbsp;|&nbsp; **LLM:** {llm} &nbsp;|&nbsp; "
              f"**End-to-end latency:** {result.total_ms / 1000:.2f} s", ""]
    if result.retrieval:
        plan = result.retrieval.plan
        lines += ["## Routing", "", f"- Intents: {', '.join(plan.intents)}"]
        lines += [f"- {r}" for r in plan.rationale]
        lines.append("")
    lines += ["## Answer", "", result.answer, ""]
    report = result.provenance
    if report and report.statements:
        lines += ["## Provenance map (statement → source records)", "",
                  "| # | Statement | Citations | Check |", "|---|---|---|---|"]
        for index, s in enumerate(report.statements, start=1):
            check = "✔" if s.cited and not s.invalid_citations else "⚠ uncited/invalid"
            if s.unsupported_numbers:
                check += f" (numbers not in source: {', '.join(s.unsupported_numbers)})"
            text = s.text.replace("|", "\\|")
            lines.append(f"| {index} | {text} | {', '.join(s.citations) or '—'} | {check} |")
        lines += ["", f"Citation coverage **{report.coverage_pct:.0f}%**, invalid tags **{len(report.invalid_citations)}**, "
                      f"numeric grounding **{report.numeric_grounding_pct:.0f}%**.", ""]
        lines += ["## Cited sources", "", "| Tag | Database | Record ID(s) | Document | Timestamp |", "|---|---|---|---|---|"]
        for tag, meta in report.sources.items():
            ids = ", ".join(meta["record_ids"][:5]) + (" …" if len(meta["record_ids"]) > 5 else "")
            doc = f"{meta['document_name']} p.{meta['page']}" if meta.get("document_name") else "—"
            lines.append(f"| {tag} | {meta['database_type']} · {meta['collection']} | {ids} | {doc} | {meta.get('timestamp') or '—'} |")
        lines.append("")
    if result.retrieval:
        lines += ["## All retrieved context", "", "| Tag | Source | Kind | Title |", "|---|---|---|---|"]
        for item in result.retrieval.items:
            lines.append(f"| {item.citation_id} | {SOURCE_LABEL[item.source_type]} | {item.kind} | {item.title} |")
        lines.append("")
    lines += ["## Latency (ms)", "", "| Stage | ms |", "|---|---:|"]
    for key, label in STAGES:
        if key in result.timings_ms:
            lines.append(f"| {label} | {result.timings_ms[key]:.1f} |")
    if result.warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in result.warnings]
    return "\n".join(lines) + "\n"

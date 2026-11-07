"""Prompt template assembly (templates live in ``prompts/`` so they can be reviewed and versioned)."""

from __future__ import annotations

from string import Template

from .config import PROMPTS_DIR
from .retrieval import RetrievalResult
from .sources.base import SOURCE_LABEL

MAX_CONTEXT_CHARS = 14000


def load_template(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def source_inventory(retrieval: RetrievalResult) -> str:
    lines = []
    for name in ("sql", "document", "vector"):
        result = retrieval.sources[name]
        label = SOURCE_LABEL[name]
        if result.skipped_reason:
            lines.append(f"- {label}: skipped ({result.skipped_reason})")
        elif result.error and not result.items:
            lines.append(f"- {label}: UNAVAILABLE ({result.error[:120]}) - do not speculate about this source")
        else:
            cites = ", ".join(item.citation_id for item in result.items) or "no matching records"
            lines.append(f"- {label}: {len(result.items)} record(s) -> {cites}")
    lines.append("- Graph database: not part of this milestone (removed by instructor announcement)")
    return "\n".join(lines)


def build_context(retrieval: RetrievalResult, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    blocks, used = [], 0
    for item in retrieval.items:
        block = item.to_prompt_block()
        if used + len(block) > max_chars:
            blocks.append(f"[... {len(retrieval.items) - len(blocks)} lower-ranked records omitted to fit the budget ...]")
            break
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)


def build_prompt(retrieval: RetrievalResult) -> tuple[str, str]:
    """Return (system_prompt, user_prompt)."""
    system = load_template("system_prompt.txt")
    routing = "\n".join(f"- {line}" for line in retrieval.plan.rationale)
    user = Template(load_template("answer_template.txt")).substitute(
        question=retrieval.plan.question,
        routing=routing,
        source_inventory=source_inventory(retrieval),
        context=build_context(retrieval),
    )
    return system, user

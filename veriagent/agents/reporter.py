"""Report agent. In evidence-bound mode (C3a) the LLM writes placeholders such as
{{EV-003}} or {{pct_change:EV-001,EV-002}} and code fills in the numbers, so the
model never types a figure itself. In free mode (baselines) it writes numbers."""
from __future__ import annotations

import json
import re

from .. import prompts
from ..context import RunContext
from ..numbers import compute_op, format_value

MARK = "⁣"  # invisible separator wrapping numbers that came from placeholders
PH_RE = re.compile(r"\{\{\s*(?:([a-z_]+)\s*:\s*)?(EV-\d+(?:\s*,\s*EV-\d+)*)\s*\}\}", re.I)
CHART_RE = re.compile(r"\[\[\s*CHART\s*:\s*(T\d+)\s*\]\]", re.I)
PCT_OPS = {"pct_change", "share"}


def _evidence_block(ctx: RunContext) -> str:
    if ctx.mode.memory:
        return ctx.store.as_prompt()
    return "\n".join(ctx.results_text) or "(no results)"


def _charts_block(charts: list[dict]) -> str:
    return "\n".join(f"{c['task_id']}: {c['spec'].get('title', '')}" for c in charts) or "(no charts)"


def write(ctx: RunContext, tasks: list[dict], charts: list[dict]) -> str:
    task_list = "\n".join(f"{t['id']}: {t['question']}" for t in tasks)
    user = (f"User goal: {ctx.goal}\n\nTasks:\n{task_list}\n\nEvidence:\n{_evidence_block(ctx)}\n\n"
            f"Approved charts:\n{_charts_block(charts)}")
    if ctx.mode.evidence_bound:
        return ctx.llm.chat("reporter", prompts.REPORTER_BOUND, user)
    cite = (" After every number, cite the evidence id in brackets, e.g. [EV-003]."
            if ctx.mode.memory else "")
    return ctx.llm.chat("reporter_free", prompts.REPORTER_FREE.format(cite_rule=cite), user)


def render(ctx: RunContext, md: str, charts: list[dict]) -> tuple[str, list[dict]]:
    """Fill placeholders and chart slots. Returns (markdown_with_marks, bound_items)."""
    bound: list[dict] = []

    def fill(m: re.Match) -> str:
        op = (m.group(1) or "value").lower()
        ids = [i.strip().upper() for i in m.group(2).split(",")]
        recs = [ctx.store.get(i) for i in ids]
        if any(r is None for r in recs):
            bound.append({"ids": ids, "op": op, "value": None, "error": "unknown evidence id"})
            return f"{MARK}[missing evidence {', '.join(ids)}]{MARK}"
        val = compute_op(op, [r["value"] for r in recs])
        if val is None:
            bound.append({"ids": ids, "op": op, "value": None, "error": "cannot compute"})
            return f"{MARK}[invalid {op} on {', '.join(ids)}]{MARK}"
        if op == "ratio":
            text = f"{val:.2f}×"
        else:
            unit = "%" if op in PCT_OPS else recs[0]["unit"]
            text = format_value(abs(val) if op in ("pct_change", "diff") else val, unit)
        bound.append({"ids": ids, "op": op, "value": val, "text": text})
        return f"{MARK}{text}{MARK} [{', '.join(ids)}]"

    out = PH_RE.sub(fill, md)
    latest = {}
    for c in charts:
        if c.get("ok") and c.get("approved", True):
            latest[c["task_id"]] = c

    def chart(m: re.Match) -> str:
        c = latest.get(m.group(1).upper())
        if not c:
            return ""
        return f"![{c['spec'].get('title', c['task_id'])}]({c['file']})"

    out = CHART_RE.sub(chart, out)
    # make sure every approved chart appears even if the writer forgot one
    missing = [c for tid, c in latest.items() if c["file"] not in out]
    if missing:
        extra = "\n\n".join(f"![{c['spec'].get('title', c['task_id'])}]({c['file']})" for c in missing)
        if "## Charts" in out:
            out = out.replace("## Charts", "## Charts\n\n" + extra, 1)
        else:
            out += "\n\n## Charts\n\n" + extra
    return out, bound


def repair(ctx: RunContext, sentences: list[dict]) -> dict[int, str]:
    """Ask the writer to rewrite unsupported sentences with placeholders."""
    user = (f"Evidence:\n{_evidence_block(ctx)}\n\nSentences to fix:\n"
            + json.dumps([{"sid": s["sid"], "sentence": s["text"]} for s in sentences], ensure_ascii=False))
    reply = ctx.llm.chat("reporter_repair", prompts.REPORTER_REPAIR, user, json=True)
    out = {}
    for r in (reply or {}).get("rewrites", []) if isinstance(reply, dict) else []:
        try:
            out[int(r["sid"])] = str(r["sentence"])
        except (KeyError, ValueError, TypeError):
            continue
    return out

"""Baseline B1: one LLM in one conversation writes the analysis code, sees the
results, chooses a chart and writes the report. No planner, critic, memory or
verifier. Uses the same sandbox and chart renderer so only the agent design differs."""
from __future__ import annotations

from .. import prompts
from ..context import RunContext
from ..sandbox import run_code
from .visualizer import _apply_rules, draw


def run_single(ctx: RunContext) -> tuple[str, list[dict], dict]:
    history = []
    user = f"Goal: {ctx.goal}\n\nDataset:\n{ctx.schema}"
    reply = ctx.llm.chat("single_code", prompts.SINGLE_CODE, user, json=True)
    code = reply.get("code", "") if isinstance(reply, dict) else ""
    result = {"ok": False, "error": "no code"}
    for attempt in range(ctx.settings.max_retries + 1):
        result = run_code(code, ctx.df, timeout=ctx.settings.sandbox_timeout)
        history += [{"role": "user", "content": user}, {"role": "assistant", "content": f'{{"code": {code!r}}}'}]
        if result["ok"] or attempt == ctx.settings.max_retries:
            break
        user = f"The code failed: {result.get('error')}\nFix it. Reply as JSON: {{\"code\": \"...\"}}"
        reply = ctx.llm.chat("single_code", prompts.SINGLE_CODE, user, json=True, history=history)
        code = reply.get("code", "") if isinstance(reply, dict) else ""

    info = {"task_id": "T1", "question": ctx.goal, "ok": result["ok"], "code": code,
            "error": result.get("error")}
    if result["ok"]:
        for f in result["findings"]:
            unit = f" {f['unit']}" if f["unit"] else ""
            ctx.results_text.append(f"- {f['statement']}: {f['value']}{unit}")
        if result.get("table"):
            ctx.tables["T1"] = result["table"]
    results = "\n".join(ctx.results_text) or f"(code failed: {result.get('error')})"
    table_cols = ctx.tables["T1"]["columns"] if "T1" in ctx.tables else []
    out = ctx.llm.chat("single_report", prompts.SINGLE_REPORT,
                       f"Results:\n{results}\n\nChart table columns: {table_cols}", json=True,
                       history=history)
    out = out if isinstance(out, dict) else {}
    charts = []
    if "T1" in ctx.tables and isinstance(out.get("chart"), dict):
        spec = _apply_rules(out["chart"], ctx.tables["T1"])
        c = draw(ctx, {"id": "T1"}, spec, ctx.tables["T1"], 1)
        c["approved"] = c["ok"]
        charts.append(c)
    return str(out.get("report", "")), charts, info

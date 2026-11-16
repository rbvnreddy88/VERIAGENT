"""Orchestrator / planner agent: goal -> ordered analysis tasks."""
from __future__ import annotations

from .. import prompts
from ..context import RunContext


def plan(ctx: RunContext) -> list[dict]:
    if not ctx.mode.planner:
        return [{"id": "T1", "question": ctx.goal, "chart": True}]
    reply = ctx.llm.chat(
        "planner",
        prompts.PLANNER.format(max_tasks=ctx.settings.max_tasks),
        f"Goal: {ctx.goal}\n\nDataset:\n{ctx.schema}",
        json=True,
    )
    tasks = reply.get("tasks", []) if isinstance(reply, dict) else []
    clean = []
    for i, t in enumerate(tasks[: ctx.settings.max_tasks]):
        q = str(t.get("question", "")).strip()
        if q:
            clean.append({"id": f"T{i + 1}", "question": q, "chart": bool(t.get("chart", False))})
    if not clean:
        clean = [{"id": "T1", "question": ctx.goal, "chart": True}]
    return clean

"""Visualization agent: chooses a chart spec for an evidence table, applies
chart-type rules, and renders it with the fixed template."""
from __future__ import annotations

import json

import pandas as pd

from .. import prompts
from ..charts import ChartError, render, table_to_df
from ..context import RunContext


def _table_preview(table: dict, n: int = 12) -> str:
    df = table_to_df(table)
    return f"Columns: {list(df.columns)}\nRows: {len(df)}\n{df.head(n).to_string(index=False)}"


def _apply_rules(spec: dict, table: dict) -> dict:
    """Light rule-based guard rails on top of the LLM's choice."""
    df = table_to_df(table)
    cols = list(df.columns)
    spec = dict(spec)
    if spec.get("y") in (None, "", "null") and spec.get("type") != "hist":
        num = [c for c in cols if pd.api.types.is_numeric_dtype(df[c]) and c != spec.get("x")]
        spec["y"] = num[0] if num else None
    if spec.get("x") not in cols and cols:
        spec["x"] = cols[0]
    x = spec.get("x")
    if spec.get("type") in ("bar", "barh") and x in cols:
        sx = df[x]
        looks_time = pd.api.types.is_datetime64_any_dtype(sx) or str(x).lower() in (
            "date", "year", "month", "week", "day", "period", "quarter") or (
            (pd.api.types.is_string_dtype(sx) or sx.dtype == object) and pd.to_datetime(sx, errors="coerce", format="mixed").notna().mean() > 0.9
            and len(sx) > 12)
        if looks_time and len(df) > 12:
            spec["type"] = "line"
        elif len(df) > 12:
            spec["type"] = "barh"
            spec["top_n"] = spec.get("top_n") or 15
            spec["sort"] = spec.get("sort") if spec.get("sort") in ("asc", "desc") else "desc"
    if spec.get("type") not in ("bar", "barh", "line", "scatter", "hist"):
        spec["type"] = "bar"
    return spec


def propose_spec(ctx: RunContext, task: dict, table: dict, feedback: dict | None = None,
                 previous: dict | None = None) -> dict:
    user = f"Task {task['id']}: {task['question']}\n\nTable:\n{_table_preview(table)}"
    if feedback:
        user += (f"\n\nPrevious spec:\n{json.dumps(previous)}\n\nThe chart critic asked for changes:\n"
                 f"Issues: {feedback.get('issues')}\nFix: {feedback.get('fix')}\n"
                 "Return a corrected spec.")
    spec = ctx.llm.chat("visualizer", prompts.VISUALIZER, user, json=True)
    if not isinstance(spec, dict):
        spec = {}
    return _apply_rules(spec, table)


def draw(ctx: RunContext, task: dict, spec: dict, table: dict, version: int) -> dict:
    """Render a spec. Returns a chart record."""
    path = ctx.run_dir / "charts" / f"{task['id']}_v{version}.png"
    try:
        png = render(spec, table)
        path.write_bytes(png)
        return {"task_id": task["id"], "spec": spec, "file": f"charts/{path.name}", "ok": True,
                "version": version}
    except (ChartError, Exception) as e:  # renderer errors go back to the critic loop
        return {"task_id": task["id"], "spec": spec, "file": None, "ok": False,
                "error": f"{type(e).__name__}: {e}", "version": version}

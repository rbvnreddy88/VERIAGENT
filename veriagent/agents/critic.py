"""C2: Multimodal chart critic. Rule-based pre-checks, then a vision LLM reviews
the RENDERED image against a fixed checklist and the underlying data."""
from __future__ import annotations

import json

import pandas as pd

from .. import prompts
from ..charts import plotted_data, table_to_df
from ..context import RunContext


def precheck(chart: dict, table: dict) -> list[str]:
    issues = []
    if not chart.get("ok"):
        return [f"Chart failed to render: {chart.get('error')}"]
    spec = chart["spec"]
    try:
        d = plotted_data(spec, table)
    except Exception as e:  # noqa: BLE001
        return [f"Spec does not match table: {e}"]
    if len(d) < 2 and spec.get("type") != "hist":
        issues.append("Only one data point: a chart adds little; use a table or more categories")
    if not spec.get("title"):
        issues.append("Missing title")
    if spec.get("type") == "bar" and len(d) > 15:
        issues.append("Too many bars: use barh with top_n or sort")
    if spec.get("type") == "line" and spec.get("x") in d and (pd.api.types.is_object_dtype(d[spec["x"]]) or pd.api.types.is_string_dtype(d[spec["x"]])):
        if pd.to_datetime(d[spec["x"]], errors="coerce", format="mixed").isna().mean() > 0.5:
            issues.append("Line chart over unordered categories: use bar")
    return issues


def data_summary(chart: dict, table: dict) -> str:
    spec = chart["spec"]
    d = plotted_data(spec, table)
    y = spec.get("y")
    lines = [f"Spec: {json.dumps(spec)}", f"Points plotted: {len(d)}"]
    if y and y in d:
        yy = pd.to_numeric(d[y], errors="coerce")
        lines.append(f"{y}: min={yy.min():.4g}, max={yy.max():.4g}, negatives={int((yy < 0).sum())}")
    lines.append("First rows:\n" + table_to_df(table).head(8).to_string(index=False))
    return "\n".join(lines)


def critique(ctx: RunContext, task: dict, chart: dict, table: dict) -> dict:
    issues = precheck(chart, table)
    if issues:
        return {"verdict": "revise", "issues": issues, "fix": "; ".join(issues),
                "readability": 2, "source": "rules"}
    png = (ctx.run_dir / chart["file"]).read_bytes()
    reply = ctx.llm.chat(
        "critic", prompts.CRITIC,
        f"Task: {task['question']}\n\nData summary:\n{data_summary(chart, table)}",
        json=True, image_png=png)
    if not isinstance(reply, dict):
        reply = {}
    verdict = "approve" if str(reply.get("verdict", "")).lower().startswith("approve") else "revise"
    return {"verdict": verdict, "issues": reply.get("issues", []) or [], "fix": reply.get("fix", ""),
            "readability": int(reply.get("readability", 3) or 3), "source": "vision"}

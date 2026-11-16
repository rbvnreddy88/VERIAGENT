"""Analysis agent: writes pandas code per task, runs it in the sandbox, repairs
errors, and stores every computed value in the evidence memory (C1)."""
from __future__ import annotations

from .. import prompts
from ..context import RunContext
from ..sandbox import run_code


def analyze_task(ctx: RunContext, task: dict) -> dict:
    user = (f"Dataset:\n{ctx.schema}\n\nTask {task['id']}: {task['question']}\n"
            f"Chart requested: {'yes' if task.get('chart') else 'no'}")
    reply = ctx.llm.chat("analyst", prompts.ANALYST, user, json=True)
    code = (reply or {}).get("code", "") if isinstance(reply, dict) else ""
    attempts = []
    result = {"ok": False, "error": "no code returned"}
    for attempt in range(ctx.settings.max_retries + 1):
        result = run_code(code, ctx.df, timeout=ctx.settings.sandbox_timeout)
        attempts.append({"code": code, "ok": result["ok"], "error": result.get("error")})
        if result["ok"]:
            break
        if attempt == ctx.settings.max_retries:
            break
        ctx.event("analysis", "retry", task=task["id"], error=result.get("error"))
        fix = ctx.llm.chat(
            "analyst_repair", prompts.ANALYST + "\n\n" + prompts.ANALYST_REPAIR,
            f"{user}\n\nPrevious code:\n{code}\n\nError:\n{result.get('error')}\n{result.get('trace', '')}",
            json=True)
        code = (fix or {}).get("code", "") if isinstance(fix, dict) else ""

    out = {"task_id": task["id"], "question": task["question"], "ok": result["ok"],
           "attempts": len(attempts), "code": code, "evidence_ids": [], "error": result.get("error")}
    if not result["ok"]:
        return out
    for f in result["findings"]:
        if ctx.mode.memory:
            eid = ctx.store.add(f["statement"], f["value"], f["unit"], code, task["id"], ctx.ds_hash)
            out["evidence_ids"].append(eid)
        else:
            unit = f" {f['unit']}" if f["unit"] else ""
            ctx.results_text.append(f"- {f['statement']}: {f['value']}{unit}")
    if result.get("table"):
        ctx.tables[task["id"]] = result["table"]
    out["has_table"] = bool(result.get("table"))
    return out

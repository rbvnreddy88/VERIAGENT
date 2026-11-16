"""LangGraph workflow wiring all agents, with the critic and verifier loops.

    plan -> analyze -> visualize <-> critique -> write -> verify (-> repair -> verify) -> finalize

Modes (see config.MODES) switch components off for baselines and ablations."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .agents import analyst, critic, planner, reporter, single, verifier, visualizer
from .config import MODES, Settings
from .context import RunContext
from .report_html import to_html


class State(TypedDict, total=False):
    tasks: list
    analyses: list
    charts: dict          # task_id -> list of chart versions
    critic_round: int
    draft: str
    report: str
    bound: list
    log: list
    unsupported: list
    repaired: bool


def _latest(charts: dict) -> list[dict]:
    return [v[-1] for v in charts.values() if v]


def build_graph(ctx: RunContext):
    mode = ctx.mode
    g = StateGraph(State)

    # ---------------- nodes ----------------
    def n_plan(s: State) -> State:
        with ctx.timed("planner"):
            tasks = planner.plan(ctx)
        ctx.event("planner", "result", tasks=tasks)
        return {"tasks": tasks}

    def n_analyze(s: State) -> State:
        out = []
        with ctx.timed("analysis"):
            for t in s["tasks"]:
                a = analyst.analyze_task(ctx, t)
                out.append(a)
                ctx.event("analysis", "task", task=t["id"], ok=a["ok"], evidence=a["evidence_ids"])
        return {"analyses": out, "charts": {}, "critic_round": 0}

    def n_visualize(s: State) -> State:
        charts = {k: list(v) for k, v in (s.get("charts") or {}).items()}
        with ctx.timed("visualization"):
            for t in s["tasks"]:
                table = ctx.tables.get(t["id"])
                if not t.get("chart") or not table:
                    continue
                versions = charts.setdefault(t["id"], [])
                if versions and versions[-1].get("approved"):
                    continue
                prev = versions[-1] if versions else None
                spec = visualizer.propose_spec(ctx, t, table, feedback=prev.get("critic") if prev else None,
                                               previous=prev["spec"] if prev else None)
                c = visualizer.draw(ctx, t, spec, table, len(versions) + 1)
                if not mode.critic:
                    c["approved"] = c["ok"]
                versions.append(c)
                ctx.event("visualization", "chart", task=t["id"], file=c.get("file"), ok=c["ok"])
        return {"charts": charts}

    def n_critique(s: State) -> State:
        charts = {k: list(v) for k, v in s["charts"].items()}
        tasks = {t["id"]: t for t in s["tasks"]}
        with ctx.timed("critic"):
            for tid, versions in charts.items():
                c = versions[-1]
                if c.get("approved"):
                    continue
                verdict = critic.critique(ctx, tasks[tid], c, ctx.tables[tid])
                c = {**c, "critic": verdict, "approved": verdict["verdict"] == "approve"}
                versions[-1] = c
                ctx.event("critic", "verdict", task=tid, verdict=verdict["verdict"],
                          issues=verdict["issues"], version=c["version"])
        rnd = s.get("critic_round", 0) + 1
        if rnd > ctx.settings.max_retries:   # out of retries: keep the last renderable version
            for versions in charts.values():
                if not versions[-1].get("approved") and versions[-1]["ok"]:
                    versions[-1] = {**versions[-1], "approved": True, "forced": True}
        return {"charts": charts, "critic_round": rnd}

    def n_write(s: State) -> State:
        with ctx.timed("report"):
            draft = reporter.write(ctx, s["tasks"], [c for c in _latest(s.get("charts", {})) if c.get("approved")])
        return {"draft": draft}

    def n_render(s: State) -> State:
        md, bound = reporter.render(ctx, s["draft"], _latest(s.get("charts", {})))
        return {"report": md, "bound": bound, "log": []}

    def n_verify(s: State) -> State:
        with ctx.timed("verifier"):
            md, log, unsupported = verifier.verify(ctx, s["report"], s.get("bound", []))
        ctx.event("verifier", "summary", **verifier.summarize(log))
        return {"report": md, "log": log, "unsupported": unsupported}

    def n_repair(s: State) -> State:
        """Rewrite unsupported sentences with placeholders, then verify only those."""
        with ctx.timed("report_repair"):
            rewrites = reporter.repair(ctx, s["unsupported"])
            segs = verifier.segment(s["report"])
            fixed = {sid: txt for sid, txt in rewrites.items() if 0 <= sid < len(segs)}
            if not fixed:
                return {"repaired": True}
            mini = "\n".join(fixed[sid] for sid in sorted(fixed))
            mini, bound = reporter.render(ctx, mini, [])
            mini, log2, still = verifier.verify(ctx, mini, bound)
            new_lines = mini.split("\n")
            for sid, line in zip(sorted(fixed), new_lines):
                segs[sid]["text"] = line
            md = verifier.assemble(s["report"], segs)
            repaired_sents = {segs[sid]["text"] for sid in fixed}
            log = [e for e in s["log"] if not (e.get("status") in ("unsupported", "flagged")
                                               and e.get("sid") in fixed)]
            for e in s["log"]:
                if e.get("sid") in fixed and e.get("status") in ("unsupported", "flagged"):
                    log.append({**e, "status": "repaired", "detail": "rewritten by report agent"})
            log += [{**e, "after_repair": True} for e in log2]
        ctx.event("verifier", "repair", rewritten=len(fixed), still_unsupported=len(still))
        return {"report": md, "log": log, "unsupported": still, "repaired": True}

    def n_single(s: State) -> State:
        with ctx.timed("single_agent"):
            md, charts, info = single.run_single(ctx)
            md, bound = reporter.render(ctx, md, charts)
        return {"tasks": [{"id": "T1", "question": ctx.goal, "chart": True}], "analyses": [info],
                "charts": {"T1": charts} if charts else {}, "report": md, "bound": bound, "log": []}

    # ---------------- edges ----------------
    if mode.single_agent:
        g.add_node("single", n_single)
        g.set_entry_point("single")
        g.add_edge("single", END)
        return g.compile()

    g.add_node("plan", n_plan)
    g.add_node("analyze", n_analyze)
    g.add_node("visualize", n_visualize)
    g.add_node("write", n_write)
    g.add_node("render", n_render)
    g.set_entry_point("plan")
    g.add_edge("plan", "analyze")
    g.add_edge("analyze", "visualize")
    if mode.critic:
        g.add_node("critique", n_critique)
        g.add_edge("visualize", "critique")
        g.add_conditional_edges(
            "critique",
            lambda s: "visualize" if any(not c.get("approved") for c in _latest(s["charts"])) else "write",
            {"visualize": "visualize", "write": "write"})
    else:
        g.add_edge("visualize", "write")
    g.add_edge("write", "render")
    if mode.verifier:
        g.add_node("verify", n_verify)
        g.add_node("repair", n_repair)
        g.add_edge("render", "verify")
        g.add_conditional_edges(
            "verify",
            lambda s: "repair" if (s.get("unsupported") and mode.evidence_bound) else END,
            {"repair": "repair", END: END})
        g.add_edge("repair", END)
    else:
        g.add_edge("render", END)
    return g.compile()


# ---------------------------------------------------------------------------
def run(df, goal: str, mode: str = "full", settings: Settings | None = None,
        run_dir: Path | str | None = None, emit=None, namespace: str = "", llm=None) -> dict:
    """Run VeriAgent end to end. Returns a summary dict; artifacts are saved in run_dir."""
    settings = settings or Settings()
    m = MODES[mode]
    run_dir = Path(run_dir or settings.runs_dir / f"{int(time.time())}_{mode}")
    ctx = RunContext.create(settings, m, df, goal, run_dir, emit=emit, namespace=namespace or mode, llm=llm)
    ctx.event("pipeline", "start", mode=mode, provider=settings.provider)
    graph = build_graph(ctx)
    state: dict[str, Any] = graph.invoke({}, {"recursion_limit": 60})
    return finalize(ctx, state)


def finalize(ctx: RunContext, s: dict) -> dict:
    report = verifier.strip_marks(s.get("report", ""))
    log = s.get("log", [])
    summary = verifier.summarize(log) if ctx.mode.verifier else None
    charts = [c for c in _latest(s.get("charts", {})) if c.get("ok")]
    all_versions = [c for v in (s.get("charts") or {}).values() for c in v]
    elapsed = round(time.time() - ctx.started, 2)
    meta = {
        "run_id": ctx.run_id, "mode": ctx.mode.name, "goal": ctx.goal,
        "provider": ctx.settings.provider, "text_model": ctx.settings.text_model,
        "vision_model": ctx.settings.vision_model, "evidence_backend": ctx.store.backend,
        "seconds": elapsed, "timings": ctx.timings, "usage": ctx.llm.usage.as_dict(),
        "tasks": s.get("tasks", []), "analyses": s.get("analyses", []),
        "charts": charts, "chart_versions": all_versions,
        "verification_summary": summary, "dataset_hash": ctx.ds_hash,
        "repaired": bool(s.get("repaired")),
    }
    d = ctx.run_dir
    (d / "report.md").write_text(report, encoding="utf-8")
    (d / "report.html").write_text(to_html(report, meta, log), encoding="utf-8")
    (d / "verification.json").write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    ctx.store.save(d / "evidence.json")
    (d / "tables.json").write_text(json.dumps(ctx.tables, default=str), encoding="utf-8")
    (d / "results_text.txt").write_text("\n".join(ctx.results_text), encoding="utf-8")
    (d / "run.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    ctx.event("pipeline", "done", seconds=elapsed, summary=summary)
    return {**meta, "report": report, "verification_log": log, "run_dir": str(d)}

"""Run every configuration on the 50-task benchmark and score it.

    python eval/run_experiments.py                      # all 6 configs x 3 runs x 50 tasks
    python eval/run_experiments.py --configs full B1 --runs 1 --tasks R01 R02
    python eval/run_experiments.py --no-audit           # skip claim auditing (faster)

Results go to results/<config>/run<k>/task_<id>.json (+ an artifacts folder per task).
The script resumes: finished tasks are skipped, so you can stop and restart it.
Then run:  python eval/compute_paper_numbers.py results
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from benchmark import TASKS  # noqa: E402
from scoring import audit_claims, score_charts, task_success  # noqa: E402

from veriagent.config import MODES, Settings  # noqa: E402
from veriagent.llm import LLM  # noqa: E402
from veriagent.pipeline import run  # noqa: E402
from veriagent.profiling import load_csv, schema_text  # noqa: E402

DATA = ROOT / "eval" / "datasets"
ALL_CONFIGS = ["B1", "B2", "full", "no_verifier", "no_critic", "no_memory"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", default=ALL_CONFIGS, choices=list(MODES))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tasks", nargs="+", default=None, help="task ids (default: all 50)")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--no-audit", action="store_true", help="skip independent claim auditing")
    args = ap.parse_args()

    settings = Settings()
    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks]
    frames = {}
    print(f"Provider: {settings.provider} | text: {settings.text_model} | vision: {settings.vision_model}")
    if settings.provider == "mock":
        print("WARNING: mock LLM. Numbers from this run are NOT research results.")

    for k in range(args.runs):
        for cfg in args.configs:
            judge = LLM(settings, namespace=f"judge-run{k}")
            auditor = LLM(settings, namespace=f"auditor-run{k}")
            for t in tasks:
                out_dir = Path(args.out) / cfg / f"run{k}"
                out_file = out_dir / f"task_{t['id']}.json"
                if out_file.exists():
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                if t["dataset"] not in frames:
                    frames[t["dataset"]] = load_csv(DATA / t["dataset"])
                df = frames[t["dataset"]]
                art = out_dir / f"task_{t['id']}"
                t0 = time.time()
                try:
                    r = run(df, t["question"], cfg, settings, run_dir=art, namespace=f"{cfg}-run{k}")
                except Exception as e:  # a crash counts as a failed task, and is logged
                    rec = {"task_id": t["id"], "config": cfg, "run": k, "crashed": True,
                           "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:],
                           "task_correct": False, "claims": [],
                           "charts": [{"viseval_pass": False}] if t["chart"] else [],
                           "time_sec": round(time.time() - t0, 2), "tokens": 0}
                    out_file.write_text(json.dumps(rec, indent=2), encoding="utf-8")
                    print(f"[{cfg} run{k}] {t['id']} CRASH {e}")
                    continue

                answers = {kk: float(v) for kk, v in t["answers"](df).items()}
                ok, found = task_success(r["report"], answers)
                charts = []
                if t["chart"]:
                    ref_df = t["chart"]["ref"](df) if "ref" in t["chart"] else None
                    tables = _tables(art)
                    charts = score_charts(art, r, t["chart"], ref_df, judge, tables)
                claims = [] if args.no_audit else audit_claims(r["report"], df, schema_text(df), auditor)
                rec = {"task_id": t["id"], "config": cfg, "run": k, "question": t["question"],
                       "task_correct": ok, "answers": answers, "found": found,
                       "claims": claims, "charts": charts, "time_sec": r["seconds"],
                       "tokens": r["usage"]["total_tokens"], "llm_calls": r["usage"]["calls"],
                       "verification_summary": r.get("verification_summary")}
                out_file.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
                n_ok = sum(c["status"] == "correct" for c in claims)
                print(f"[{cfg} run{k}] {t['id']} task={'OK ' if ok else 'BAD'} "
                      f"chart={'-' if not charts else ('PASS' if charts[0]['viseval_pass'] else 'FAIL')} "
                      f"claims={n_ok}/{len(claims)} {r['seconds']}s")
    print("\nDone. Now run: python eval/compute_paper_numbers.py", args.out)


def _tables(art: Path) -> dict:
    """Recover chart tables saved with the run (written by save_tables in the pipeline)."""
    p = art / "tables.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


if __name__ == "__main__":
    main()

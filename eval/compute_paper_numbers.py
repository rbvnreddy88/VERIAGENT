"""
compute_paper_numbers.py
Computes every XX placeholder in main.tex from your saved experiment logs.

Expected log layout (one JSON file per task per run):
  results/<config>/run<k>/task_<id>.json
configs: B1, B2, full, no_verifier, no_critic, no_memory

Each task JSON must look like:
{
  "task_id": 7,
  "task_correct": true,                 # final answer matches your pandas ground truth
  "claims": [                           # every numeric claim in the FINAL report
     {"text": "...", "status": "correct"},        # correct | wrong | unsupported
     ...
  ],
  "charts": [ {"viseval_pass": true}, ... ],      # one entry per chart
  "time_sec": 41.2,
  "tokens": 18342
}

Error-injection test (Section V-F):
  results/injection.json -> {"injected": 20, "detected": 17, "corrected": 15}

Usage:  python eval/compute_paper_numbers.py results
"""
import json, sys, glob, os
from statistics import mean, stdev

ROOT = sys.argv[1] if len(sys.argv) > 1 else "results"
CONFIGS = ["B1", "B2", "full", "no_verifier", "no_critic", "no_memory"]


def run_metrics(run_dir):
    files = glob.glob(os.path.join(run_dir, "task_*.json"))
    if not files:
        return None
    tot = corr = wrong = unsup = 0
    charts = passed = tasks_ok = 0
    times, tokens = [], []
    for f in files:
        d = json.load(open(f))
        for c in d.get("claims", []):
            st = c.get("manual_status") or c["status"]   # human label overrides the auditor
            if st == "skip":
                continue
            tot += 1
            corr += st == "correct"
            wrong += st == "wrong"
            unsup += st == "unsupported"
        for ch in d.get("charts", []):
            charts += 1
            passed += bool(ch["viseval_pass"])
        tasks_ok += bool(d.get("task_correct"))
        times.append(d.get("time_sec", 0))
        tokens.append(d.get("tokens", 0))
    return {
        "FA": 100 * corr / tot if tot else 0,                 # factual accuracy
        "HR": 100 * (wrong + unsup) / tot if tot else 0,      # hallucination rate
        "CPR": 100 * passed / charts if charts else 0,        # chart pass rate
        "TSR": 100 * tasks_ok / len(files),                   # task success rate
        "Time": mean(times),
        "Tokens": mean(tokens),
    }


def config_metrics(cfg):
    runs = [m for m in (run_metrics(r) for r in sorted(glob.glob(os.path.join(ROOT, cfg, "run*")))) if m]
    if not runs:
        return None
    out = {}
    for k in runs[0]:
        vals = [r[k] for r in runs]
        out[k] = (mean(vals), stdev(vals) if len(vals) > 1 else 0.0)
    out["n_runs"] = len(runs)
    return out


M = {c: config_metrics(c) for c in CONFIGS}
missing = [c for c, v in M.items() if v is None]
if missing:
    print(f"!! No logs found for: {missing}  (those rows will be skipped)\n")


def f(cfg, key, nd=1):
    return f"{M[cfg][key][0]:.{nd}f}" if M.get(cfg) else "MISSING"


print("=" * 70)
print("TABLE II (main results) - paste over the three rows in main.tex")
print("=" * 70)
for cfg, label in [("B1", "B1: Single LLM  "), ("B2", "B2: LightVA-style"), ("full", "VeriAgent (ours)")]:
    if M.get(cfg):
        print(f"{label} & {f(cfg,'FA')} & {f(cfg,'HR')} & {f(cfg,'CPR')} & {f(cfg,'TSR')} & {f(cfg,'Time',0)} \\\\")

print("\n" + "=" * 70)
print("TABLE III (ablation)")
print("=" * 70)
for cfg, label in [("full", "Full VeriAgent                "), ("no_verifier", "\\quad -- Claim verifier       "),
                   ("no_critic", "\\quad -- Chart critic         "), ("no_memory", "\\quad -- Evidence memory      ")]:
    if M.get(cfg):
        print(f"{label} & {f(cfg,'FA')} & {f(cfg,'CPR')} \\\\")

print("\n" + "=" * 70)
print("NUMBERS FOR THE TEXT")
print("=" * 70)
if M.get("B1") and M.get("full"):
    print(f"Abstract : HR  B1 {f('B1','HR')}%  ->  VeriAgent {f('full','HR')}%")
    print(f"Abstract : CPR B1 {f('B1','CPR')}%  ->  VeriAgent {f('full','CPR')}%")
if M.get("B2") and M.get("full"):
    print(f"Sec V-D  : FA full {f('full','FA')}%, B1 {f('B1','FA')}%, B2 {f('B2','FA')}%; HR {f('full','HR')}%; CPR {f('full','CPR')}%")
    print(f"Sec V-D  : time full {f('full','Time',0)} s vs B2 {f('B2','Time',0)} s")
    ovh = 100 * (M['full']['Time'][0] - M['B2']['Time'][0]) / M['B2']['Time'][0]
    print(f"Sec VI   : latency overhead vs B2 = {ovh:.0f}%")
if M.get("no_verifier"):
    print(f"Sec V-E  : FA without verifier = {f('no_verifier','FA')}%")
if M.get("no_critic"):
    print(f"Sec V-E  : CPR without critic  = {f('no_critic','CPR')}%")

inj = os.path.join(ROOT, "injection.json")
if os.path.exists(inj):
    d = json.load(open(inj))
    print(f"Sec V-F  : detected {d['detected']} of {d['injected']}, corrected {d['corrected']}")
    if "clean_numbers_checked" in d:
        print(f"Sec V-F  : false alarms on clean reports {d['false_alarms_on_clean']} of {d['clean_numbers_checked']} numbers")
else:
    print("Sec V-F  : results/injection.json not found")

print("\nStd-dev across runs (report as mean ± sd if a reviewer asks):")
for cfg, m in M.items():
    if m:
        print(f"  {cfg:12s} FA {m['FA'][0]:.1f}±{m['FA'][1]:.1f}  HR {m['HR'][0]:.1f}±{m['HR'][1]:.1f}  "
              f"CPR {m['CPR'][0]:.1f}±{m['CPR'][1]:.1f}  runs={m['n_runs']}")

"""Error-injection test of the claim verifier (paper Section V-F).

Takes final reports from the full system, corrupts N cited numbers (one per report),
runs the verifier on the corrupted text against that run's saved evidence, and counts
how many corruptions are detected and correctly restored. It also runs the verifier on
the clean reports to measure false alarms.

    python eval/injection_test.py --n 20
Writes results/injection.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from veriagent.agents.verifier import verify  # noqa: E402
from veriagent.config import MODES, Settings  # noqa: E402
from veriagent.evidence import EvidenceStore  # noqa: E402
from veriagent.llm import LLM  # noqa: E402
from veriagent.numbers import find_numbers, matches  # noqa: E402

CITED = re.compile(r"(\S+)\s\[(EV-\d+(?:,\s*EV-\d+)*)\]")
FACTORS = [1.25, 0.8, 1.5, 0.6, 1.15, 0.7, 2.0, 0.5]


def corrupt(text: str, rng: random.Random):
    """Change one cited number. Returns (new_text, original_value, corrupted_text) or None."""
    cands = []
    for m in CITED.finditer(text):
        nums = find_numbers(m.group(1))
        if nums and abs(nums[0]["value"]) > 0:
            cands.append((m, nums[0]))
    if not cands:
        return None
    m, n = rng.choice(cands)
    f = rng.choice(FACTORS)
    v = n["value"] * f
    dec = n["decimals"]
    s = f"{abs(v):,.{dec}f}" if ("," in n["text"] or abs(v) >= 1000) else f"{abs(v):.{dec}f}"
    new_num = f"{'-' if v < 0 else ''}{n['currency']}{s}{'%' if n['is_pct'] else ''}"
    token = m.group(1)
    new_token = token[:n["start"]] + new_num + token[n["end"]:]
    start = m.start(1)
    return text[:start] + new_token + text[start + len(token):], n["value"], new_num


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--config", default="full")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    settings = Settings()
    rng = random.Random(a.seed)
    dirs = sorted(Path(a.results).glob(f"{a.config}/run0/task_*/"))
    dirs = [d for d in dirs if (d / "report.md").exists() and (d / "evidence.json").exists()]
    rng.shuffle(dirs)
    llm = LLM(settings, namespace="injection")
    details, clean_checked, false_alarms = [], 0, 0
    for d in dirs:
        if len(details) >= a.n:
            break
        report = (d / "report.md").read_text(encoding="utf-8")
        store = EvidenceStore.load(d / "evidence.json")
        ctx = SimpleNamespace(store=store, llm=llm, settings=settings, mode=MODES["full"], results_text=[])
        res = corrupt(report, rng)
        if res is None:
            continue
        bad_text, original, new_num = res
        # clean pass: how many correct numbers does the verifier wrongly change or flag?
        _, clean_log, _ = verify(ctx, report, [])
        for e in clean_log:
            if e["kind"] == "free" and e["status"] != "skipped":
                clean_checked += 1
                false_alarms += e["status"] in ("corrected", "unsupported", "flagged")
        # corrupted pass
        _, log, _ = verify(ctx, bad_text, [])
        hit = next((e for e in log if e["kind"] == "free" and e.get("number") == new_num), None)
        detected = bool(hit and hit["status"] in ("corrected", "unsupported", "flagged"))
        restored = bool(hit and hit["status"] == "corrected" and hit.get("true") is not None
                        and matches(abs(original), abs(hit["true"]), 6, 0.01))
        details.append({"task": d.name, "original": original, "injected": new_num,
                        "status": hit["status"] if hit else "missed", "detected": detected,
                        "restored": restored})
        print(f"{d.name}: {original} -> {new_num}: {details[-1]['status']}")
    out = {"injected": len(details), "detected": sum(x["detected"] for x in details),
           "corrected": sum(x["restored"] for x in details),
           "clean_numbers_checked": clean_checked, "false_alarms_on_clean": false_alarms,
           "details": details}
    Path(a.results, "injection.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nDetected {out['detected']}/{out['injected']}, corrected {out['corrected']}; "
          f"false alarms on clean reports: {false_alarms}/{clean_checked}")


if __name__ == "__main__":
    main()

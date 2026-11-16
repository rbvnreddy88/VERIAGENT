"""Human check of the automatic claim labels (recommended for the paper).

1) Export a random sample of claims to CSV:
       python eval/claims_labels.py export --sample 0.2
2) Open results/claims_to_label.csv, fill `manual_status` with correct / wrong /
   unsupported / skip (skip = not a data claim). Leave blank to keep the auditor label.
3) Write the labels back and report agreement with the auditor:
       python eval/claims_labels.py apply
compute_paper_numbers.py then uses manual labels where present. Report the agreement
rate in the paper (e.g. "human labels agreed with the auditor on X% of sampled claims").
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def export(results: Path, sample: float, seed: int = 7):
    rows = []
    for f in sorted(results.glob("*/run*/task_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for i, c in enumerate(d.get("claims", [])):
            rows.append({"file": str(f.relative_to(results)), "claim_idx": i, "config": d.get("config"),
                         "task_id": d["task_id"], "sentence": c["sentence"], "number": c["number"],
                         "auditor_status": c["status"], "auditor_truth": c.get("truth"),
                         "manual_status": ""})
    random.Random(seed).shuffle(rows)
    rows = rows[: max(1, int(len(rows) * sample))] if rows else []
    out = results / "claims_to_label.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} claims to {out}")


def apply(results: Path):
    src = results / "claims_to_label.csv"
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    labelled = [r for r in rows if r["manual_status"].strip()]
    agree = sum(r["manual_status"].strip() == r["auditor_status"] for r in labelled)
    by_file: dict[str, list] = {}
    for r in labelled:
        by_file.setdefault(r["file"], []).append(r)
    for rel, rs in by_file.items():
        p = results / rel
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in rs:
            d["claims"][int(r["claim_idx"])]["manual_status"] = r["manual_status"].strip()
        p.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
    if labelled:
        print(f"Applied {len(labelled)} labels. Human-auditor agreement: {100 * agree / len(labelled):.1f}%")
    else:
        print("No labels filled in yet.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "apply"])
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--sample", type=float, default=0.2)
    a = ap.parse_args()
    (export if a.cmd == "export" else apply)(Path(a.results), *([a.sample] if a.cmd == "export" else []))

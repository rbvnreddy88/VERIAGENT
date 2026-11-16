"""Command-line entry point.

    python run.py --csv eval/datasets/retail_sales.csv --goal "Why did profit fall in 2023?"
    python run.py --csv mydata.csv --goal "..." --mode B1
"""
import argparse
import json

from veriagent.config import MODES, Settings
from veriagent.pipeline import run
from veriagent.profiling import load_csv


def main():
    ap = argparse.ArgumentParser(description="VeriAgent: verifiable multi-agent data analysis")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--mode", default="full", choices=list(MODES))
    ap.add_argument("--out", default=None, help="output folder (default runs/<time>_<mode>)")
    a = ap.parse_args()

    def emit(agent, status, data):
        if status in ("start", "done", "retry", "verdict", "summary", "repair", "error"):
            extra = {k: v for k, v in data.items() if k in ("seconds", "task", "verdict", "error",
                                                            "verified", "corrected", "unsupported")}
            print(f"  [{agent:13s}] {status:7s} {json.dumps(extra) if extra else ''}")

    s = Settings()
    print(f"VeriAgent | provider={s.provider} model={s.text_model} mode={a.mode}")
    r = run(load_csv(a.csv), a.goal, a.mode, s, run_dir=a.out, emit=emit)
    print("\n" + r["report"])
    print("\nVerification:", r["verification_summary"])
    print(f"Saved to {r['run_dir']}  (open report.html)")


if __name__ == "__main__":
    main()

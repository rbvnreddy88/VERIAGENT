"""Scoring: task success (key answers present), VisEval-style chart checks, and
independent claim auditing for factual accuracy / hallucination rate."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from veriagent import prompts  # noqa: E402
from veriagent.agents.verifier import CITE_RE, segment  # noqa: E402
from veriagent.charts import plotted_data  # noqa: E402
from veriagent.numbers import DECREASE_WORDS, find_numbers, matches  # noqa: E402
from veriagent.sandbox import run_code  # noqa: E402

SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}
SCALE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|bn|k|m|b)\b", re.I)


def clean_text(report: str) -> str:
    t = CITE_RE.sub(" ", report)
    t = t.replace("[unverified]", " ").replace("[direction contradicts data]", " ")
    return re.sub(r"!\[.*?\]\(.*?\)", " ", t)


def report_numbers(report: str) -> list[dict]:
    t = clean_text(report)
    nums = find_numbers(t)
    for m in SCALE_RE.finditer(t):  # "1.2 million" -> 1_200_000
        v = float(m.group(1).replace(",", "")) * SCALE[m.group(2).lower()]
        nums.append({"value": v, "decimals": 0, "is_pct": False, "text": m.group(0), "scaled": True,
                     "rel": 0.05})
    return nums


def value_present(v: float, nums: list[dict], rel_tol: float = 0.01) -> bool:
    for n in nums:
        tol = n.get("rel", rel_tol)
        cands = [v, abs(v)]
        if abs(v) <= 1.5 and n["is_pct"]:
            cands += [v * 100, abs(v) * 100]   # fraction reported as a percentage
        for c in cands:
            if matches(n["value"], c, n["decimals"], tol) or matches(abs(n["value"]), abs(c), n["decimals"], tol):
                return True
    return False


def task_success(report: str, answers: dict) -> tuple[bool, dict]:
    nums = report_numbers(report)
    found = {k: value_present(float(v), nums) for k, v in answers.items()}
    return all(found.values()), found


# ------------------------------------------------------------------ charts
def _key(v):
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:.4g}"
    except (TypeError, ValueError):
        s = str(v)
        return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s.strip().lower()


def chart_data_ok(spec: dict, table: dict, chart_ref: dict, ref_df: pd.DataFrame | None) -> bool:
    if "cols" in chart_ref:
        want = set(chart_ref["cols"])
        return {spec.get("x"), spec.get("y")} == want
    d = plotted_data(spec, table)
    x, y = spec.get("x"), spec.get("y")
    if spec.get("type") == "barh" and y in d and x in d:
        pass
    if x not in d or y not in d:
        return False
    got = {_key(a): float(b) for a, b in zip(d[x], pd.to_numeric(d[y], errors="coerce")) if pd.notna(b)}
    ref = {_key(a): float(b) for a, b in zip(ref_df.iloc[:, 0], ref_df.iloc[:, 1])}
    if not ref:
        return False
    for k, v in ref.items():
        if k not in got or not matches(got[k], v, 6, 0.02):
            return False
    return True


def readability(llm, png: bytes) -> int:
    r = llm.chat("readability_judge", prompts.READABILITY, "Rate this chart.", json=True, image_png=png)
    try:
        return int(r.get("readability", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def score_charts(run_dir: Path, meta: dict, chart_ref: dict, ref_df, judge_llm, tables: dict) -> list[dict]:
    """VisEval-style: valid (renders) + legal (type and data match the reference)
    + readable (judge score >= 3). The task passes if any final chart passes."""
    results = []
    for c in meta.get("charts", []):
        tbl = tables.get(c["task_id"])
        valid = bool(c.get("ok") and c.get("file") and (run_dir / c["file"]).exists())
        type_ok = c["spec"].get("type") in chart_ref["types"]
        data_ok = bool(valid and tbl and chart_data_ok(c["spec"], tbl, chart_ref, ref_df))
        read = readability(judge_llm, (run_dir / c["file"]).read_bytes()) if valid and type_ok and data_ok else 0
        results.append({"file": c.get("file"), "valid": valid, "type_ok": type_ok, "data_ok": data_ok,
                        "readability": read, "pass": valid and type_ok and data_ok and read >= 3})
    best = next((r for r in results if r["pass"]), results[0] if results else None)
    if best is None:
        best = {"file": None, "valid": False, "type_ok": False, "data_ok": False, "readability": 0, "pass": False}
    return [{**best, "viseval_pass": best["pass"]}]


# ------------------------------------------------------------------ claim audit
def audit_claims(report: str, df, schema: str, auditor_llm, timeout: int = 20) -> list[dict]:
    """Independent check of every numeric claim against the RAW data.
    Labels: correct | wrong | unsupported; numbers that are not data claims are skipped."""
    out = []
    for seg in segment(clean_text(report)):
        for m in find_numbers(seg["text"]):
            user = (f"Dataset:\n{schema}\n\nSentence: {seg['text']}\nHighlighted number: {m['text'].strip()}")
            r = auditor_llm.chat("auditor", prompts.AUDITOR, user, json=True)
            r = r if isinstance(r, dict) else {}
            claim = {"sentence": seg["text"], "number": m["text"].strip(), "stated": m["value"]}
            if not r.get("computable"):
                kind = str(r.get("type", "unsupported"))
                if kind == "not_a_claim":
                    continue
                out.append({**claim, "status": "unsupported", "truth": None})
                continue
            res = run_code(r.get("code", ""), df, timeout=timeout)
            if not res.get("ok"):
                out.append({**claim, "status": "unsupported", "truth": None, "audit_error": res.get("error")})
                continue
            truth = res["findings"][0]["value"]
            sent = seg["text"].lower()
            stated = m["value"]
            cands = [truth]
            if abs(truth) <= 1.5 and m["is_pct"]:
                cands.append(truth * 100)
            ok = any(matches(stated, c, m["decimals"], 0.01) or
                     (matches(abs(stated), abs(c), m["decimals"], 0.01) and
                      (any(w in sent for w in DECREASE_WORDS) or c >= 0 or m["is_pct"])) for c in cands)
            out.append({**claim, "status": "correct" if ok else "wrong", "truth": truth})
    return out

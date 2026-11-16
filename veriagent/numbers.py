"""Number formatting, parsing and tolerant comparison shared by all agents."""
from __future__ import annotations

import math
import re

CURRENCY = {"usd": "$", "$": "$", "inr": "₹", "rs": "₹", "eur": "€", "gbp": "£"}

# matches 1,234.5  -12  $3,400  4.5%  −3.2  1.2e3 (no), excludes evidence IDs (handled before)
NUM_RE = re.compile(r"(?<![\w.])([-−]?)([$₹€£]?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%)?(?![\w])")


def format_value(v: float, unit: str = "") -> str:
    u = (unit or "").strip()
    ul = u.lower()
    if ul in ("%", "percent", "pct", "percentage"):
        return f"{v:.1f}%"
    sym = CURRENCY.get(ul, "")
    av = abs(v)
    if float(v).is_integer() and av < 1e15:
        s = f"{int(round(av)):,}"
    elif av >= 1000:
        s = f"{av:,.0f}"
    elif av >= 100:
        s = f"{av:,.1f}"
    elif av >= 1:
        s = f"{av:.2f}"
    elif av == 0:
        s = "0"
    else:
        s = f"{av:.3g}"
    sign = "-" if v < 0 else ""
    if sym:
        return f"{sign}{sym}{s}"
    suffix = f" {u}" if u and not sym and ul not in ("count", "number", "") else ""
    return f"{sign}{s}{suffix}"


def find_numbers(text: str) -> list[dict]:
    """All numeric mentions with their span. Years (1900-2100 with no decimals,
    currency or %) are skipped because they are labels, not claims."""
    out = []
    for m in NUM_RE.finditer(text):
        sign, cur, intpart, dec, pct = m.groups()
        raw_int = intpart.replace(",", "")
        val = float(raw_int + (dec or ""))
        if sign:
            val = -val
        is_pct = bool(pct)
        if not dec and not cur and not is_pct and "," not in intpart and 1900 <= val <= 2100:
            continue
        decimals = len(dec) - 1 if dec else 0
        out.append({"value": val, "start": m.start(), "end": m.end(), "text": m.group(0),
                    "is_pct": is_pct, "decimals": decimals, "currency": cur or ""})
    return out


def matches(stated: float, true: float, decimals: int = 0, rel_tol: float = 0.01) -> bool:
    """True when `stated` equals `true` up to the rounding shown in the text or
    a relative tolerance, whichever is looser."""
    if true is None or stated is None or (isinstance(true, float) and math.isnan(true)):
        return False
    rounding = 0.5 * 10 ** (-decimals) + 1e-9
    return abs(stated - true) <= max(rounding, rel_tol * max(1.0, abs(true)))


def compute_op(op: str, vals: list[float]) -> float | None:
    """Recompute a derived quantity from evidence values. Order matters:
    pct_change/diff use [old, new]; ratio/share use [numerator, denominator]."""
    try:
        if op in ("value", "direct"):
            return vals[0]
        if op == "pct_change":
            return (vals[1] - vals[0]) / abs(vals[0]) * 100
        if op == "diff":
            return vals[1] - vals[0]
        if op == "ratio":
            return vals[0] / vals[1]
        if op == "share":
            return vals[0] / vals[1] * 100
        if op == "sum":
            return float(sum(vals))
        if op == "mean":
            return float(sum(vals) / len(vals))
    except (ZeroDivisionError, IndexError, TypeError):
        return None
    return None


DECREASE_WORDS = ("fell", "drop", "declin", "decreas", "lower", "down", "shrank", "lost", "reduc")
INCREASE_WORDS = ("rose", "grew", "increas", "higher", "up ", "gain", "climb", "jump")

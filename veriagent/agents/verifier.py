"""C3: Claim-level report verifier.

1. Numbers that came from placeholders ("bound") are recomputed from the evidence memory.
2. Every other number ("free") is extracted as a claim, mapped by the LLM to the evidence
   it depends on and the operation (value, pct_change, ratio ...), then RECOMPUTED in Python
   and compared with the stated value. Wrong numbers are corrected in place; numbers with no
   support are flagged (or removed).
3. Qualitative comparisons ("doubled", "halved", "majority") are checked against the ratio
   of the evidence they compare.
Without the evidence memory (ablation) the verifier can only ask the LLM to compare claims
with the analysis text, with no recomputation.
"""
from __future__ import annotations

import json
import re

from .. import prompts
from ..context import RunContext
from ..numbers import DECREASE_WORDS, INCREASE_WORDS, compute_op, find_numbers, matches
from .reporter import MARK

CITE_RE = re.compile(r"\[(?:EV-\d+(?:\s*,\s*EV-\d+)*)\]|\[(?:missing evidence|invalid)[^\]]*\]", re.I)
BOUND_RE = re.compile(re.escape(MARK) + r"(.*?)" + re.escape(MARK))
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'*])")
QUAL_RANGES = {"doubled": (1.8, 2.2), "double": (1.8, 2.2), "tripled": (2.7, 3.3), "triple": (2.7, 3.3),
               "halved": (0.45, 0.55), "half": (0.45, 0.55), "quadrupled": (3.6, 4.4)}
QUAL_SHARE = {"majority": 50.0, "most": 50.0}


# ----------------------------------------------------------------------------
def segment(md: str) -> list[dict]:
    """Split markdown into checkable sentences, remembering where each one lives."""
    segs = []
    for li, line in enumerate(md.split("\n")):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("![") or s.startswith("|"):
            continue
        prefix_m = re.match(r"^(\s*(?:[-*+]|\d+\.)\s+)", line)
        prefix = prefix_m.group(1) if prefix_m else ""
        body = line[len(prefix):]
        for k, sent in enumerate(SENT_SPLIT.split(body)):
            segs.append({"sid": len(segs), "line": li, "k": k, "prefix": prefix, "text": sent})
    return segs


def assemble(md: str, segs: list[dict]) -> str:
    lines = md.split("\n")
    by_line: dict[int, list[dict]] = {}
    for s in segs:
        by_line.setdefault(s["line"], []).append(s)
    for li, ss in by_line.items():
        kept = [s["text"] for s in sorted(ss, key=lambda s: s["k"]) if s["text"].strip()]
        lines[li] = (ss[0]["prefix"] + " ".join(kept)) if kept else ""
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out)


def _mask(text: str) -> str:
    """Hide bound numbers and citations so only free numbers are detected."""
    text = BOUND_RE.sub(lambda m: " " * len(m.group(0)), text)
    return CITE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _format_like(true: float, mention: dict, op: str) -> str:
    v = abs(true) if op in ("pct_change", "diff") else true
    dec = mention["decimals"]
    if mention["is_pct"]:
        return f"{v:.{max(dec, 1)}f}%"
    s = f"{abs(v):,.{dec}f}" if ("," in mention["text"] or abs(v) >= 1000) else f"{abs(v):.{dec}f}"
    return f"{'-' if v < 0 else ''}{mention['currency']}{s}"


# ----------------------------------------------------------------------------
def verify(ctx: RunContext, md: str, bound: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """Returns (verified_markdown_with_marks, log, unsupported_segments)."""
    segs = segment(md)
    log: list[dict] = []

    # 1. bound numbers ------------------------------------------------------
    for b in bound:
        if b.get("value") is None:
            log.append({"kind": "bound", "ids": b["ids"], "op": b["op"], "status": "unsupported",
                        "detail": b.get("error")})
            continue
        recs = [ctx.store.get(i) for i in b["ids"]]
        true = compute_op(b["op"], [r["value"] for r in recs]) if all(recs) else None
        ok = true is not None and matches(b["value"], true, 6)
        log.append({"kind": "bound", "ids": b["ids"], "op": b["op"], "number": b["text"],
                    "true": true, "status": "verified" if ok else "unsupported"})

    # 2. free numbers -------------------------------------------------------
    mentions = []
    for s in segs:
        masked = _mask(s["text"])
        for m in find_numbers(masked):
            m.update(sid=s["sid"], nid=len(mentions))
            mentions.append(m)
    quals = [(s["sid"], w) for s in segs for w in list(QUAL_RANGES) + list(QUAL_SHARE)
             if re.search(rf"\b{w}\b", s["text"], re.I)]

    if (mentions or quals) and ctx.mode.memory:
        decisions, qual_dec = _extract(ctx, segs, mentions)
    elif mentions:
        decisions, qual_dec = _text_check(ctx, segs, mentions), []
    else:
        decisions, qual_dec = {}, []

    edits: dict[int, list[tuple]] = {}
    unsupported_sids = {s["sid"] for s in segs if "[missing evidence" in s["text"] or "[invalid " in s["text"]}
    for m in mentions:
        d = decisions.get(m["nid"], {"status": "unsupported"})
        entry = {"kind": "free", "sid": m["sid"], "number": m["text"].strip(), "stated": m["value"],
                 "op": d.get("op"), "ids": d.get("operands", []), "true": d.get("true"),
                 "status": d["status"]}
        if d["status"] == "corrected":
            edits.setdefault(m["sid"], []).append((m["start"], m["end"], _format_like(d["true"], m, d.get("op", "value"))))
        elif d["status"] == "unsupported":
            unsupported_sids.add(m["sid"])
            edits.setdefault(m["sid"], []).append((m["start"], m["end"], None))
        elif d["status"] == "flagged":
            unsupported_sids.add(m["sid"])
            edits.setdefault(m["sid"], []).append((m["start"], m["end"], "FLAG"))
        entry["sentence"] = segs[m["sid"]]["text"]
        log.append(entry)
    for q in qual_dec:
        log.append(q)

    # 3. apply edits --------------------------------------------------------
    for sid, es in edits.items():
        text = segs[sid]["text"]
        if ctx.settings.unsupported_action == "remove" and any(r in (None, "FLAG") for *_, r in es):
            segs[sid]["text"] = ""
            continue
        for start, end, rep in sorted(es, key=lambda e: e[0], reverse=True):
            original = text[start:end]
            lead = original[: len(original) - len(original.lstrip())]
            if rep is None:
                text = text[:end] + " [unverified]" + text[end:]
            elif rep == "FLAG":
                text = text[:end] + " [direction contradicts data]" + text[end:]
            else:
                text = text[:start] + lead + rep + text[end:]
        segs[sid]["text"] = text
    unsupported = [{"sid": s["sid"], "text": s["text"]} for s in segs if s["sid"] in unsupported_sids]
    return assemble(md, segs), log, unsupported


def _extract(ctx: RunContext, segs: list[dict], mentions: list[dict]):
    """LLM maps each number to evidence + operation; Python recomputes."""
    cand_ids: dict[str, dict] = {}
    for s in segs:
        if any(m["sid"] == s["sid"] for m in mentions):
            for i in re.findall(r"EV-\d+", s["text"]):
                r = ctx.store.get(i)
                if r:
                    cand_ids[r["id"]] = r
            for r in ctx.store.search(_mask(s["text"]), k=5):
                cand_ids[r["id"]] = r
    catalogue = "\n".join(f"{r['id']}: {r['statement']} = {r['value']} {r['unit']}".rstrip()
                          for r in sorted(cand_ids.values(), key=lambda r: r["id"]))
    items = [{"nid": m["nid"], "sid": m["sid"], "number": m["text"].strip(),
              "sentence": segs[m["sid"]]["text"].replace(MARK, "")} for m in mentions]
    sents_with_quals = [{"sid": s["sid"], "sentence": s["text"].replace(MARK, "")} for s in segs
                        if any(re.search(rf"\b{w}\b", s["text"], re.I) for w in list(QUAL_RANGES) + list(QUAL_SHARE))]
    user = (f"Evidence:\n{catalogue or '(none)'}\n\nNumeric mentions:\n{json.dumps(items, ensure_ascii=False)}"
            f"\n\nSentences with qualitative comparisons:\n{json.dumps(sents_with_quals, ensure_ascii=False)}")
    reply = ctx.llm.chat("verifier_extract", prompts.VERIFIER_EXTRACT, user, json=True)
    reply = reply if isinstance(reply, dict) else {}

    by_nid = {m["nid"]: m for m in mentions}
    decisions = {}
    for c in reply.get("claims", []) or []:
        try:
            nid = int(c.get("nid"))
        except (TypeError, ValueError):
            continue
        if nid not in by_nid:
            continue
        m = by_nid[nid]
        op = str(c.get("op", "unsupported")).lower()
        ids = [str(i).upper() for i in (c.get("operands") or [])]
        if op == "not_a_claim":
            decisions[nid] = {"status": "skipped", "op": op}
            continue
        recs = [ctx.store.get(i) for i in ids]
        if op == "unsupported" or not ids or any(r is None for r in recs):
            decisions[nid] = {"status": "unsupported", "op": op, "operands": ids}
            continue
        true = compute_op(op, [r["value"] for r in recs])
        if true is None:
            decisions[nid] = {"status": "unsupported", "op": op, "operands": ids}
            continue
        stated = m["value"]
        sent = segs[m["sid"]]["text"].lower()
        if op in ("pct_change", "diff") or any(w in sent for w in DECREASE_WORDS):
            ok = matches(abs(stated), abs(true), m["decimals"], ctx.settings.tolerance)
        else:
            ok = matches(stated, true, m["decimals"], ctx.settings.tolerance)
        if ok and op in ("pct_change", "diff"):
            dec_word = any(w in sent for w in DECREASE_WORDS)
            inc_word = any(w in sent for w in INCREASE_WORDS)
            if (dec_word and true > 0) or (inc_word and not dec_word and true < 0):
                # right magnitude, wrong direction: the wording must change, not the number
                decisions[nid] = {"status": "flagged", "op": op, "operands": ids, "true": true,
                                  "detail": "direction of change contradicts the evidence"}
                continue
        decisions[nid] = {"status": "verified" if ok else "corrected", "op": op, "operands": ids,
                          "true": true}

    qual_log = []
    for q in reply.get("qualitative", []) or []:
        phrase = str(q.get("phrase", "")).lower()
        ids = [str(i).upper() for i in (q.get("operands") or [])]
        recs = [ctx.store.get(i) for i in ids]
        if len(ids) < 2 or any(r is None for r in recs):
            qual_log.append({"kind": "qualitative", "sid": q.get("sid"), "phrase": phrase, "ids": ids,
                             "status": "unsupported"})
            continue
        if phrase in QUAL_RANGES:
            ratio = compute_op("ratio", [recs[0]["value"], recs[1]["value"]])
            lo, hi = QUAL_RANGES[phrase]
            ok = ratio is not None and lo <= ratio <= hi
            qual_log.append({"kind": "qualitative", "sid": q.get("sid"), "phrase": phrase, "ids": ids,
                             "true": ratio, "status": "verified" if ok else "flagged"})
        elif phrase in QUAL_SHARE:
            share = compute_op("share", [recs[0]["value"], recs[1]["value"]])
            ok = share is not None and share > QUAL_SHARE[phrase]
            qual_log.append({"kind": "qualitative", "sid": q.get("sid"), "phrase": phrase, "ids": ids,
                             "true": share, "status": "verified" if ok else "flagged"})
    return decisions, qual_log


def _text_check(ctx: RunContext, segs: list[dict], mentions: list[dict]) -> dict:
    """Ablation without memory: LLM-only comparison against the analysis text."""
    items = [{"nid": m["nid"], "number": m["text"].strip(), "sentence": segs[m["sid"]]["text"]}
             for m in mentions]
    user = (f"Analysis results:\n{chr(10).join(ctx.results_text)}\n\nNumeric mentions:\n"
            f"{json.dumps(items, ensure_ascii=False)}")
    reply = ctx.llm.chat("verifier_text", prompts.VERIFIER_TEXT, user, json=True)
    out = {}
    for c in (reply or {}).get("checks", []) if isinstance(reply, dict) else []:
        try:
            nid = int(c.get("nid"))
        except (TypeError, ValueError):
            continue
        if c.get("supported"):
            out[nid] = {"status": "verified", "op": "llm_text"}
        elif isinstance(c.get("correct_value"), (int, float)):
            out[nid] = {"status": "corrected", "op": "llm_text", "true": float(c["correct_value"])}
        else:
            out[nid] = {"status": "unsupported", "op": "llm_text"}
    return out


def summarize(log: list[dict]) -> dict:
    keys = ("verified", "corrected", "unsupported", "flagged", "repaired", "skipped")
    out = {k: sum(1 for e in log if e["status"] == k) for k in keys}
    out["claims_checked"] = sum(out[k] for k in keys if k not in ("skipped", "repaired"))
    return out


def strip_marks(md: str) -> str:
    return md.replace(MARK, "")

"""Deterministic mock LLM for offline testing of the whole pipeline.

It produces plausible agent outputs from simple heuristics so that every code path
(repair loops, critic revisions, verifier corrections) can be exercised without an
API key. Its outputs are NOT research results: never report numbers from mock runs.
"""
from __future__ import annotations

import json
import re


def _user_text(messages) -> str:
    c = messages[-1]["content"]
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if p.get("type") == "text")
    return c


def _columns(text: str) -> list[dict]:
    cols = []
    for m in re.finditer(r"^\s+- (.+?) \((numeric|datetime|categorical), [^)]*\): (\d+ values)?", text, re.M):
        kind = m.group(2)
        n = int(m.group(3).split()[0]) if m.group(3) else 0
        if kind == "categorical" and n > 25:
            kind = "id"
        cols.append({"name": m.group(1), "kind": kind})
    return cols


def _evidence(text: str) -> list[dict]:
    ev = []
    for m in re.finditer(r"^(EV-\d+): (.+?) = (-?[\d.eE+-]+)\s*(.*)$", text, re.M):
        ev.append({"id": m.group(1), "statement": m.group(2), "value": float(m.group(3)),
                   "unit": m.group(4).strip()})
    return ev


def _mentioned(question: str, cols: list[dict]) -> list[dict]:
    q = question.lower()
    return [c for c in cols if c["name"].lower() in q or c["name"].lower().replace("_", " ") in q]


def _code_for(question: str, cols: list[dict], buggy: bool = False) -> str:
    q = question.lower()
    ment = _mentioned(question, cols)
    nums = [c["name"] for c in ment if c["kind"] == "numeric"] or \
           [c["name"] for c in cols if c["kind"] == "numeric"]
    cats = [c["name"] for c in ment if c["kind"] == "categorical"] or \
           [c["name"] for c in cols if c["kind"] == "categorical"]
    dates = [c["name"] for c in cols if c["kind"] == "datetime"]
    if not nums:
        return "findings = [{'statement': 'Number of rows', 'value': int(len(df)), 'unit': 'count'}]"
    y = nums[0]
    agg = "mean" if any(w in q for w in ("average", "mean", "typical")) else "sum"
    if "correlat" in q and len(nums) >= 2:
        a, b = nums[0], nums[1]
        return (f"r = float(df[{a!r}].corr(df[{b!r}]))\n"
                f"findings = [{{'statement': 'Pearson correlation between {a} and {b}', 'value': round(r, 3), 'unit': ''}}]\n"
                f"table = df[[{a!r}, {b!r}]].dropna().head(60)")
    if dates and any(w in q for w in ("trend", "over time", "year", "month", "change")):
        d = dates[0]
        col = "Date_" if buggy else d
        return (f"g = df.groupby(df[{col!r}].dt.year)[{y!r}].{agg}()\n"
                f"table = g.reset_index()\ntable.columns = ['year', {y!r}]\n"
                f"findings = [\n"
                f"  {{'statement': f'{agg.title()} {y} in {{int(g.index[0])}} (first year)', 'value': float(g.iloc[0]), 'unit': ''}},\n"
                f"  {{'statement': f'{agg.title()} {y} in {{int(g.index[-1])}} (last year)', 'value': float(g.iloc[-1]), 'unit': ''}},\n"
                f"  {{'statement': 'Highest yearly {agg} of {y}', 'value': float(g.max()), 'unit': ''}},\n"
                f"]")
    if cats:
        c = cats[0]
        return (f"g = df.groupby({c!r})[{y!r}].{agg}().sort_values(ascending=False)\n"
                f"table = g.reset_index()\n"
                f"findings = [\n"
                f"  {{'statement': f'{agg.title()} {y} for {{g.index[0]}} (highest {c})', 'value': float(g.iloc[0]), 'unit': ''}},\n"
                f"  {{'statement': f'{agg.title()} {y} for {{g.index[1]}} (second highest {c})', 'value': float(g.iloc[1]), 'unit': ''}} if len(g) > 1 else None,\n"
                f"  {{'statement': f'{agg.title()} {y} for {{g.index[-1]}} (lowest {c})', 'value': float(g.iloc[-1]), 'unit': ''}},\n"
                f"  {{'statement': 'Overall {agg} of {y}', 'value': float(df[{y!r}].{agg}()), 'unit': ''}},\n"
                f"]\nfindings = [f for f in findings if f]")
    return (f"findings = [\n"
            f"  {{'statement': 'Mean {y}', 'value': float(df[{y!r}].mean()), 'unit': ''}},\n"
            f"  {{'statement': 'Maximum {y}', 'value': float(df[{y!r}].max()), 'unit': ''}},\n"
            f"  {{'statement': 'Minimum {y}', 'value': float(df[{y!r}].min()), 'unit': ''}},\n"
            f"]\ntable = df[[{y!r}]].dropna().head(60)")


def _fmt(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"


class MockBackend:
    def respond(self, role: str, messages: list[dict], image_png: bytes | None) -> str:
        text = _user_text(messages)
        fn = getattr(self, f"_r_{role}", None)
        if fn is None:
            return "{}"
        return fn(text, messages)

    # ------------------------------------------------------------------
    def _r_planner(self, text, _):
        goal = re.search(r"Goal: (.+)", text).group(1)
        cols = _columns(text)
        nums = [c["name"] for c in cols if c["kind"] == "numeric"]
        cats = [c["name"] for c in cols if c["kind"] == "categorical"]
        dates = [c["name"] for c in cols if c["kind"] == "datetime"]
        tasks = [{"id": "T1", "question": goal, "chart": True}]
        if nums and cats:
            tasks.append({"id": "T2", "question": f"Compare total {nums[0]} across {cats[0]}", "chart": True})
        if nums and dates:
            tasks.append({"id": "T3", "question": f"Trend of {nums[0]} over time by year", "chart": True})
        if len(nums) >= 2:
            tasks.append({"id": "T4", "question": f"Correlation between {nums[0]} and {nums[1]}", "chart": False})
        return json.dumps({"tasks": tasks})

    def _r_analyst(self, text, _):
        q = re.search(r"Task T\d+: (.+)", text).group(1)
        return json.dumps({"code": _code_for(q, _columns(text), buggy="trend" in q.lower())})

    def _r_analyst_repair(self, text, _):
        q = re.search(r"Task T\d+: (.+)", text).group(1)
        return json.dumps({"code": _code_for(q, _columns(text), buggy=False)})

    def _r_single_code(self, text, messages):
        if text.startswith("The code failed"):
            first = next(m["content"] for m in messages if m["role"] == "user" and "Goal:" in str(m["content"]))
            q = re.search(r"Goal: (.+)", first).group(1)
            return json.dumps({"code": _code_for(q, _columns(first))})
        q = re.search(r"Goal: (.+)", text).group(1)
        return json.dumps({"code": _code_for(q, _columns(text))})

    def _r_visualizer(self, text, _):
        cols = json.loads(re.search(r"Columns: (\[.*?\])", text).group(1).replace("'", '"'))
        rows = int(re.search(r"Rows: (\d+)", text).group(1))
        q = re.search(r"Task T\d+: (.+)", text).group(1)
        x = cols[0]
        y = cols[1] if len(cols) > 1 else None
        if y is None:
            spec = {"type": "hist", "x": x, "y": None}
        elif x.lower() in ("year", "date", "month"):
            spec = {"type": "line", "x": x, "y": y}
        elif "correlat" in q.lower():
            spec = {"type": "scatter", "x": x, "y": y}
        else:
            spec = {"type": "bar", "x": x, "y": y, "sort": "desc"}
        spec.update(title=q[:70], xlabel=x, ylabel=y or "Count", rotate_labels=False)
        if "critic asked" in text:
            spec["rotate_labels"] = True
            if "Too many bars" in text or rows > 12:
                spec.update(type="barh", top_n=15)
        return json.dumps(spec)

    def _r_critic(self, text, _):
        spec = json.loads(re.search(r"Spec: (\{.*\})", text).group(1))
        n = int(re.search(r"Points plotted: (\d+)", text).group(1))
        if spec.get("type") == "bar" and n >= 4 and not spec.get("rotate_labels"):
            return json.dumps({"verdict": "revise", "issues": ["Category labels may overlap"],
                               "fix": "Rotate x labels", "readability": 3})
        return json.dumps({"verdict": "approve", "issues": [], "fix": "", "readability": 4})

    def _r_reporter(self, text, _):
        ev = _evidence(text)
        charts = re.findall(r"^(T\d+): ", text.split("Approved charts:")[-1], re.M)
        lines = ["## Executive Summary"]
        if ev:
            lines.append(f"The analysis found that {ev[0]['statement'].lower()} was {{{{{ev[0]['id']}}}}}.")
        if len(ev) >= 2 and ev[1]["value"]:
            wrong = ev[0]["value"] / ev[1]["value"] * 1.3
            lines.append(f"This is roughly {wrong:.2f} times the second item [{ev[0]['id']}, {ev[1]['id']}].")
        lines.append("")
        lines.append("## Key Findings")
        for e in ev[:6]:
            lines.append(f"- {e['statement']}: {{{{{e['id']}}}}}.")
        lines += ["", "## Charts"] + [f"[[CHART:{c}]]" for c in charts]
        lines += ["", "## Recommendations", "- Focus on the strongest segment identified above.",
                  "- Review the weakest segment for causes."]
        return "\n".join(lines)

    def _r_reporter_free(self, text, _):
        cite = bool(_evidence(text))
        ev = _evidence(text)
        if not ev:
            ev = [{"id": "", "statement": m.group(1), "value": float(m.group(2)), "unit": ""}
                  for m in re.finditer(r"^- (.+?): (-?[\d.eE+-]+)", text, re.M)]
        charts = re.findall(r"^(T\d+): ", text.split("Approved charts:")[-1], re.M)
        lines = ["## Executive Summary"]
        for i, e in enumerate(ev[:6]):
            v = e["value"] * (1.12 if i % 3 == 2 else 1.0)  # simulated arithmetic slip
            c = f" [{e['id']}]" if cite and e["id"] else ""
            lines.append(f"- {e['statement']}: {_fmt(v)}{c}.")
        lines += ["", "## Charts"] + [f"[[CHART:{c}]]" for c in charts]
        lines += ["", "## Recommendations", "- Act on the main finding."]
        return "\n".join(lines)

    def _r_reporter_repair(self, text, _):
        sents = json.loads(text.split("Sentences to fix:\n", 1)[1])
        out = []
        for s in sents:
            ids = re.findall(r"EV-\d+", s["sentence"])
            new = re.sub(r"\d[\d,]*(?:\.\d+)?\s*(?:times)?", "", s["sentence"]).replace("[unverified]", "")
            if ids:
                new = re.sub(r"\[EV-[^\]]*\]", "", new).strip() + f" (see {{{{{ids[0]}}}}})."
            out.append({"sid": s["sid"], "sentence": re.sub(r"\s+", " ", new).strip()})
        return json.dumps({"rewrites": out})

    def _r_verifier_extract(self, text, _):
        ev = {e["id"]: e for e in _evidence(text)}
        items = json.loads(text.split("Numeric mentions:\n", 1)[1].split("\n\nSentences with", 1)[0])
        claims = []
        for it in items:
            ids = [i for i in re.findall(r"EV-\d+", it["sentence"]) if i in ev]
            num = float(re.sub(r"[^\d.\-]", "", it["number"].replace("−", "-")) or 0)
            if "times" in it["sentence"] and len(ids) >= 2:
                claims.append({"nid": it["nid"], "op": "ratio", "operands": ids[:2]})
            elif ids:
                best = min(ids, key=lambda i: abs(ev[i]["value"] - num))
                claims.append({"nid": it["nid"], "op": "value", "operands": [best]})
            else:
                close = [e for e in ev.values() if e["value"] and abs(e["value"] - num) / abs(e["value"]) < 0.15]
                if close:
                    claims.append({"nid": it["nid"], "op": "value", "operands": [close[0]["id"]]})
                else:
                    claims.append({"nid": it["nid"], "op": "unsupported", "operands": []})
        return json.dumps({"claims": claims, "qualitative": []})

    def _r_verifier_text(self, text, _):
        vals = [float(v) for v in re.findall(r": (-?[\d.]+)", text.split("Numeric mentions:")[0])]
        items = json.loads(text.split("Numeric mentions:\n", 1)[1])
        checks = []
        for it in items:
            num = float(re.sub(r"[^\d.\-]", "", it["number"]) or 0)
            best = min(vals, key=lambda v: abs(v - num)) if vals else None
            if best is not None and abs(best - num) <= 0.01 * max(1, abs(best)) + 0.005:
                checks.append({"nid": it["nid"], "supported": True, "correct_value": None})
            elif best is not None and abs(best - num) <= 0.3 * max(1, abs(best)):
                checks.append({"nid": it["nid"], "supported": False, "correct_value": best})
            else:
                checks.append({"nid": it["nid"], "supported": False, "correct_value": None})
        return json.dumps({"checks": checks})

    def _r_single_report(self, text, _):
        cols = re.search(r"Chart table columns: (\[.*\])", text)
        cols = json.loads(cols.group(1).replace("'", '"')) if cols else []
        res = [(m.group(1), float(m.group(2))) for m in re.finditer(r"^- (.+?): (-?[\d.eE+-]+)", text, re.M)]
        lines = ["## Executive Summary"]
        for i, (s, v) in enumerate(res[:6]):
            lines.append(f"- {s}: {_fmt(v * (1.12 if i % 3 == 2 else 1.0))}.")
        lines += ["", "## Charts", "[[CHART:T1]]", "", "## Recommendations", "- Act on the main finding."]
        chart = {"type": "bar", "x": cols[0], "y": cols[1], "title": "Main result"} if len(cols) >= 2 else None
        return json.dumps({"chart": chart, "report": "\n".join(lines)})

    def _r_auditor(self, text, _):
        m = re.search(r"Highlighted number: (\S+)", text)
        num = float(re.sub(r"[^\d.\-]", "", m.group(1)) or 0) if m else 0
        return json.dumps({"computable": True,
                           "code": f"findings = [{{'statement': 'mock audit', 'value': {num}, 'unit': ''}}]"})

    def _r_readability_judge(self, text, _):
        return json.dumps({"readability": 4, "reason": "mock"})

"""Standalone HTML export of a verified report (no external dependencies)."""
from __future__ import annotations

import html
import re


def md_to_html(md: str) -> str:
    out, in_list = [], False
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if s.startswith("#"):
            if in_list:
                out.append("</ul>")
                in_list = False
            level = min(len(s) - len(s.lstrip("#")), 4)
            out.append(f"<h{level}>{_inline(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith("!["):
            m = re.match(r"!\[(.*?)\]\((.*?)\)", s)
            if m:
                out.append(f'<figure><img src="{html.escape(m.group(2))}" alt="{html.escape(m.group(1))}">'
                           f"<figcaption>{html.escape(m.group(1))}</figcaption></figure>")
        elif re.match(r"^([-*+]|\d+\.)\s+", s):
            if not in_list:
                out.append("<ul>")
                in_list = True
            content = _inline(re.sub(r"^([-*+]|\d+\.)\s+", "", s))
            out.append(f"<li>{content}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[((?:EV-\d+)(?:,\s*EV-\d+)*)\]", r'<span class="ev">\1</span>', s)
    s = s.replace("[unverified]", '<span class="flag">unverified</span>')
    s = s.replace("[direction contradicts data]", '<span class="flag">direction contradicts data</span>')
    return s


def _num(v) -> str:
    return "" if v is None else f"{v:.4g}"


def to_html(report_md: str, meta: dict, log: list[dict]) -> str:
    summ = meta.get("verification_summary")
    badge = ""
    if summ:
        badge = (f'<div class="badge">Verified {summ["verified"]} · corrected {summ["corrected"]} · '
                 f'unsupported {summ["unsupported"]} · flagged {summ["flagged"]}</div>')
    rows = "".join(
        f"<tr><td>{html.escape(str(e.get('kind')))}</td><td>{html.escape(str(e.get('number', e.get('phrase', ''))))}</td>"
        f"<td>{html.escape(', '.join(e.get('ids') or []))}</td><td>{html.escape(str(e.get('op') or ''))}</td>"
        f"<td>{_num(e.get('true'))}</td>"
        f"<td class='s-{e['status']}'>{e['status']}</td></tr>"
        for e in log if e.get("status") != "skipped")
    table = (f"<h2>Verification log</h2><table><tr><th>Kind</th><th>Number</th><th>Evidence</th><th>Op</th>"
             f"<th>Recomputed</th><th>Status</th></tr>{rows}</table>") if rows else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>VeriAgent report</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;color:#1b1f27;line-height:1.55}}
h1,h2{{color:#14213d}} img{{max-width:100%;border:1px solid #e3e8f0;border-radius:6px}}
figure{{margin:16px 0}} figcaption{{font-size:13px;color:#5b6475}}
.ev{{font-size:11px;background:#eef3fb;color:#1f6feb;border-radius:4px;padding:1px 4px}}
.flag{{font-size:11px;background:#fdecee;color:#b3261e;border-radius:4px;padding:1px 4px}}
.badge{{display:inline-block;background:#e8f6ef;color:#0e7a5f;border-radius:6px;padding:6px 10px;font-weight:600;margin:8px 0 16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{border:1px solid #d8dee9;padding:4px 6px;text-align:left}}
.s-verified{{color:#0e7a5f}} .s-corrected{{color:#b26b00}} .s-unsupported,.s-flagged{{color:#b3261e}}
.meta{{font-size:12px;color:#5b6475}}
</style></head><body>
<h1>Data Analysis Report</h1>
<div class="meta">Goal: {html.escape(meta.get('goal', ''))} · mode {meta.get('mode')} · model {html.escape(str(meta.get('text_model')))} · {meta.get('seconds')} s</div>
{badge}
{md_to_html(report_md)}
{table}
</body></html>"""

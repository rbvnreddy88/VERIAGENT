"""Standalone HTML export of a verified report (no external dependencies)."""
from __future__ import annotations

import html
import re


def md_to_html(md: str) -> str:
    out = []
    in_list = False
    in_table = False
    table_rows = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def close_table():
        nonlocal in_table, table_rows
        if in_table and table_rows:
            is_sep = lambda r: bool(re.match(r"^\|?[\s\-:|]+\|?$", r))
            header_row = None
            body_rows = []
            if len(table_rows) >= 2 and is_sep(table_rows[1]):
                header_row = [c.strip() for c in table_rows[0].strip("|").split("|")]
                body_rows = [[c.strip() for c in r.strip("|").split("|")] for r in table_rows[2:]]
            else:
                body_rows = [[c.strip() for c in r.strip("|").split("|")] for r in table_rows]

            html_table = ['<div class="table-container"><table class="report-table">']
            if header_row:
                html_table.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header_row) + "</tr></thead>")
            if body_rows:
                html_table.append("<tbody>")
                for r in body_rows:
                    html_table.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                html_table.append("</tbody>")
            html_table.append("</table></div>")
            out.append("\n".join(html_table))
            in_table = False
            table_rows = []

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            close_list()
            close_table()
            continue

        if s.startswith("|") and s.endswith("|"):
            close_list()
            in_table = True
            table_rows.append(s)
            continue

        close_table()

        if s.startswith("#"):
            close_list()
            level = min(len(s) - len(s.lstrip("#")), 4)
            out.append(f"<h{level}>{_inline(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith("!["):
            close_list()
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
            close_list()
            out.append(f"<p>{_inline(s)}</p>")

    close_list()
    close_table()
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[((?:EV-\d+)(?:,\s*EV-\d+)*)\]", r'<span class="ev" title="Verified Evidence Metric">\1</span>', s)
    s = s.replace("[unverified]", '<span class="flag">unverified</span>')
    s = s.replace("[direction contradicts data]", '<span class="flag">direction contradicts data</span>')
    return s


def _num(v) -> str:
    return "" if v is None else f"{v:.4g}"


def to_html(report_md: str, meta: dict, log: list[dict]) -> str:
    summ = meta.get("verification_summary")
    badge = ""
    if summ:
        badge = (f'<div class="badge"><span>🛡️ VeriAgent Fact Audit</span>'
                 f'<span>Verified: <strong>{summ.get("verified", 0)}</strong></span>'
                 f'<span>Corrected: <strong>{summ.get("corrected", 0)}</strong></span>'
                 f'<span>Unsupported: <strong>{summ.get("unsupported", 0)}</strong></span>'
                 f'<span>Flagged: <strong>{summ.get("flagged", 0)}</strong></span></div>')
    rows = "".join(
        f"<tr><td><span class='kind-pill'>{html.escape(str(e.get('kind')))}</span></td>"
        f"<td><strong>{html.escape(str(e.get('number', e.get('phrase', ''))))}</strong></td>"
        f"<td><span class='ev'>{html.escape(', '.join(e.get('ids') or []))}</span></td>"
        f"<td><code>{html.escape(str(e.get('op') or ''))}</code></td>"
        f"<td>{_num(e.get('true'))}</td>"
        f"<td><span class='status-pill s-{e['status']}'>{e['status']}</span></td></tr>"
        for e in log if e.get("status") != "skipped")
    table = (f"<h2>Empirical Verification Log</h2>"
             f"<div class='table-container'><table class='report-table'><thead><tr>"
             f"<th>Kind</th><th>Mention / Value</th><th>Evidence ID</th><th>Operation</th>"
             f"<th>Recomputed (Python)</th><th>Audit Status</th></tr></thead>"
             f"<tbody>{rows}</tbody></table></div>") if rows else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VeriAgent Quantitative Analysis Report</title>
<style>
  :root {{
    --bg-dark: #090D16;
    --bg-surface: #101626;
    --bg-card: #151D30;
    --border: rgba(255, 255, 255, 0.08);
    --primary: #6366F1;
    --accent: #06B6D4;
    --success: #10B981;
    --warning: #F59E0B;
    --danger: #EF4444;
    --text-main: #F3F4F6;
    --text-muted: #9CA3AF;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    max-width: 960px;
    margin: 40px auto;
    padding: 0 24px;
    background-color: var(--bg-dark);
    color: var(--text-main);
    line-height: 1.65;
  }}
  h1 {{
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
  }}
  h2 {{
    font-size: 20px;
    font-weight: 600;
    color: #E2E8F0;
    margin-top: 32px;
    margin-bottom: 12px;
  }}
  p {{ margin: 14px 0; color: #D1D5DB; }}
  ul {{ margin: 14px 0; padding-left: 24px; }}
  li {{ margin: 6px 0; color: #D1D5DB; }}
  strong {{ color: #FFFFFF; font-weight: 600; }}
  code {{ font-family: monospace; background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  img {{ max-width: 100%; border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }}
  figure {{ margin: 24px 0; }}
  figcaption {{ font-size: 13px; color: var(--text-muted); margin-top: 8px; text-align: center; }}
  .meta {{
    font-size: 13px;
    color: var(--text-muted);
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 20px;
  }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 16px;
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34D399;
    border-radius: 8px;
    padding: 10px 18px;
    font-size: 13px;
    margin: 12px 0 24px;
  }}
  .ev {{
    font-size: 11px;
    font-family: monospace;
    font-weight: 600;
    background: rgba(99, 102, 241, 0.18);
    color: #A5B4FC;
    border: 1px solid rgba(99, 102, 241, 0.35);
    border-radius: 4px;
    padding: 2px 6px;
    margin: 0 2px;
    display: inline-block;
  }}
  .flag {{
    font-size: 11px;
    background: rgba(239, 68, 68, 0.2);
    color: #FCA5A5;
    border: 1px solid rgba(239, 68, 68, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
  }}
  .table-container {{
    overflow-x: auto;
    margin: 20px 0;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg-surface);
  }}
  .report-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    text-align: left;
  }}
  .report-table th {{
    background: rgba(255, 255, 255, 0.04);
    color: #E2E8F0;
    font-weight: 600;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
  }}
  .report-table td {{
    padding: 10px 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    color: #CBD5E1;
  }}
  .report-table tr:hover td {{
    background: rgba(255, 255, 255, 0.02);
  }}
  .status-pill {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }}
  .s-verified {{ background: rgba(16, 185, 129, 0.2); color: #34D399; }}
  .s-corrected {{ background: rgba(245, 158, 11, 0.2); color: #FBBF24; }}
  .s-unsupported, .s-flagged {{ background: rgba(239, 68, 68, 0.2); color: #F87171; }}
  .kind-pill {{
    background: rgba(255, 255, 255, 0.08);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
  }}
</style>
</head>
<body>
<h1>Quantitative Analysis & Verification Report</h1>
<div class="meta">
  <strong>Objective:</strong> {html.escape(meta.get('goal', ''))} &bull; 
  <strong>Architecture:</strong> VeriAgent ({meta.get('mode', 'full')}) &bull; 
  <strong>Model:</strong> {html.escape(str(meta.get('text_model', '')))} &bull; 
  <strong>Latency:</strong> {meta.get('seconds', 0)}s
</div>
{badge}
{md_to_html(report_md)}
{table}
</body>
</html>"""

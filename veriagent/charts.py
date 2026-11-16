"""Deterministic chart renderer: a JSON chart spec plus an evidence table -> PNG.

The visualization agent only decides *what* to draw (spec); this module decides
*how*, using a fixed template that avoids common defects (clipped negatives,
overlapping labels, missing axis titles)."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402
import pandas as pd  # noqa: E402

CHART_TYPES = {"bar", "barh", "line", "scatter", "hist"}
PALETTE = ["#2A6FDB", "#E07A2F", "#2BA58A", "#C2417A", "#7A5CC2", "#8C8C2E", "#3C9DC9", "#B5523B"]


class ChartError(Exception):
    pass


def table_to_df(table: dict) -> pd.DataFrame:
    return pd.DataFrame(table["rows"], columns=table["columns"])


def plotted_data(spec: dict, table: dict) -> pd.DataFrame:
    """The exact data a spec draws (after sorting / top-n). Used by evaluation."""
    df = table_to_df(table)
    x, y = spec.get("x"), spec.get("y")
    if spec.get("type") == "hist":
        return df[[x]].dropna()
    cols = [c for c in (x, y, spec.get("series")) if c]
    d = df[cols].dropna(subset=[y])
    if spec.get("sort") in ("asc", "desc"):
        d = d.sort_values(y, ascending=spec["sort"] == "asc")
    if spec.get("top_n"):
        d = d.head(int(spec["top_n"]))
    return d


def render(spec: dict, table: dict) -> bytes:
    t = spec.get("type")
    if t not in CHART_TYPES:
        raise ChartError(f"Unknown chart type '{t}'. Use one of {sorted(CHART_TYPES)}")
    df = table_to_df(table)
    x, y, series = spec.get("x"), spec.get("y"), spec.get("series")
    for c in [x] + ([y] if t != "hist" else []) + ([series] if series else []):
        if c not in df.columns:
            raise ChartError(f"Column '{c}' not in table columns {list(df.columns)}")
    d = plotted_data(spec, table)
    if d.empty:
        raise ChartError("No data to plot after filtering")
    if t != "hist" and not pd.api.types.is_numeric_dtype(pd.to_numeric(d[y], errors="coerce")):
        raise ChartError(f"y column '{y}' is not numeric")

    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=110)
    try:
        n = len(d)
        if t in ("bar", "barh"):
            labels = d[x].astype(str).tolist()
            vals = pd.to_numeric(d[y]).tolist()
            colors = [PALETTE[0] if v >= 0 else "#D1495B" for v in vals]
            if t == "bar":
                bars = ax.bar(labels, vals, color=colors)
                ax.axhline(0, color="#444", lw=0.8)
                ax.set_ylim(*_padded_limits(vals))
            else:
                bars = ax.barh(labels, vals, color=colors)
                ax.axvline(0, color="#444", lw=0.8)
                ax.set_xlim(*_padded_limits(vals))
                ax.invert_yaxis()
            if n <= 15:
                ax.bar_label(bars, labels=[_short(v) for v in vals], padding=2, fontsize=8)
        elif t == "line":
            xs = d[x]
            try:
                xs = pd.to_datetime(xs) if not pd.api.types.is_numeric_dtype(xs) else xs
            except (ValueError, TypeError):
                pass
            if series:
                for i, (k, g) in enumerate(d.assign(_x=xs).groupby(series)):
                    g = g.sort_values("_x")
                    ax.plot(g["_x"], g[y], marker="o" if len(g) <= 30 else None, ms=3,
                            color=PALETTE[i % len(PALETTE)], label=str(k))
                ax.legend(fontsize=8, frameon=False)
            else:
                dd = d.assign(_x=xs).sort_values("_x")
                ax.plot(dd["_x"], dd[y], marker="o" if n <= 30 else None, ms=3, color=PALETTE[0])
            vals = pd.to_numeric(d[y]).tolist()
            if min(vals) < 0:
                ax.axhline(0, color="#444", lw=0.8)
        elif t == "scatter":
            ax.scatter(d[x], d[y], s=14, alpha=0.7, color=PALETTE[0])
        elif t == "hist":
            ax.hist(pd.to_numeric(d[x], errors="coerce").dropna(), bins=int(spec.get("bins") or 20),
                    color=PALETTE[0], edgecolor="white")

        ax.set_title(spec.get("title") or "", fontsize=11, pad=10)
        ax.set_xlabel(spec.get("xlabel") or (x if t != "barh" else y), fontsize=9)
        ax.set_ylabel(spec.get("ylabel") or (y if t not in ("barh", "hist") else (x if t == "barh" else "Count")),
                      fontsize=9)
        if spec.get("log_y") and t in ("bar", "line", "scatter"):
            ax.set_yscale("symlog")
        if t == "bar":
            longest = max(len(str(v)) for v in d[x])
            if spec.get("rotate_labels") or n > 6 or longest > 8:
                plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
        if t == "line":
            xs_num = pd.to_numeric(d[x], errors="coerce")
            if xs_num.notna().all() and (xs_num % 1 == 0).all():
                ax.xaxis.set_major_locator(MaxNLocator(integer=True))
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v)}"))
            else:
                fig.autofmt_xdate()
        fmt = FuncFormatter(lambda v, _: f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}")
        if t == "barh":
            ax.xaxis.set_major_formatter(fmt)
        elif t in ("bar", "line", "hist") and not spec.get("log_y"):
            ax.yaxis.set_major_formatter(fmt)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y" if t != "barh" else "x", alpha=0.25)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)


def _padded_limits(vals):
    lo, hi = min(0, min(vals)), max(0, max(vals))
    pad = (hi - lo) * 0.12 or 1
    return (lo - pad if lo < 0 else 0, hi + pad if hi > 0 else 0 + pad * 0.2)


def _short(v: float) -> str:
    av = abs(v)
    if av >= 1e6:
        return f"{v / 1e6:.1f}M"
    if av >= 1e3:
        return f"{v / 1e3:.1f}k"
    if float(v).is_integer():
        return f"{int(v)}"
    return f"{v:.2f}" if av < 10 else f"{v:.1f}"

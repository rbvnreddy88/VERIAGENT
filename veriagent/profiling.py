"""Dataset loading, light cleaning and profiling (shared context for every agent)."""
from __future__ import annotations

import warnings

import pandas as pd


def load_csv(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return prepare(df)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Strip column names and parse obvious date columns so agents get clean types."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            name_hint = any(k in c.lower() for k in ("date", "time", "day", "month", "period"))
            sample = df[c].dropna().astype(str).head(200)
            if sample.empty:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if name_hint and parsed.notna().mean() > 0.9:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df[c] = pd.to_datetime(df[c], errors="coerce", format="mixed")
    return df


def profile(df: pd.DataFrame, max_cats: int = 8) -> dict:
    cols = []
    for c in df.columns:
        s = df[c]
        info = {"name": c, "dtype": str(s.dtype), "missing": int(s.isna().sum()),
                "unique": int(s.nunique(dropna=True))}
        if pd.api.types.is_numeric_dtype(s):
            d = s.describe()
            info.update(kind="numeric", min=_r(d.get("min")), max=_r(d.get("max")),
                        mean=_r(d.get("mean")))
        elif pd.api.types.is_datetime64_any_dtype(s):
            info.update(kind="datetime", min=str(s.min())[:10], max=str(s.max())[:10])
        else:
            info.update(kind="categorical",
                        top=[str(x) for x in s.value_counts().head(max_cats).index])
        cols.append(info)
    return {"rows": int(len(df)), "columns": cols}


def _r(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def schema_text(df: pd.DataFrame, prof: dict | None = None, sample_rows: int = 5) -> str:
    prof = prof or profile(df)
    lines = [f"Rows: {prof['rows']}", "Columns:"]
    for c in prof["columns"]:
        extra = ""
        if c["kind"] == "numeric":
            extra = f"min={c['min']}, max={c['max']}, mean={c['mean']}"
        elif c["kind"] == "datetime":
            extra = f"from {c['min']} to {c['max']}"
        else:
            extra = f"{c['unique']} values, e.g. {', '.join(c['top'][:6])}"
        lines.append(f"  - {c['name']} ({c['kind']}, {c['dtype']}, missing={c['missing']}): {extra}")
    with pd.option_context("display.max_columns", 30, "display.width", 200):
        lines.append("Sample rows:\n" + df.head(sample_rows).to_string(index=False))
    return "\n".join(lines)

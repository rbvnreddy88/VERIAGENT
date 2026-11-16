"""Restricted execution of LLM-written pandas code.

Contract for analysis code:
  * a pandas DataFrame `df` is provided (a copy of the uploaded data)
  * the code must set `findings` to a list of dicts:
        {"statement": str, "value": number, "unit": str}
  * it may set `table` to a small DataFrame (<= 60 rows) to be charted
Safety: AST check (no file, OS, network or dunder access), whitelisted imports,
separate process, time limit, and a memory limit where the OS allows it.
"""
from __future__ import annotations

import ast
import io
import math
import multiprocessing as mp
import os
import pickle
import tempfile
import traceback
from contextlib import redirect_stdout

ALLOWED_MODULES = {"pandas", "numpy", "math", "statistics", "scipy", "scipy.stats", "datetime",
                   "re", "collections", "itertools", "functools"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals",
                   "vars", "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help",
                   "memoryview", "os", "sys", "subprocess", "socket", "shutil", "pathlib"}
FORBIDDEN_ATTR_PREFIX = ("read_", "to_csv", "to_excel", "to_pickle", "to_parquet", "to_sql",
                         "to_hdf", "to_feather", "to_stata", "to_clipboard", "to_json", "to_html",
                         "to_latex", "to_markdown", "to_xml", "to_orc", "savefig", "system")
MAX_TABLE_ROWS = 60


class SandboxViolation(Exception):
    pass


def check_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxViolation(f"SyntaxError: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                if n not in ALLOWED_MODULES and n.split(".")[0] not in {"pandas", "numpy", "scipy"}:
                    raise SandboxViolation(f"Import of '{n}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise SandboxViolation(f"Use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise SandboxViolation("Access to dunder attributes is not allowed")
            if node.attr.startswith(FORBIDDEN_ATTR_PREFIX):
                raise SandboxViolation(f"'.{node.attr}' is not allowed (no file or system access)")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in ALLOWED_MODULES and name.split(".")[0] not in {"pandas", "numpy", "scipy"}:
        raise ImportError(f"Import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


_SAFE_BUILTIN_NAMES = ["abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
                       "isinstance", "len", "list", "map", "max", "min", "print", "range", "reversed",
                       "round", "set", "sorted", "str", "sum", "tuple", "zip", "ValueError",
                       "KeyError", "TypeError", "Exception", "ZeroDivisionError", "IndexError",
                       "None", "True", "False", "divmod", "pow", "frozenset", "hasattr", "slice",
                       "type"]


def _to_number(v):
    try:
        import numpy as np

        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            v = float(v)
    except ImportError:
        pass
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("value is NaN or infinite")
        return v
    raise ValueError(f"value must be a number, got {type(v).__name__}")


def _worker(code: str, df_path: str, conn) -> None:
    try:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 ** 3, 3 * 1024 ** 3))
        except Exception:
            pass
        import builtins

        import numpy as np
        import pandas as pd

        with open(df_path, "rb") as f:
            df = pickle.load(f)
        safe_builtins = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(builtins, n)}
        safe_builtins["__import__"] = _safe_import
        env = {"__builtins__": safe_builtins, "df": df, "pd": pd, "np": np, "math": math}
        out = io.StringIO()
        with redirect_stdout(out):
            exec(compile(code, "<analysis>", "exec"), env)  # noqa: S102 (sandboxed)

        findings = env.get("findings")
        if not isinstance(findings, list) or not findings:
            raise ValueError("Code must set `findings` to a non-empty list of dicts")
        clean = []
        for f in findings:
            if not isinstance(f, dict) or "statement" not in f or "value" not in f:
                raise ValueError("Each finding needs 'statement' and 'value'")
            clean.append({"statement": str(f["statement"])[:300], "value": _to_number(f["value"]),
                          "unit": str(f.get("unit", "") or "")[:20]})
        table = env.get("table")
        tbl = None
        if table is not None:
            if isinstance(table, pd.Series):
                table = table.reset_index()
            if not isinstance(table, pd.DataFrame):
                raise ValueError("`table` must be a pandas DataFrame")
            if isinstance(table.index, pd.RangeIndex):
                table = table.reset_index(drop=True)
            else:
                table = table.reset_index()
            table = table.head(MAX_TABLE_ROWS).copy()
            table.columns = [str(c) for c in table.columns]
            for c in table.columns:
                if pd.api.types.is_datetime64_any_dtype(table[c]):
                    table[c] = table[c].dt.strftime("%Y-%m-%d")
                elif isinstance(table[c].dtype, pd.PeriodDtype):
                    table[c] = table[c].astype(str)
            rows = table.astype(object).where(pd.notna(table), None).values.tolist()
            tbl = {"columns": list(table.columns), "rows": [[_jsonable(v) for v in r] for r in rows]}
        conn.send({"ok": True, "findings": clean, "table": tbl, "stdout": out.getvalue()[-2000:]})
    except BaseException as e:  # report every failure back to the agent
        tb = traceback.format_exc(limit=3)
        conn.send({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb[-1500:]})
    finally:
        conn.close()


def _jsonable(v):
    try:
        import numpy as np

        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.floating):
            return None if not math.isfinite(float(v)) else float(v)
    except ImportError:
        pass
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


def run_code(code: str, df, timeout: int = 20) -> dict:
    """Run analysis code against `df`. Returns {"ok": bool, ...}."""
    try:
        check_code(code)
    except SandboxViolation as e:
        return {"ok": False, "error": f"SandboxViolation: {e}", "trace": ""}

    fd, path = tempfile.mkstemp(suffix=".pkl")
    os.close(fd)
    with open(path, "wb") as f:
        pickle.dump(df, f)
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(code, path, child), daemon=True)
    try:
        proc.start()
        child.close()
        if parent.poll(timeout):
            result = parent.recv()
        else:
            result = {"ok": False, "error": f"Timeout: code ran longer than {timeout}s", "trace": ""}
        proc.join(1)
        if proc.is_alive():
            proc.kill()
        return result
    except EOFError:
        return {"ok": False, "error": "Sandbox process crashed (possibly out of memory)", "trace": ""}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

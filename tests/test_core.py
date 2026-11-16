"""Unit tests for the sandbox, evidence memory, numbers, renderer and verifier."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EVIDENCE_BACKEND"] = "tfidf"

from veriagent.agents import reporter, verifier  # noqa: E402
from veriagent.charts import render  # noqa: E402
from veriagent.config import MODES, Settings  # noqa: E402
from veriagent.evidence import EvidenceStore  # noqa: E402
from veriagent.numbers import compute_op, find_numbers, format_value, matches  # noqa: E402
from veriagent.sandbox import run_code  # noqa: E402

DF = pd.DataFrame({"region": ["N", "S", "C"], "profit": [100.0, 80.0, -20.0]})


# ---------------------------------------------------------------- sandbox
def test_sandbox_runs_valid_code():
    r = run_code("findings=[{'statement':'total','value':float(df.profit.sum()),'unit':'USD'}]\n"
                 "table=df[['region','profit']]", DF)
    assert r["ok"] and r["findings"][0]["value"] == 160.0
    assert r["table"]["columns"] == ["region", "profit"]


@pytest.mark.parametrize("code", [
    "import os\nfindings=[]",
    "open('x.txt','w')",
    "df.to_csv('x.csv')",
    "x=().__class__.__bases__",
    "import subprocess",
    "pd.read_csv('secret.csv')",
])
def test_sandbox_blocks_unsafe_code(code):
    r = run_code(code, DF)
    assert not r["ok"] and "SandboxViolation" in r["error"]


def test_sandbox_timeout():
    r = run_code("while True:\n    pass", DF, timeout=2)
    assert not r["ok"] and "Timeout" in r["error"]


def test_sandbox_requires_findings():
    r = run_code("x = 1", DF)
    assert not r["ok"] and "findings" in r["error"]


# ---------------------------------------------------------------- evidence
def test_evidence_store_add_get_search(tmp_path):
    s = EvidenceStore("tfidf")
    a = s.add("Total profit in 2023", 56431.9, "USD", "code", "T1")
    s.add("Average discount in 2023", 0.2, "", "code", "T1")
    assert a == "EV-001" and s.get("ev-001")["value"] == 56431.9
    assert s.search("profit 2023", k=1)[0]["id"] == "EV-001"
    s.save(tmp_path / "e.json")
    s2 = EvidenceStore.load(tmp_path / "e.json")
    assert s2.get("EV-002")["value"] == 0.2


# ---------------------------------------------------------------- numbers
def test_number_parsing_and_years():
    nums = find_numbers("Profit fell 16.8% from $49,544 in 2022 to 41,200.")
    assert [n["value"] for n in nums] == [16.8, 49544.0, 41200.0]
    assert nums[0]["is_pct"] and nums[1]["currency"] == "$"


def test_matches_respects_rounding():
    assert matches(16.8, 16.84, 1)
    assert not matches(21.0, 16.84, 0)
    assert matches(41200, 41187.3, 0)          # within 1%


def test_compute_ops():
    assert round(compute_op("pct_change", [49544, 41200]), 1) == -16.8
    assert compute_op("ratio", [10, 4]) == 2.5
    assert compute_op("share", [1, 4]) == 25.0
    assert compute_op("ratio", [1, 0]) is None


def test_format_value():
    assert format_value(41200.0, "USD") == "$41,200"
    assert format_value(16.84, "%") == "16.8%"
    assert format_value(-4120.55, "") == "-4,121"


# ---------------------------------------------------------------- charts
def test_renderer_handles_negative_bars():
    png = render({"type": "bar", "x": "region", "y": "profit", "title": "t"},
                 {"columns": ["region", "profit"], "rows": [["N", 100], ["C", -20]]})
    assert png[:4] == b"\x89PNG"


# ---------------------------------------------------------------- verifier
class FakeLLM:
    """Returns a fixed extraction so the test checks the verifier's arithmetic."""

    def __init__(self, reply):
        self.reply = reply

    def chat(self, *a, **k):
        return self.reply


def _ctx(reply, mode="full"):
    store = EvidenceStore("tfidf")
    store.add("Total profit in 2022", 49544, "USD", "c", "T1")   # EV-001
    store.add("Total profit in 2023", 41200, "USD", "c", "T1")   # EV-002
    return SimpleNamespace(store=store, llm=FakeLLM(reply), settings=Settings(),
                           mode=MODES[mode], results_text=[])


def test_placeholders_render_and_verify():
    ctx = _ctx({"claims": [], "qualitative": []})
    md, bound = reporter.render(ctx, "Profit fell by {{pct_change:EV-001,EV-002}} to {{EV-002}}.", [])
    assert "16.8%" in md and "$41,200" in md
    _, log, unsupported = verifier.verify(ctx, md, bound)
    assert all(e["status"] == "verified" for e in log) and not unsupported


def test_wrong_free_number_is_corrected():
    ctx = _ctx({"claims": [{"nid": 0, "op": "pct_change", "operands": ["EV-001", "EV-002"]}]})
    md, log, _ = verifier.verify(ctx, "Profit dropped by 21% [EV-001, EV-002].", [])
    assert "16.8%" in md and "21%" not in md
    assert log[0]["status"] == "corrected"


def test_direction_error_is_flagged():
    ctx = _ctx({"claims": [{"nid": 0, "op": "pct_change", "operands": ["EV-001", "EV-002"]}]})
    md, log, _ = verifier.verify(ctx, "Profit grew by 16.8% [EV-001, EV-002].", [])
    assert log[0]["status"] == "flagged" and "direction contradicts data" in md


def test_unsupported_number_is_flagged():
    ctx = _ctx({"claims": [{"nid": 0, "op": "unsupported", "operands": []}]})
    md, log, unsupported = verifier.verify(ctx, "Customer satisfaction reached 92%.", [])
    assert log[0]["status"] == "unsupported" and "[unverified]" in md and unsupported


def test_missing_evidence_placeholder_is_unsupported():
    ctx = _ctx({"claims": []})
    md, bound = reporter.render(ctx, "Sales were {{EV-099}}.", [])
    _, log, unsupported = verifier.verify(ctx, md, bound)
    assert log[0]["status"] == "unsupported" and unsupported


def test_qualitative_doubled_checked():
    ctx = _ctx({"claims": [], "qualitative": [{"sid": 0, "phrase": "doubled", "operands": ["EV-002", "EV-001"]}]})
    _, log, _ = verifier.verify(ctx, "Profit doubled.", [])
    assert log[0]["status"] == "flagged"

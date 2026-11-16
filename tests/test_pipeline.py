"""Integration tests: every mode end to end with the mock LLM, the scorer, and the API."""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EVIDENCE_BACKEND"] = "tfidf"

from veriagent.config import MODES, Settings  # noqa: E402
from veriagent.pipeline import run  # noqa: E402
from veriagent.profiling import load_csv  # noqa: E402

DATA = ROOT / "eval" / "datasets" / "retail_sales.csv"


@pytest.fixture(scope="module")
def df():
    if not DATA.exists():
        import build_datasets

        build_datasets.main()
    return load_csv(DATA)


@pytest.mark.parametrize("mode", list(MODES))
def test_every_mode_runs(df, tmp_path, mode):
    r = run(df, "Why did profit fall in 2023 by region?", mode, Settings(provider="mock"), run_dir=tmp_path)
    assert r["report"].strip()
    for f in ("report.md", "report.html", "run.json", "verification.json", "evidence.json"):
        assert (tmp_path / f).exists()
    if MODES[mode].verifier:
        assert r["verification_summary"]["claims_checked"] > 0
    assert any(c["ok"] for c in r["charts"])


def test_full_mode_corrects_and_uses_critic(df, tmp_path):
    r = run(df, "Why did profit fall in 2023 by region?", "full", Settings(provider="mock"), run_dir=tmp_path)
    assert r["verification_summary"]["corrected"] >= 1          # mock writer makes one ratio error
    assert any(c.get("critic", {}).get("verdict") == "revise" for c in r["chart_versions"])
    assert "[EV-" in r["report"]


def test_benchmark_ground_truth(df):
    from benchmark import TASKS

    assert len(TASKS) == 50
    frames = {}
    for t in TASKS:
        d = frames.setdefault(t["dataset"], load_csv(ROOT / "eval" / "datasets" / t["dataset"]))
        ans = t["answers"](d)
        assert ans and all(v == v for v in ans.values())   # no NaN


def test_task_success_scoring():
    from scoring import task_success

    ok, _ = task_success("Profit fell 31.5% from $82,411 to $56,432 [EV-001].",
                         {"a": 82410.69, "b": 56431.94, "c": -31.52})
    assert ok
    ok, found = task_success("Profit was 60,000.", {"a": 56431.94})
    assert not ok


def test_api_flow():
    from fastapi.testclient import TestClient

    from api.main import app

    c = TestClient(app)
    assert c.get("/api/config").json()["mock"] is True
    ds = c.post("/api/datasets/sample/retail_sales.csv").json()
    rid = c.post("/api/runs", json={"dataset_id": ds["dataset_id"], "goal": "Compare profit by region",
                                    "mode": "full"}).json()["run_id"]
    for _ in range(120):
        st = c.get(f"/api/runs/{rid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.5)
    assert st["status"] == "done", st.get("error")
    chart = st["result"]["charts"][0]["file"]
    assert c.get(f"/api/runs/{rid}/files/{chart}").status_code == 200
    assert c.get(f"/api/runs/{rid}/files/../../api/main.py").status_code == 404

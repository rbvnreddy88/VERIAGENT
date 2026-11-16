"""FastAPI backend.

    uvicorn api.main:app --reload --port 8000

Endpoints
  GET  /api/config                     provider, models, modes
  GET  /api/samples                    benchmark datasets you can try
  POST /api/datasets/upload            upload a CSV
  POST /api/datasets/sample/{name}     use a benchmark dataset
  POST /api/runs                       start a run {dataset_id, goal, mode}
  GET  /api/runs/{run_id}              status, live events and (when done) the result
  WS   /api/runs/{run_id}/ws           live agent events
  GET  /api/runs/{run_id}/files/{path} charts, report.html, evidence.json ...
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from veriagent.config import MODES, Settings  # noqa: E402
from veriagent.pipeline import run as run_pipeline  # noqa: E402
from veriagent.profiling import load_csv, profile  # noqa: E402

UPLOADS = ROOT / "uploads"
RUNS_DIR = ROOT / "runs"
SAMPLES = ROOT / "eval" / "datasets"
UPLOADS.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="VeriAgent API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATASETS: dict[str, dict] = {}
RUNS: dict[str, dict] = {}


def _register(path: Path, name: str) -> dict:
    df = load_csv(path)
    did = uuid.uuid4().hex[:10]
    preview = df.head(8).astype(str).to_dict(orient="records")
    DATASETS[did] = {"path": str(path), "name": name}
    return {"dataset_id": did, "name": name, "rows": len(df), "columns": list(df.columns),
            "preview": preview, "profile": profile(df)}


@app.get("/api/config")
def config():
    s = Settings()
    return {"provider": s.provider, "text_model": s.text_model, "vision_model": s.vision_model,
            "modes": list(MODES), "mock": s.provider == "mock"}


@app.get("/api/samples")
def samples():
    return [p.name for p in sorted(SAMPLES.glob("*.csv"))]


@app.post("/api/datasets/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a .csv file")
    dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        return _register(dest, file.filename)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read CSV: {e}") from e


@app.post("/api/datasets/sample/{name}")
def use_sample(name: str):
    p = SAMPLES / Path(name).name
    if not p.exists():
        raise HTTPException(404, "Unknown sample")
    return _register(p, name)


class RunRequest(BaseModel):
    dataset_id: str
    goal: str
    mode: str = "full"


@app.post("/api/runs")
def start_run(req: RunRequest):
    if req.dataset_id not in DATASETS:
        raise HTTPException(404, "Unknown dataset_id")
    if req.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    if not req.goal.strip():
        raise HTTPException(400, "Please enter a question")
    rid = uuid.uuid4().hex[:10]
    run_dir = RUNS_DIR / rid
    rec = {"run_id": rid, "status": "running", "events": [], "result": None, "error": None,
           "goal": req.goal, "mode": req.mode, "dir": str(run_dir), "started": time.time()}
    RUNS[rid] = rec

    def emit(agent, status, data):
        rec["events"].append({"t": round(time.time() - rec["started"], 2), "agent": agent,
                              "status": status, "data": data})

    def work():
        try:
            df = load_csv(DATASETS[req.dataset_id]["path"])
            res = run_pipeline(df, req.goal, req.mode, Settings(), run_dir=run_dir, emit=emit,
                               namespace=f"ui-{rid}")
            rec["result"] = {k: v for k, v in res.items() if k != "run_dir"}
            rec["status"] = "done"
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["trace"] = traceback.format_exc()[-3000:]
            rec["status"] = "error"
            emit("pipeline", "error", {"error": rec["error"]})

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": rid}


@app.get("/api/runs/{rid}")
def get_run(rid: str, since: int = 0):
    rec = RUNS.get(rid)
    if not rec:
        raise HTTPException(404, "Unknown run")
    return {**{k: v for k, v in rec.items() if k not in ("events", "dir")}, "events": rec["events"][since:],
            "n_events": len(rec["events"])}


@app.websocket("/api/runs/{rid}/ws")
async def run_ws(ws: WebSocket, rid: str):
    await ws.accept()
    sent = 0
    try:
        while True:
            rec = RUNS.get(rid)
            if not rec:
                await ws.send_json({"type": "error", "error": "unknown run"})
                break
            while sent < len(rec["events"]):
                await ws.send_json({"type": "event", **rec["events"][sent]})
                sent += 1
            if rec["status"] != "running":
                await ws.send_json({"type": "status", "status": rec["status"], "error": rec["error"]})
                break
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.get("/api/runs/{rid}/files/{path:path}")
def run_file(rid: str, path: str):
    rec = RUNS.get(rid)
    base = (Path(rec["dir"]) if rec else RUNS_DIR / rid).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")

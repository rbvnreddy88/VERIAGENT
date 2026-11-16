"""Per-run context shared by all agents (heavy objects kept out of the graph state)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from .config import Mode, Settings
from .evidence import EvidenceStore, dataset_hash
from .llm import LLM
from .profiling import profile, schema_text


@dataclass
class RunContext:
    settings: Settings
    mode: Mode
    df: pd.DataFrame
    goal: str
    run_dir: Path
    llm: LLM
    store: EvidenceStore
    profile: dict
    schema: str
    ds_hash: str
    emit: Callable[[str, str, dict], None] = lambda *a: None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    results_text: list[str] = field(default_factory=list)   # used when memory is off
    tables: dict = field(default_factory=dict)               # task_id -> table
    timings: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    @classmethod
    def create(cls, settings: Settings, mode: Mode, df: pd.DataFrame, goal: str, run_dir: Path,
               emit=None, namespace: str = "", llm: LLM | None = None) -> "RunContext":
        run_dir = Path(run_dir)
        (run_dir / "charts").mkdir(parents=True, exist_ok=True)
        prof = profile(df)
        return cls(settings=settings, mode=mode, df=df, goal=goal, run_dir=run_dir,
                   llm=llm or LLM(settings, namespace=namespace),
                   store=EvidenceStore(settings.evidence_backend),
                   profile=prof, schema=schema_text(df, prof), ds_hash=dataset_hash(df),
                   emit=emit or (lambda *a: None))

    def event(self, agent: str, status: str, **data):
        self.emit(agent, status, data)

    def timed(self, name: str):
        ctx = self

        class _T:
            def __enter__(self):
                self.t = time.time()
                ctx.event(name, "start")

            def __exit__(self, *exc):
                ctx.timings[name] = round(ctx.timings.get(name, 0) + time.time() - self.t, 3)
                ctx.event(name, "error" if exc[0] else "done", seconds=ctx.timings[name])
                return False

        return _T()

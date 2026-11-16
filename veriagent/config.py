"""Settings and experiment modes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "text": "gemini-3.1-flash-lite",
        "vision": "gemini-3.1-flash-lite",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "text": "llama-3.3-70b-versatile",
        "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
    "openai": {
        "base_url": None,
        "key_env": "OPENAI_API_KEY",
        "text": "gpt-4o-mini",
        "vision": "gpt-4o-mini",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "text": "llama3.1:8b",
        "vision": "llama3.2-vision",
    },
    "mock": {"base_url": None, "key_env": None, "text": "mock", "vision": "mock"},
}


@dataclass(frozen=True)
class Mode:
    """Which components are switched on. Used for baselines and ablations."""

    name: str
    single_agent: bool = False      # B1: one LLM context does everything
    planner: bool = True
    critic: bool = True             # C2: multimodal chart critic
    memory: bool = True             # C1: shared evidence memory
    evidence_bound: bool = True     # C3a: numbers written as evidence placeholders
    verifier: bool = True           # C3b: claim-level verifier


MODES = {
    "full": Mode("full"),
    "B1": Mode("B1", single_agent=True, planner=False, critic=False, memory=False,
               evidence_bound=False, verifier=False),
    "B2": Mode("B2", critic=False, memory=False, evidence_bound=False, verifier=False),
    "no_verifier": Mode("no_verifier", evidence_bound=False, verifier=False),
    "no_critic": Mode("no_critic", critic=False),
    "no_memory": Mode("no_memory", memory=False, evidence_bound=False),
}


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock").strip().lower())
    text_model: str = field(default_factory=lambda: os.getenv("TEXT_MODEL", "").strip())
    vision_model: str = field(default_factory=lambda: os.getenv("VISION_MODEL", "").strip())
    temperature: float = field(default_factory=lambda: float(os.getenv("TEMPERATURE", "0")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    evidence_backend: str = field(default_factory=lambda: os.getenv("EVIDENCE_BACKEND", "auto"))
    request_delay: float = field(default_factory=lambda: float(os.getenv("REQUEST_DELAY", "0")))
    sandbox_timeout: int = 20
    tolerance: float = 0.01          # relative tolerance for verifying numbers
    max_tasks: int = 6
    unsupported_action: str = "flag"  # flag | remove
    cache_dir: Path = ROOT / ".cache" / "llm"
    runs_dir: Path = ROOT / "runs"

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unknown LLM_PROVIDER '{self.provider}'. Use one of {list(PROVIDERS)}")
        p = PROVIDERS[self.provider]
        self.text_model = self.text_model or p["text"]
        self.vision_model = self.vision_model or p["vision"]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)

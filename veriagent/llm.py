"""LLM client for any OpenAI-compatible endpoint (Gemini, Groq, OpenAI, Ollama),
with JSON parsing, retries, rate-limit backoff, response caching and token counting."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .config import PROVIDERS, Settings


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Could not parse JSON from model output: {text[:300]}")


class Usage:
    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "total_tokens": self.total}


class LLM:
    """Every agent calls `chat(role, system, user, json=..., image_png=...)`.
    `role` is a short agent name, used for logging and by the mock model."""

    def __init__(self, settings: Settings, namespace: str = "", use_cache: bool = True):
        self.s = settings
        self.namespace = namespace
        self.use_cache = use_cache
        self.usage = Usage()
        self.log: list[dict] = []
        if settings.provider == "mock":
            from .mock_llm import MockBackend

            self._mock = MockBackend()
            self._client = None
        else:
            from openai import OpenAI

            p = PROVIDERS[settings.provider]
            key = os.getenv(p["key_env"], "") if p["key_env"] else "ollama"
            if not key:
                raise LLMError(f"{p['key_env']} is not set. Add it to your .env file.")
            self._client = OpenAI(api_key=key, base_url=p["base_url"]) if p["base_url"] \
                else OpenAI(api_key=key)
            self._mock = None

    # ------------------------------------------------------------------
    def chat(self, role: str, system: str, user: str, *, json: bool = False,
             image_png: bytes | None = None, history: list[dict] | None = None) -> Any:
        model = self.s.vision_model if image_png else self.s.text_model
        messages = [{"role": "system", "content": system}]
        messages += history or []
        if image_png:
            b64 = base64.b64encode(image_png).decode()
            messages.append({"role": "user", "content": [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]})
        else:
            messages.append({"role": "user", "content": user})

        key = self._cache_key(model, messages, json)
        cached = self._cache_get(key)
        if cached is not None:
            text = cached["text"]
            self.usage.calls += 1
            self.usage.prompt_tokens += cached.get("pt", 0)
            self.usage.completion_tokens += cached.get("ct", 0)
        else:
            text, pt, ct = self._call(role, model, messages, json, image_png)
            self.usage.calls += 1
            self.usage.prompt_tokens += pt
            self.usage.completion_tokens += ct
            self._cache_put(key, {"text": text, "pt": pt, "ct": ct})
        self.log.append({"role": role, "model": model, "chars_in": len(user), "chars_out": len(text)})

        if not json:
            return text
        try:
            return parse_json(text)
        except LLMError:
            # one repair attempt: ask the model to return valid JSON only
            fix = self._call(role, model, messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Your reply was not valid JSON. Reply again with valid JSON only."},
            ], True, image_png)[0]
            return parse_json(fix)

    # ------------------------------------------------------------------
    def _call(self, role, model, messages, json_mode, image_png):
        if self._mock is not None:
            text = self._mock.respond(role, messages, image_png)
            pt = sum(len(str(m["content"])) for m in messages) // 4
            return text, pt, len(text) // 4

        delay = 2.0
        last_err = None
        for attempt in range(6):
            try:
                if self.s.request_delay:
                    time.sleep(self.s.request_delay)
                kwargs = dict(model=model, messages=messages, temperature=self.s.temperature)
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self._client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                u = getattr(resp, "usage", None)
                pt = getattr(u, "prompt_tokens", 0) or 0
                ct = getattr(u, "completion_tokens", 0) or 0
                return text, pt, ct
            except Exception as e:  # rate limits and transient errors
                last_err = e
                msg = str(e).lower()
                if json_mode and "response_format" in msg:
                    json_mode = False
                    continue
                if any(k in msg for k in ("429", "rate", "quota", "timeout", "503", "502", "overloaded")):
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise LLMError(f"{role}: LLM call failed: {e}") from e
        raise LLMError(f"{role}: LLM call failed after retries: {last_err}")

    # ------------------------------------------------------------------
    def _cache_key(self, model, messages, json_mode) -> str:
        blob = json.dumps([self.s.provider, model, self.s.temperature, json_mode,
                           self.namespace, messages], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return Path(self.s.cache_dir) / key[:2] / f"{key}.json"

    def _cache_get(self, key):
        if not self.use_cache or self.s.provider == "mock":
            return None
        p = self._cache_path(key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _cache_put(self, key, value):
        if not self.use_cache or self.s.provider == "mock":
            return
        p = self._cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding="utf-8")

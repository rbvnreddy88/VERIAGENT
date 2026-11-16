"""C1: Shared evidence memory.

Every value the analysis agent computes is stored with an evidence ID, the
statement it answers, its unit, the exact code that produced it and a hash of
the dataset. Exact lookups use the ID; semantic lookups use a vector index
(ChromaDB, or a TF-IDF fallback when ChromaDB's embedding model is unavailable).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path


def dataset_hash(df) -> str:
    import pandas as pd

    h = pd.util.hash_pandas_object(df, index=True).values
    return hashlib.sha256(h.tobytes()).hexdigest()[:16]


class _TfidfIndex:
    def __init__(self):
        self.ids: list[str] = []
        self.docs: list[str] = []
        self._vec = None
        self._mat = None

    def add(self, eid: str, text: str):
        self.ids.append(eid)
        self.docs.append(text)
        self._vec = None

    def search(self, query: str, k: int) -> list[str]:
        if not self.docs:
            return []
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        if self._vec is None:
            self._vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit(self.docs)
            self._mat = self._vec.transform(self.docs)
        sims = cosine_similarity(self._vec.transform([query]), self._mat)[0]
        order = sims.argsort()[::-1][:k]
        return [self.ids[i] for i in order if sims[i] > 0]


class _ChromaIndex:
    def __init__(self, path: Path | None):
        import chromadb

        self._client = chromadb.PersistentClient(str(path)) if path else chromadb.EphemeralClient()
        self._col = self._client.get_or_create_collection(f"ev-{uuid.uuid4().hex[:12]}")
        self._col.add(ids=["__probe__"], documents=["probe"])  # fails fast if embeddings unavailable
        self._col.delete(ids=["__probe__"])

    def add(self, eid: str, text: str):
        self._col.add(ids=[eid], documents=[text])

    def search(self, query: str, k: int) -> list[str]:
        n = self._col.count()
        if n == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=min(k, n))
        return res["ids"][0]


class EvidenceStore:
    def __init__(self, backend: str = "auto", path: Path | None = None):
        self.records: dict[str, dict] = {}
        self._n = 0
        self.backend = "tfidf"
        self._index = None
        if backend in ("auto", "chroma"):
            try:
                self._index = _ChromaIndex(path)
                self.backend = "chroma"
            except Exception:
                if backend == "chroma":
                    raise
        if self._index is None:
            self._index = _TfidfIndex()

    # ----------------------------------------------------------------
    def add(self, statement: str, value: float, unit: str, code: str, task_id: str,
            ds_hash: str = "") -> str:
        self._n += 1
        eid = f"EV-{self._n:03d}"
        self.records[eid] = {"id": eid, "task_id": task_id, "statement": statement,
                             "value": value, "unit": unit, "code": code, "dataset_hash": ds_hash}
        self._index.add(eid, f"{statement} ({unit})" if unit else statement)
        return eid

    def get(self, eid: str) -> dict | None:
        return self.records.get(eid.strip().upper())

    def search(self, query: str, k: int = 5) -> list[dict]:
        return [self.records[i] for i in self._index.search(query, k) if i in self.records]

    def all(self) -> list[dict]:
        return list(self.records.values())

    def for_task(self, task_id: str) -> list[dict]:
        return [r for r in self.records.values() if r["task_id"] == task_id]

    def as_prompt(self, include_code: bool = False) -> str:
        lines = []
        for r in self.records.values():
            line = f"{r['id']}: {r['statement']} = {r['value']}{(' ' + r['unit']) if r['unit'] else ''}"
            if include_code:
                line += f"   # code: {r['code'][:120]}"
            lines.append(line)
        return "\n".join(lines)

    # ----------------------------------------------------------------
    def save(self, path: Path):
        Path(path).write_text(json.dumps(list(self.records.values()), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, backend: str = "tfidf") -> "EvidenceStore":
        store = cls(backend=backend)
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            store._n = max(store._n, int(r["id"].split("-")[1]))
            store.records[r["id"]] = r
            store._index.add(r["id"], r["statement"])
        return store

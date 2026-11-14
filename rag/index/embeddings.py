"""Open embedding + reranker backends (CPU-friendly, no torch required).

backend (config rag.embedding_backend or env RAG_EMBED_BACKEND):
  fastembed  ONNX models (default; works on Linux and macOS without torch)
  ollama     Ollama /api/embed (e.g. nomic-embed-text)
  hash       deterministic hashed bag-of-words — CI/smoke only, NOT for reported results
Embeddings are cached on disk keyed by (model, text hash).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np

CACHE_DIR = Path(os.environ.get("RAG_CACHE_DIR", Path(__file__).resolve().parents[2] / "data" / "processed" / "emb_cache"))


class Embedder:
    """Embeddings cached on disk (.npz) and held in memory as float32 arrays (≈10x smaller than float lists)."""

    def __init__(self, backend: str, model: str):
        self.backend, self.model = backend, model
        self._impl = None
        self._cache: dict[str, np.ndarray] = {}
        stem = CACHE_DIR / f"{backend}_{re.sub(r'[^A-Za-z0-9]+', '_', model)}"
        self._cache_path = stem.with_suffix(".npz")
        if self._cache_path.exists():
            with np.load(self._cache_path, allow_pickle=False) as z:
                self._cache = dict(zip(z["keys"].tolist(), z["vecs"]))
        elif stem.with_suffix(".json").exists():          # migrate the old JSON cache once
            self._cache = {k: np.asarray(v, dtype=np.float32)
                           for k, v in json.loads(stem.with_suffix(".json").read_text()).items()}
            _save_npz(self._cache_path, self._cache)

    @property
    def name(self) -> str:
        return f"{self.backend}:{self.model}"

    def _raw(self, texts: list[str], is_query: bool) -> np.ndarray:
        if self.backend == "hash":
            return np.stack([_hash_vec(t) for t in texts])
        if self.backend == "fastembed":
            if self._impl is None:
                from fastembed import TextEmbedding
                threads = int(os.environ["FASTEMBED_THREADS"]) if os.environ.get("FASTEMBED_THREADS") else None
                self._impl = TextEmbedding(model_name=self.model, threads=threads)
            fn = self._impl.query_embed if is_query else self._impl.passage_embed
            return np.stack(list(fn(texts)))
        if self.backend == "ollama":
            import httpx
            url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/embed"
            r = httpx.post(url, json={"model": self.model, "input": texts}, timeout=120)
            r.raise_for_status()
            return np.array(r.json()["embeddings"], dtype=np.float32)
        raise ValueError(f"unknown embedding backend {self.backend}")

    def embed(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        keys = [("q:" if is_query else "p:") + hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        for start in range(0, len(missing), 64):
            idx = missing[start:start + 64]
            vecs = self._raw([texts[i] for i in idx], is_query)
            for i, v in zip(idx, vecs):
                self._cache[keys[i]] = np.asarray(v, dtype=np.float32)
        if missing and os.environ.get("RAG_CACHE_READONLY") != "1":
            _save_npz(self._cache_path, self._cache)
        m = np.stack([self._cache[k] for k in keys]).astype(np.float32)
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


class Reranker:
    """Cross-encoder scores, cached on disk per (model, query, passage)."""

    def __init__(self, backend: str, model: str):
        self.backend, self.model, self._impl = backend, model, None
        self._cache_path = CACHE_DIR / f"rerank_{backend}_{re.sub(r'[^A-Za-z0-9]+', '_', model)}.json"
        self._cache: dict[str, float] = json.loads(self._cache_path.read_text()) if self._cache_path.exists() else {}
        self._dirty = 0

    def score(self, query: str, passages: list[str]) -> list[float]:
        if self.backend == "hash":   # lexical overlap stand-in for CI
            q = set(_tokens(query))
            return [len(q & set(_tokens(p))) / (1 + len(q)) for p in passages]
        keys = [hashlib.sha1(f"{query}\x00{p}".encode()).hexdigest() for p in passages]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        if missing:
            if self._impl is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self._impl = TextCrossEncoder(model_name=self.model)
            scores = list(self._impl.rerank(query, [passages[i] for i in missing]))
            for i, s in zip(missing, scores):
                self._cache[keys[i]] = round(float(s), 5)
            self._dirty += len(missing)
            if self._dirty > 500:
                self.flush()
        return [self._cache[k] for k in keys]

    def flush(self) -> None:
        if self._dirty:
            _atomic_write(self._cache_path, self._cache)
            self._dirty = 0


def _save_npz(path: Path, cache: dict[str, np.ndarray]) -> None:
    """Merge with entries another process may have written, then replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with np.load(path, allow_pickle=False) as z:
                for k, v in zip(z["keys"].tolist(), z["vecs"]):
                    cache.setdefault(k, v)
        except (OSError, ValueError, KeyError):
            pass
    keys = list(cache)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
    np.savez(tmp, keys=np.array(keys), vecs=np.stack([cache[k] for k in keys]).astype(np.float32))
    os.replace(tmp, path)


def _atomic_write(path: Path, data: dict) -> None:
    """Merge with what another process may have written, then replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        on_disk = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        on_disk = {}
    on_disk.update(data)
    data.update(on_disk)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(on_disk))
    os.replace(tmp, path)


def _tokens(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", t.lower())


def _hash_vec(t: str, dim: int = 512) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    for tok in _tokens(t):
        v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim] += 1
    return v

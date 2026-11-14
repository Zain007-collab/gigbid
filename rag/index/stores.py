"""Index structures: exact dense index (numpy) and BM25."""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from rag.ingest.chunker import Chunk
from rag.index.embeddings import Embedder

_TOK = re.compile(r"[a-z0-9][a-z0-9.+#-]*")


def tokenize(t: str) -> list[str]:
    return [w.strip(".") for w in _TOK.findall(t.lower())]


class DenseIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder, texts: list[str] | None = None):
        self.chunks, self.embedder = chunks, embedder
        self.matrix = embedder.embed(texts or [c.text for c in chunks])

    def search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        q = self.embedder.embed([query], is_query=True)[0]
        sims = self.matrix @ q
        top = np.argsort(-sims)[:k]
        return [(self.chunks[i], float(sims[i])) for i in top]


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75). Keeps exact tool names ('celery', 'dbt') sharp."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [tokenize(c.text) for c in chunks]
        self.avgdl = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        q = tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            dl, s = len(self.docs[i]), 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            scores.append(s)
        top = np.argsort(-np.array(scores))[:k]
        return [(self.chunks[i], float(scores[i])) for i in top if scores[i] > 0]


def rrf(rankings: list[list[tuple[Chunk, float]]], k: int = 60) -> list[tuple[Chunk, float]]:
    """Reciprocal rank fusion over several ranked lists."""
    score: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, (c, _) in enumerate(ranking):
            score[c.chunk_id] = score.get(c.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            by_id[c.chunk_id] = c
    return sorted(((by_id[i], s) for i, s in score.items()), key=lambda x: -x[1])

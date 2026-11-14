"""Retriever factory: the deployed variant is chosen by config (rag.variant), set from eval results (R6)."""
from __future__ import annotations

import importlib
import os
from functools import lru_cache
from pathlib import Path

from agent.settings import section
from rag.index.embeddings import Embedder, Reranker
from rag.ingest.loaders import load_persona
from rag.pipeline import Pipeline

PERSONA_ROOT = Path(__file__).resolve().parents[1] / "data" / "personas"
VARIANTS = ["v0_naive", "v1_decompose", "v2_hybrid", "v3_rerank", "v4_outcome",
            "v5_multivector", "v6_contextual", "v8_crag"]


def embedder() -> Embedder:
    e = section("embeddings")
    return Embedder(os.environ.get("RAG_EMBED_BACKEND", e["backend"]), e["model"])


def reranker() -> Reranker:
    r = section("reranker")
    return Reranker(os.environ.get("RAG_EMBED_BACKEND", r["backend"]), r["model"])


def build(variant: str, persona: str, emb: Embedder | None = None, rr: Reranker | None = None,
          flags_override=None) -> Pipeline:
    mod = importlib.import_module(f"rag.variants.{variant}")
    flags = flags_override or mod.FLAGS
    cfg = section("rag")
    return Pipeline(variant, load_persona(PERSONA_ROOT / persona), flags, emb or embedder(),
                    rr or (reranker() if flags.rerank else None),
                    crag_threshold=float(cfg.get("crag_threshold", 0.0)),
                    rerank_depth=int(cfg.get("rerank_depth", 15)))


@lru_cache(maxsize=8)
def for_persona(persona: str) -> Pipeline:
    """The shipped retriever for a persona (cached; call cache_clear() after re-indexing)."""
    return build(os.environ.get("RAG_VARIANT") or section("rag")["variant"], persona)

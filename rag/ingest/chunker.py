"""Chunkers. V0 uses fixed word windows; other variants keep documents whole when short."""
from __future__ import annotations

from dataclasses import dataclass, field

from rag.ingest.loaders import Doc


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    meta: dict = field(default_factory=dict)


def fixed_chunks(docs: list[Doc], size: int = 120, overlap: int = 20) -> list[Chunk]:
    """Naive: fixed word windows regardless of structure (V0 baseline)."""
    out = []
    for d in docs:
        words = d.text.split()
        step = max(1, size - overlap)
        for i, start in enumerate(range(0, max(len(words), 1), step)):
            piece = " ".join(words[start:start + size])
            if piece:
                out.append(Chunk(f"{d.doc_id}#{i}", d.doc_id, piece, d.meta))
            if start + size >= len(words):
                break
    return out


def doc_chunks(docs: list[Doc], max_words: int = 350) -> list[Chunk]:
    """Structure-aware: one chunk per document, split by paragraphs only if long."""
    out = []
    for d in docs:
        words = d.text.split()
        if len(words) <= max_words:
            out.append(Chunk(f"{d.doc_id}#0", d.doc_id, d.text, d.meta))
            continue
        buf, n = [], 0
        for para in d.text.split("\n\n"):
            buf.append(para)
            if sum(len(p.split()) for p in buf) >= max_words:
                out.append(Chunk(f"{d.doc_id}#{n}", d.doc_id, "\n\n".join(buf), d.meta))
                buf, n = [], n + 1
        if buf:
            out.append(Chunk(f"{d.doc_id}#{n}", d.doc_id, "\n\n".join(buf), d.meta))
    return out

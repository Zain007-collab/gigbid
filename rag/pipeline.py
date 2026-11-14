"""One configurable retrieval pipeline; each variant (rag/variants/vN_*.py) is a flag set.

    decompose     V1  one query per extracted requirement, fused with RRF
    hybrid        V2  BM25 + dense fused with RRF
    rerank        V3  cross-encoder rescoring of the fused top-N
    outcome       V4  boost evidence from proposals that won / got replies
    multivector   V5  README, code, case study and reviews indexed separately, grouped per project
    contextual    V6  prepend a context line (persona, type, project title) to every chunk
    crag          V8  grade evidence per requirement; re-query when weak; drop when none

Ablation = switch one flag off at a time (evals/runners/ablation.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from rag.index.embeddings import Embedder, Reranker
from rag.index.stores import BM25Index, DenseIndex, rrf
from rag.ingest.chunker import Chunk, doc_chunks, fixed_chunks
from rag.ingest.loaders import Doc

OUTCOME_WEIGHT = {"won": 1.35, "replied": 1.15, "ignored": 0.8}


@dataclass(frozen=True)
class Flags:
    fixed_chunks: bool = False
    decompose: bool = False
    hybrid: bool = False
    rerank: bool = False
    outcome: bool = False
    multivector: bool = False
    contextual: bool = False
    crag: bool = False


@dataclass
class Hit:
    chunk: Chunk
    score: float
    requirement: str | None = None

    @property
    def project(self) -> str | None:
        return self.chunk.meta.get("project")


@dataclass
class EvidenceSet:
    hits: list[Hit]
    by_requirement: dict[str, list[Hit]] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)   # CRAG: requirements with no usable evidence

    def ranked_projects(self) -> list[str]:
        seen, out = set(), []
        for h in self.hits:
            p = h.project
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out


def extract_requirements(job_text: str) -> list[str]:
    """Cheap fallback when no JobSpec is available: bullet lines, else sentences."""
    bullets = [l[2:].strip() for l in job_text.splitlines() if l.strip().startswith(("- ", "* ", "• "))]
    if bullets:
        return bullets
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", job_text) if len(s.split()) >= 4][:6]


class Pipeline:
    def __init__(self, name: str, docs: list[Doc], flags: Flags, embedder: Embedder,
                 reranker: Reranker | None = None, crag_threshold: float = 0.0, candidate_n: int = 30,
                 rerank_depth: int = 15):
        self.name, self.flags, self.embedder, self.reranker = name, flags, embedder, reranker
        self.crag_threshold, self.candidate_n, self.rerank_depth = crag_threshold, candidate_n, rerank_depth
        self.docs = docs
        self.chunks = fixed_chunks(docs) if flags.fixed_chunks else doc_chunks(docs)
        if not flags.multivector:
            # without multi-vector, a repo is one item: README + code concatenated (code may be lost)
            self.chunks = _merge_repo_chunks(self.chunks)
        titles = {d.meta.get("project"): d.text.splitlines()[0].lstrip("# ").strip()
                  for d in docs if d.meta.get("type") == "case_study"}
        texts = [(_context_line(c, titles) + "\n" + c.text) if flags.contextual else c.text for c in self.chunks]
        self.dense = DenseIndex(self.chunks, embedder, texts)
        self.bm25 = BM25Index([replace(c, text=t) for c, t in zip(self.chunks, texts)]) if flags.hybrid else None
        self._won_projects = {p for d in docs if d.meta.get("type") == "proposal" and d.meta.get("outcome") == "won"
                              for p in d.meta.get("cited_projects") or []}

    # ------------------------------------------------------------------ one query
    def _search(self, query: str, n: int) -> list[Hit]:
        dense = self.dense.search(query, n)
        if self.bm25 is not None:
            # map BM25 hits back to the original chunk objects
            by_id = {c.chunk_id: c for c in self.chunks}
            lex = [(by_id[c.chunk_id], s) for c, s in self.bm25.search(query, n)]
            fused = rrf([dense, lex])[:n]
        else:
            fused = dense
        hits = [Hit(c, s) for c, s in fused]
        if self.flags.rerank and self.reranker is not None and hits:
            head, tail = hits[:self.rerank_depth], hits[self.rerank_depth:]
            scores = self.reranker.score(query, [h.chunk.text[:1500] for h in head])
            hits = [Hit(h.chunk, s) for h, s in sorted(zip(head, scores), key=lambda x: -x[1])]
            floor = min(scores) - 1.0
            hits += [Hit(h.chunk, floor - i * 1e-3) for i, h in enumerate(tail)]
        if self.flags.outcome:
            hits = sorted((Hit(h.chunk, self._outcome_adjust(h)) for h in hits), key=lambda h: -h.score)
        return hits

    def _outcome_adjust(self, h: Hit) -> float:
        s = h.score
        sign = 1 if s >= 0 else -1
        w = OUTCOME_WEIGHT.get(h.chunk.meta.get("outcome"), 1.0)
        if h.project in self._won_projects:
            w *= 1.1
        return s * w if sign > 0 else s / w

    def similarity(self, query: str, hits: list[Hit]) -> list[float]:
        """Cosine similarity of the query to each hit's chunk (comparable across variants)."""
        if not hasattr(self, "_row"):
            self._row = {c.chunk_id: i for i, c in enumerate(self.chunks)}
        q = self.embedder.embed([query], is_query=True)[0]
        return [float(self.dense.matrix[self._row[h.chunk.chunk_id]] @ q) if h.chunk.chunk_id in self._row else 0.0
                for h in hits]

    # ------------------------------------------------------------------ public
    def retrieve(self, job_text: str, requirements: list[str] | None = None, k: int = 10) -> EvidenceSet:
        if not self.flags.decompose:
            hits = self._search(job_text, max(k, self.candidate_n))
            return EvidenceSet(_group(hits, k) if self.flags.multivector else hits[:k])

        reqs = requirements or extract_requirements(job_text)
        per_req: dict[str, list[Hit]] = {}
        unsupported = []
        for r in reqs:
            hits = self._search(r, self.candidate_n)
            if self.flags.crag:
                hits = self._corrective(r, hits, job_text)
                if not hits:
                    unsupported.append(r)
            if self.flags.multivector:
                hits = _group(hits, k)
            per_req[r] = [Hit(h.chunk, h.score, r) for h in hits[:k]]
        fused = rrf([[(h.chunk, h.score) for h in hs] for hs in per_req.values() if hs])
        req_of = {h.chunk.chunk_id: h.requirement for hs in per_req.values() for h in hs}
        return EvidenceSet([Hit(c, s, req_of.get(c.chunk_id)) for c, s in fused[:max(k, 10)]],
                           per_req, unsupported)

    def _corrective(self, requirement: str, hits: list[Hit], job_text: str) -> list[Hit]:
        """CRAG: keep hits above the grade threshold; if none, re-query once with job context; else refuse."""
        good = [h for h in hits if h.score >= self.crag_threshold]
        if good:
            return good
        retry = self._search(f"{requirement} — in the context of: {job_text[:300]}", self.candidate_n)
        return [h for h in retry if h.score >= self.crag_threshold]


def _group(hits: list[Hit], k: int) -> list[Hit]:
    """Multi-vector: aggregate chunks of one project (max score + small bonus per extra representation)."""
    best: dict[str, Hit] = {}
    types: dict[str, set] = {}
    loose = []
    for h in hits:
        p = h.project
        if not p:
            loose.append(h)
            continue
        types.setdefault(p, set()).add(h.chunk.meta.get("type"))
        if p not in best or h.score > best[p].score:
            best[p] = h
    grouped = [Hit(h.chunk, h.score + 0.02 * (len(types[p]) - 1) * abs(h.score), h.requirement)
               for p, h in best.items()]
    return sorted(grouped + loose, key=lambda h: -h.score)[:max(k, 10)]


def _merge_repo_chunks(chunks: list[Chunk]) -> list[Chunk]:
    out, repos = [], {}
    for c in chunks:
        if c.meta.get("type") in ("readme", "code"):
            key = c.meta.get("repo")
            if key in repos:
                repos[key].text += "\n\n" + c.text
                continue
            merged = Chunk(f"{c.doc_id}#repo", c.doc_id, c.text, {**c.meta, "type": "repo"})
            repos[key] = merged
            out.append(merged)
        else:
            out.append(c)
    return out


def _context_line(c: Chunk, titles: dict) -> str:
    m = c.meta
    title = titles.get(m.get("project"), "")
    bits = [f"{m.get('type', 'item')} from {m.get('persona')}'s portfolio"]
    if title:
        bits.append(f"project: {title}")
    if m.get("client_industry"):
        bits.append(f"industry: {m['client_industry']}")
    if m.get("outcome"):
        bits.append(f"proposal outcome: {m['outcome']}")
    return "[" + "; ".join(bits) + "]"

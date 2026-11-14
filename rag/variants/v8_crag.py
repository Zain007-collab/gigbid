"""V8 corrective RAG: grade per-requirement evidence, re-query when weak, refuse to claim when none."""
from rag.pipeline import Flags

NAME = "v8_crag"
FLAGS = Flags(decompose=True, hybrid=True, rerank=True, multivector=True, crag=True)

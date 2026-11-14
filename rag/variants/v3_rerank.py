"""V3 cross-encoder reranking on top of V2."""
from rag.pipeline import Flags

NAME = "v3_rerank"
FLAGS = Flags(decompose=True, hybrid=True, rerank=True)

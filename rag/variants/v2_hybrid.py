"""V2 hybrid: BM25 + dense (RRF) per requirement — keeps exact tool names sharp."""
from rag.pipeline import Flags

NAME = "v2_hybrid"
FLAGS = Flags(decompose=True, hybrid=True)

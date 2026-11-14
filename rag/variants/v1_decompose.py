"""V1 requirement decomposition: one dense retrieval per requirement, fused with RRF."""
from rag.pipeline import Flags

NAME = "v1_decompose"
FLAGS = Flags(decompose=True)

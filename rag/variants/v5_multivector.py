"""V5 multi-vector: README, code, case study, reviews indexed separately, returned per project."""
from rag.pipeline import Flags

NAME = "v5_multivector"
FLAGS = Flags(decompose=True, hybrid=True, rerank=True, multivector=True)

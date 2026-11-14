"""V4 outcome-aware: boost evidence from proposals that won or got replies."""
from rag.pipeline import Flags

NAME = "v4_outcome"
FLAGS = Flags(decompose=True, hybrid=True, rerank=True, outcome=True)

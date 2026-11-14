"""V6 contextual retrieval: a context line (type, project title, industry, outcome) prepended to every chunk."""
from rag.pipeline import Flags

NAME = "v6_contextual"
FLAGS = Flags(decompose=True, hybrid=True, rerank=True, contextual=True)

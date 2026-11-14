"""V0 baseline: fixed word chunks, dense top-k on the whole job post."""
from rag.pipeline import Flags

NAME = "v0_naive"
FLAGS = Flags(fixed_chunks=True)

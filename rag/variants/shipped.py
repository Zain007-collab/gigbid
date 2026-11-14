"""The shipped retriever, chosen by measurement (evals/results/ablation_dev_round2.json):
requirement decomposition + hybrid BM25/dense (RRF) + multi-vector project grouping.
Dropping any component lowers mean Recall@5 by 4.6-10.5 points; adding the MS-MARCO cross-encoder,
outcome boosting or contextual lines did not raise Recall@5."""
from rag.pipeline import Flags

NAME = "shipped"
FLAGS = Flags(decompose=True, hybrid=True, multivector=True)

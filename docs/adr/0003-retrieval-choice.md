# ADR 0003 — Ship decompose + hybrid + multi-vector retrieval
**Status:** accepted. **Evidence:** `evals/results/rag_dev_fastembed.json`, `ablation_dev_round1.json`,
`ablation_dev_round2.json`. Mean Recall@5 0.842 vs 0.685 for V0; removing any component costs 4.6–10.5 points; the
MS-MARCO cross-encoder (−6.7), outcome boosting (−6.8) and contextual lines (−0.3) did not help Recall@5.
**Consequences:** no reranker cost at query time; outcome knowledge is used in the *writer* (outcome memory), not in
evidence ranking.

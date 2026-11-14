"""Ablation (R&D question B): start from a pipeline and switch one component at a time.

    python -m evals.runners.ablation [--base hybrid,decompose,multivector] [--limit 100]

Also evaluates the no-reranker combinations, because the cross-encoder lowered recall in the variant sweep.
Writes evals/results/ablation_<split>.json.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import fields

from evals.runners.rag_eval import ROOT, evaluate
from rag import retriever as R
from rag.pipeline import Flags

COMPONENTS = ["decompose", "hybrid", "rerank", "outcome", "multivector", "contextual"]


def flags_from(names: set[str]) -> Flags:
    return Flags(**{f.name: f.name in names for f in fields(Flags)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="decompose,hybrid,multivector,outcome")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--personas", default="ai_ml,fullstack,data_analyst")
    args = ap.parse_args()
    base = set(args.base.split(","))
    configs = {"base: " + "+".join(sorted(base)): base}
    for c in COMPONENTS:
        alt = base - {c} if c in base else base | {c}
        configs[("- " if c in base else "+ ") + c] = alt
    configs["hybrid only"] = {"decompose", "hybrid"}
    emb, rr = R.embedder(), R.reranker()
    out = {"embedder": emb.name, "reranker": f"{rr.backend}:{rr.model}", "base": sorted(base), "configs": {}}
    for name, comps in configs.items():
        per = {}
        for persona in args.personas.split(","):
            rows = [json.loads(l) for l in (ROOT / "evals/datasets" / args.split / f"{persona}.jsonl").read_text().splitlines()][: args.limit]
            per[persona] = evaluate("v2_hybrid", persona, rows, emb, rr, flags_override=flags_from(comps))["aggregate"]
        rr.flush()
        mean = {k: round(sum(p[k] for p in per.values()) / len(per), 4) for k in ("recall@5", "ndcg@10", "mrr")}
        out["configs"][name] = {"components": sorted(comps), "mean": mean, "by_persona": per}
        print(f"{name:42s} R@5={mean['recall@5']:.3f} nDCG={mean['ndcg@10']:.3f} MRR={mean['mrr']:.3f}", flush=True)
    (ROOT / "evals/results" / f"ablation_{args.split}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

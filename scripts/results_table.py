"""Regenerate the README results section from evals/results/*.json (every number traces to a file).

    python scripts/results_table.py          # rewrite README between the markers
    python scripts/results_table.py --check  # CI: fail if README is stale
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "evals" / "results"
START, END = "<!-- results:start -->", "<!-- results:end -->"


def load(name: str) -> dict | None:
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else None


def f(x, nd=3, pct=False):
    if x is None:
        return "n/a"
    return f"{x * 100:.1f}%" if pct else f"{x:.{nd}f}"


def rag_table() -> str:
    r = load("rag_dev_fastembed.json")
    if not r:
        return "_RAG results not generated yet._"
    rows = ["| Variant | Recall@5 | Recall@5 (4+ reqs) | nDCG@10 | MRR | p95 retrieval latency (s) |",
            "|---|---|---|---|---|---|"]
    for v, per in r["variants"].items():
        a = [p["aggregate"] for p in per.values()]
        m = lambda k: sum(x[k] for x in a if x[k] is not None) / len(a)  # noqa: E731
        rows.append(f"| {v} | {m('recall@5'):.3f} | {m('recall@5_4plus_reqs'):.3f} | {m('ndcg@10'):.3f} | {m('mrr'):.3f} "
                    f"| {max(x['p95_latency_s'] for x in a):.3f} |")
    return "\n".join(rows) + f"\n\n_Source: `evals/results/rag_dev_fastembed.json` ({r['embedder']}, reranker {r['reranker']}; " \
                             f"mean over 3 personas, ≤100 dev jobs each)._"


def headline_table() -> str:
    rows = ["| Variant | Evidence Recall@5 | nDCG@10 | Unsupported claims / proposal (after verify) | Judge score | GigSim reply rate (expected) | p95 latency per proposal (s) | Cost per proposal (USD) |",
            "|---|---|---|---|---|---|---|---|"]
    rag = load("rag_dev_fastembed.json") or {"variants": {}}
    abl = load("ablation_dev_round2.json") or {"configs": {}}
    for label, rag_key, tag in (("V0 naive", "v0_naive", "v0_offline"), ("Shipped (decompose+hybrid+multivector)", None, "shipped_offline")):
        if rag_key and rag_key in rag["variants"]:
            a = [p["aggregate"] for p in rag["variants"][rag_key].values()]
            r5, nd = sum(x["recall@5"] for x in a) / len(a), sum(x["ndcg@10"] for x in a) / len(a)
        else:
            base = next((c for k, c in abl["configs"].items() if k.startswith("base")), None)
            r5, nd = (base["mean"]["recall@5"], base["mean"]["ndcg@10"]) if base else (None, None)
        d = (load(f"decision_dev_{tag}.json") or {}).get("overall", {})
        o = (load(f"gigsim_outcomes_{tag}.json") or {}).get("overall", {})
        j = (load(f"judge_{tag}.json") or {}).get("mean_score")
        rows.append(f"| {label} | {f(r5)} | {f(nd)} | {f(d.get('unsupported_per_proposal_after'), 2)} | {f(j, 2)} | "
                    f"{f(o.get('reply_rate'), pct=True)} ({f(o.get('expected_reply_rate'), pct=True)}, n={o.get('submitted', 'n/a')}) | "
                    f"{f((d.get('latency_proposal_s') or {}).get('p95'), 2)} | "
                    f"{f(d.get('usd_per_proposal'), 4)} |")
    return "\n".join(rows)


def decision_table() -> str:
    d = load("decision_dev_shipped_offline.json")
    if not d:
        return "_Decision results not generated yet._"
    rows = ["| Persona | Bid/skip F1 | Scam precision | Scam recall | Price in fair band | Injection attack success | Unsupported before → after verify |",
            "|---|---|---|---|---|---|---|"]
    for p, s in {**d["by_persona"], "overall": d["overall"]}.items():
        rows.append(f"| {p} | {f(s['bid_skip']['f1'])} | {f(s['scam']['precision'])} | {f(s['scam']['recall'])} | "
                    f"{f(s['price_in_fair_band'], pct=True)} | {f(s['injection']['attack_success_rate'], pct=True)} | "
                    f"{f(s['unsupported_per_proposal_before'], 2)} → {f(s['unsupported_per_proposal_after'], 2)} |")
    return "\n".join(rows) + "\n\n_Source: `evals/results/decision_dev_shipped_offline.json` (deterministic node fallbacks, open embeddings)._"


def agents_table() -> str:
    c = load("rq_c_agents.json")
    if not c:
        return "_Multi-agent comparison (R&D question C) pending an LLM run — see `evals/runners/rq_c_agents.py`._"
    rows = ["| System | Bid/skip accuracy | Unsupported claims / proposal | Compliance violations caught | GigSim reply rate | Tokens / job | p95 latency (s) |",
            "|---|---|---|---|---|---|---|"]
    for name, s in c["systems"].items():
        rows.append(f"| {name} | {f(s['bid_accuracy'])} | {f(s['unsupported_per_proposal'], 2)} | {s['violations_caught']} | "
                    f"{f(s['reply_rate'], pct=True)} | {f(s['tokens_per_job'], 0)} | {f(s['p95_latency_s'], 2)} |")
    return "\n".join(rows) + f"\n\n_Source: `evals/results/rq_c_agents.json` ({c.get('n_jobs')} dev jobs, model {c.get('model')})._"


def ab_table() -> str:
    a = load("ab_test_evidence_first_vs_concise_question.json")
    if not a:
        return "_A/B test not generated yet._"
    rows = ["| Strategy | Proposals | Replies | Reply rate | Hires |", "|---|---|---|---|---|"]
    for arm, s in a["arms"].items():
        rows.append(f"| {arm} | {s['n']} | {s['replies']} | {f(s['reply_rate'], pct=True)} | {s['hires']} |")
    return "\n".join(rows) + (f"\n\nTwo-proportion z = {a['z']}, p = {a['p_value_z']}; Fisher exact p = {a['p_value_fisher']} "
                              f"(seed {a['seed']}). _Source: `evals/results/ab_test_evidence_first_vs_concise_question.json`._")


def render() -> str:
    return "\n\n".join([START, "### Headline (V0 vs shipped)", headline_table(), "### Retrieval variants", rag_table(),
                        "### Decisions and safety (shipped pipeline)", decision_table(),
                        "### Proposal strategy A/B test (signature extension X2)", ab_table(),
                        "### Single agent vs supervisor vs hand-built graph", agents_table(), END])


def main() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text() if readme.exists() else f"# GigBid\n\n{START}\n{END}\n"
    new = text.split(START)[0] + render() + text.split(END, 1)[1] if START in text else text + "\n" + render() + "\n"
    if "--check" in sys.argv:
        sys.exit(0 if new == text else 1)
    readme.write_text(new)
    print("README results updated")


if __name__ == "__main__":
    main()

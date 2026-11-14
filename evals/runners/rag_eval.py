"""Retrieval evaluation (B4): evidence Recall@5, MRR, nDCG@10 per persona and variant.

    python -m evals.runners.rag_eval [--variants v0_naive,v2_hybrid] [--limit 200] [--split dev]

Gold: a project is relevant to a job if its (ground-truth) skills cover at least
one of the job's requirements; its graded gain = number of requirements it covers.
Recall@5 is normalised by min(5, |relevant|) so jobs with many relevant projects
are not capped below 1.0.

Writes evals/results/rag_<split>_<backend>.json and .csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path

from rag import retriever as R

ROOT = Path(__file__).resolve().parents[2]
JOBS = {j["job_id"]: j for j in json.loads((ROOT / "gigsim" / "seed" / "jobs.json").read_text())}


def manifest(persona: str) -> dict[str, set[str]]:
    m = json.loads((ROOT / "data" / "personas" / persona / "_manifest.json").read_text())
    return {p["project_id"]: set(p["skills"]) for p in m["projects"]}


def job_text(job: dict) -> str:
    return f"{job['title']}\n\n{job['description']}"


def metrics(ranked: list[str], gains: dict[str, int]) -> dict[str, float]:
    rel = {p for p, g in gains.items() if g > 0}
    top5 = ranked[:5]
    recall5 = len(set(top5) & rel) / min(5, len(rel))
    mrr = next((1 / (i + 1) for i, p in enumerate(ranked) if p in rel), 0.0)
    dcg = sum(gains.get(p, 0) / math.log2(i + 2) for i, p in enumerate(ranked[:10]))
    ideal = sorted(gains.values(), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return {"recall@5": recall5, "mrr": mrr, "ndcg@10": dcg / idcg if idcg else 0.0}


def evaluate(variant: str, persona: str, rows: list[dict], emb, rr, flags_override=None) -> dict:
    pipe = R.build(variant, persona, emb, rr, flags_override)
    skills = manifest(persona)
    per_job, lat = [], []
    for row in rows:
        reqs = row["gold"]["requirements"]
        gains = {p: len(set(reqs) & s) for p, s in skills.items()}
        if not any(gains.values()):
            continue
        job = JOBS[row["job_id"]]
        t0 = time.perf_counter()
        ev = pipe.retrieve(job_text(job), None, k=10)
        lat.append(time.perf_counter() - t0)
        m = metrics(ev.ranked_projects(), gains)
        m.update(job_id=row["job_id"], n_req=len(reqs), category=row["category"])
        per_job.append(m)
    agg = {k: round(statistics.mean(j[k] for j in per_job), 4) for k in ("recall@5", "mrr", "ndcg@10")}
    many = [j for j in per_job if j["n_req"] >= 4]
    agg["recall@5_4plus_reqs"] = round(statistics.mean(j["recall@5"] for j in many), 4) if many else None
    agg["n_jobs"] = len(per_job)
    agg["p50_latency_s"] = round(statistics.median(lat), 4) if lat else None
    agg["p95_latency_s"] = round(sorted(lat)[int(0.95 * (len(lat) - 1))], 4) if lat else None
    return {"aggregate": agg, "per_job": per_job}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(R.VARIANTS))
    ap.add_argument("--personas", default="ai_ml,fullstack,data_analyst")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    emb, rr = R.embedder(), R.reranker()
    results = {"embedder": emb.name, "reranker": f"{rr.backend}:{rr.model}", "split": args.split, "variants": {}}
    for v in args.variants.split(","):
        results["variants"][v] = {}
        for persona in args.personas.split(","):
            rows = [json.loads(l) for l in (ROOT / "evals" / "datasets" / args.split / f"{persona}.jsonl")
                    .read_text().splitlines()][: args.limit]
            r = evaluate(v, persona, rows, emb, rr)
            results["variants"][v][persona] = r
            print(f"{v:16s} {persona:13s} {r['aggregate']}", flush=True)

    out = ROOT / "evals" / "results"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"rag_{args.split}_{emb.backend}"
    (out / f"{tag}.json").write_text(json.dumps(results, indent=1))
    with open(out / f"{tag}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "persona", "recall@5", "mrr", "ndcg@10", "recall@5_4plus_reqs", "n_jobs",
                    "p95_latency_s"])
        for v, per in results["variants"].items():
            for persona, r in per.items():
                a = r["aggregate"]
                w.writerow([v, persona, a["recall@5"], a["mrr"], a["ndcg@10"], a["recall@5_4plus_reqs"],
                            a["n_jobs"], a["p95_latency_s"]])
    print(f"wrote {out / tag}.json/.csv")


if __name__ == "__main__":
    main()

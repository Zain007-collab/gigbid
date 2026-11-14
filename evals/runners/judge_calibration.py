"""LLM-judge calibration (B4) and judge scoring.

  1. python -m evals.runners.judge_calibration sample            # writes evals/datasets/judge_labels.csv (30 items)
  2. YOU fill the `human_score` column (1-5, rubric in agent/prompts/judge.md) — your own judgement, no AI help.
  3. python -m evals.runners.judge_calibration calibrate         # LLM judge on the same 30 -> Cohen's kappa
  4. python -m evals.runners.judge_calibration score --tag shipped_offline   # judge every proposal of a run

Kappa is reported unweighted and quadratic-weighted; disagreements are listed for the viva ("who was right?").
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics

from pydantic import BaseModel, Field

from evals.runners.harness import ROOT, write

LABELS = ROOT / "evals" / "datasets" / "judge_labels.csv"
JOBS = None


class Grade(BaseModel):
    score: int = Field(ge=1, le=5)
    justification: str


def job(job_id: str) -> dict:
    global JOBS
    if JOBS is None:
        JOBS = {j["job_id"]: j for j in json.loads((ROOT / "gigsim/seed/jobs.json").read_text())}
    return JOBS[job_id]


def judge_one(job_id: str, proposal: str) -> tuple[int | None, str, dict]:
    from agent import llm, prompting
    j = job(job_id)
    g, usage = llm.structured("judge", Grade, prompting.render("judge", untrusted_rules=prompting.load("_untrusted_rules")),
                              f"PROPOSAL:\n{proposal}\n\n" + prompting.untrusted("job_post", f"{j['title']}\n\n{j['description']}"))
    return (g.score, g.justification, usage) if g else (None, usage.get("error", "judge failed"), usage)


def kappa(a: list[int], b: list[int], weighted: bool) -> float:
    cats = [1, 2, 3, 4, 5]
    n = len(a)
    w = lambda i, j: ((i - j) ** 2) / 16 if weighted else float(i != j)  # noqa: E731
    obs = sum(w(x, y) for x, y in zip(a, b)) / n
    pa = {c: a.count(c) / n for c in cats}
    pb = {c: b.count(c) / n for c in cats}
    exp = sum(pa[i] * pb[j] * w(i, j) for i in cats for j in cats)
    return round(1 - obs / exp, 4) if exp else 1.0


def sample(n: int, seed: int = 3) -> None:
    pool = []
    for tag in ("shipped_offline", "v0_offline", "shipped_t055"):
        p = ROOT / f"evals/results/decision_dev_{tag}.json"
        if p.exists():
            pool += [(tag, r) for r in json.loads(p.read_text())["jobs"] if r["proposal_text"]]
    random.Random(seed).shuffle(pool)
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    with open(LABELS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "job_id", "persona", "job_title", "proposal", "human_score"])
        for i, (tag, r) in enumerate(pool[:n]):
            w.writerow([i + 1, r["job_id"], r["persona"], job(r["job_id"])["title"], r["proposal_text"], ""])
    print(f"wrote {LABELS} — now fill human_score (1-5) yourself, using the rubric in agent/prompts/judge.md")


def calibrate() -> None:
    rows = list(csv.DictReader(open(LABELS)))
    labelled = [r for r in rows if r["human_score"].strip()]
    if len(labelled) < len(rows):
        raise SystemExit(f"only {len(labelled)}/{len(rows)} items have human_score — label them all first")
    items = []
    for r in labelled:
        s, why, _ = judge_one(r["job_id"], r["proposal"])
        items.append({"item": r["item"], "job_id": r["job_id"], "human": int(r["human_score"]), "judge": s, "why": why})
    ok = [i for i in items if i["judge"] is not None]
    h, j = [i["human"] for i in ok], [i["judge"] for i in ok]
    out = {"n": len(ok), "cohen_kappa": kappa(h, j, False), "quadratic_weighted_kappa": kappa(h, j, True),
           "exact_agreement": round(sum(x == y for x, y in zip(h, j)) / len(ok), 4),
           "within_one": round(sum(abs(x - y) <= 1 for x, y in zip(h, j)) / len(ok), 4),
           "mean_human": round(statistics.mean(h), 3), "mean_judge": round(statistics.mean(j), 3),
           "disagreements": sorted([i for i in ok if abs(i["human"] - i["judge"]) >= 2], key=lambda i: -abs(i["human"] - i["judge"])),
           "items": items}
    print({k: v for k, v in out.items() if k not in ("items", "disagreements")})
    print("wrote", write("judge_calibration", out))


def score(tag: str, limit: int) -> None:
    rows = [r for r in json.loads((ROOT / f"evals/results/decision_dev_{tag}.json").read_text())["jobs"] if r["proposal_text"]]
    rows = rows[:limit]
    graded = []
    for r in rows:
        s, why, usage = judge_one(r["job_id"], r["proposal_text"])
        graded.append({"job_id": r["job_id"], "persona": r["persona"], "score": s, "why": why})
    ok = [g["score"] for g in graded if g["score"] is not None]
    out = {"tag": tag, "n": len(ok), "mean_score": round(statistics.mean(ok), 3) if ok else None, "items": graded}
    print(tag, out["n"], out["mean_score"])
    print("wrote", write(f"judge_{tag}", out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "calibrate", "score"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--tag", default="shipped_offline")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    {"sample": lambda: sample(a.n), "calibrate": calibrate, "score": lambda: score(a.tag, a.limit)}[a.cmd]()


if __name__ == "__main__":
    main()

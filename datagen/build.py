"""Build every dataset GigBid needs from a single seed.

    python -m datagen.build --seed 7

Outputs (all SYNTHETIC unless marked `provenance: kaggle_*`):
  data/personas/<persona>/        CV (md + pdf), case studies, repos (README + code),
                                  reviews.json, proposals.jsonl, profile.json
  data/personas/<persona>/_manifest.json   ground truth (project -> skills); never ingested
  gigsim/seed/                    clients.json, jobs.json, hidden_prefs.json, market_stats.json
  evals/datasets/dev/<persona>.jsonl       200 jobs per persona with gold labels
  evals/datasets/holdout/<persona>.jsonl   50 jobs per persona, never used for tuning

Kaggle inputs (optional, see docs/DATA_CARD.md): if data/raw/ holds the
Upwork job-postings CSV, real job titles and real budget/hourly-rate
distributions replace the synthetic ones.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import shutil
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from datagen import catalog as C
from datagen.pdf import write_pdf

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)   # fixed so builds are reproducible
SYNTH = "synthetic"


# ====================================================================== helpers
def phrase(rng: random.Random, skill: str, p_paraphrase: float) -> str:
    if skill in C.PARAPHRASE and rng.random() < p_paraphrase:
        return rng.choice(C.PARAPHRASE[skill])
    return C.display(skill)


def fill(rng: random.Random, tmpl: str, **kw) -> str:
    vals = dict(a=rng.randint(3, 9), b=rng.randint(1, 2), p=rng.randint(18, 64), p0=rng.randint(40, 70), **kw)
    return tmpl.format(**vals)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


# ====================================================================== Kaggle (optional)
def load_kaggle(raw_dir: Path) -> dict | None:
    """Real titles + budget distributions from the Upwork 2024 job-postings CSV."""
    files = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    rows = []
    for f in files:
        with open(f, encoding="utf-8", errors="ignore") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "title" not in reader.fieldnames:
                continue
            for i, r in enumerate(reader):
                rows.append(r)
                if i > 60000:
                    break
    if not rows:
        return None
    kw = {
        "ai_ml": r"\b(ai|ml|machine learning|llm|gpt|chatbot|rag|langchain|nlp|computer vision|deep learning)\b",
        "web": r"\b(react|next\.?js|node|full[- ]?stack|web app|frontend|backend|typescript|django|laravel)\b",
        "data": r"\b(data analy|power bi|tableau|excel|sql|dashboard|looker|dbt|bigquery|snowflake)\b",
        "mobile": r"\b(flutter|ios|android|mobile app|swift|react native)\b",
        "design": r"\b(figma|ui/ux|logo|designer)\b",
        "writing": r"\b(writer|article|blog|copywrit|seo)\b",
        "devops": r"\b(devops|kubernetes|terraform|ci/cd)\b",
    }
    titles: dict[str, list[str]] = {k: [] for k in kw}
    fixed: dict[str, list[float]] = {k: [] for k in kw}
    hourly: dict[str, list[float]] = {k: [] for k in kw}
    for r in rows:
        t = (r.get("title") or "").strip()
        for cat, pat in kw.items():
            if re.search(pat, t, re.I):
                if 15 <= len(t) <= 90:
                    titles[cat].append(t)
                try:
                    if str(r.get("is_hourly", "")).lower() in ("true", "1"):
                        lo, hi = float(r.get("hourly_low") or 0), float(r.get("hourly_high") or 0)
                        if 3 <= lo <= hi <= 300:
                            hourly[cat].append((lo + hi) / 2)
                    else:
                        b = float(r.get("budget") or 0)
                        if 20 <= b <= 50000:
                            fixed[cat].append(b)
                except ValueError:
                    pass
                break
    return {"titles": titles, "fixed": fixed, "hourly": hourly, "n_rows": len(rows),
            "files": [f.name for f in files]}


def market_stats(rng: random.Random, kaggle: dict | None) -> dict:
    """Percentiles per category for fixed budgets and hourly rates."""
    synth_fixed = {"ai_ml": 900, "web": 700, "data": 350, "mobile": 900, "design": 250,
                   "writing": 120, "devops": 800, "other": 400}
    synth_hourly = {"ai_ml": 40, "web": 32, "data": 25, "mobile": 35, "design": 25,
                    "writing": 20, "devops": 45, "other": 25}
    out = {}
    for cat in C.CATEGORY_SKILLS:
        src = "synthetic"
        f = (kaggle or {}).get("fixed", {}).get(cat) or []
        h = (kaggle or {}).get("hourly", {}).get(cat) or []
        if len(f) < 30:
            f = [rng.lognormvariate(math.log(synth_fixed[cat]), 0.6) for _ in range(400)]
        else:
            src = "kaggle_upwork_2024"
        if len(h) < 30:
            h = [rng.lognormvariate(math.log(synth_hourly[cat]), 0.35) for _ in range(400)]
        q = lambda xs, p: round(statistics.quantiles(xs, n=100)[p - 1], 2)  # noqa: E731
        out[cat] = {"source": src, "n_fixed": len(f), "n_hourly": len(h),
                    "fixed": {"p25": q(f, 25), "p50": q(f, 50), "p75": q(f, 75)},
                    "hourly": {"p25": q(h, 25), "p50": q(h, 50), "p75": q(h, 75)}}
    return out


# ====================================================================== personas
def build_projects(rng: random.Random, pid: str) -> list[dict]:
    arche = C.ARCHETYPES[pid]
    projects = []
    n = rng.randint(18, 22)
    for i in range(n):
        a = arche[i % len(arche)]
        ind = rng.choice(C.INDUSTRIES)
        year = rng.choice([2021, 2022, 2023, 2024, 2025, 2026])
        title = a["title"].format(ind=ind)
        projects.append({
            "project_id": f"{pid}-p{i + 1:02d}", "archetype": a["key"], "title": title, "industry": ind,
            "year": year, "explicit": a["explicit"], "implied": a["implied"],
            "skills": sorted(set(a["explicit"]) | set(a["implied"])),
            "metric": fill(rng, a["metric"]), "repo": slug(f"{a['key']}-{ind}") + f"-{i + 1}",
            "client_industry": ind,
        })
    return projects


def case_study_md(rng: random.Random, p: dict, persona: dict) -> str:
    named = ", ".join(phrase(rng, s, 0.0) for s in p["explicit"])
    hinted = "; ".join(phrase(rng, s, 1.0) for s in p["implied"])   # implied skills only paraphrased
    return f"""---
type: case_study
project: {p['project_id']}
industry: {p['industry']}
date: {p['year']}
provenance: {SYNTH}
---
# {p['title']}

**Client:** a {p['industry']} organisation (anonymised). **Year:** {p['year']}.

## Problem
The client needed help with {p['title'].lower()}. Their existing process was manual and slow.

## What I did
I designed and delivered the solution using {named}. Supporting pieces included {hinted}.
I wrote documentation and handed over with a recorded walkthrough.

## Result
We {p['metric']}.
"""


def readme_md(rng: random.Random, p: dict) -> str:
    # Deliberately terse: some repos look irrelevant from the README alone.
    terse = rng.random() < 0.4
    body = "Internal tooling. See src/." if terse else (
        f"{p['title']}. Built with " + ", ".join(C.display(s) for s in p["explicit"]) + ".")
    return f"# {p['repo']}\n\n{body}\n\n_Synthetic repository generated for GigBid ({SYNTH})._\n"


def code_file(p: dict) -> tuple[str, str]:
    ts = any(s in p["skills"] for s in ("react", "nextjs", "typescript", "nodejs", "stripe", "playwright"))
    ext = "ts" if ts else "py"
    lines = [f"// {p['repo']} (synthetic)" if ts else f"# {p['repo']} (synthetic)"]
    for s in p["skills"]:
        if s in C.CODE_IMPORT:
            lines.append(C.CODE_IMPORT[s])
    return f"src/main.{ext}", "\n\n".join(lines) + "\n"


def build_persona(rng: random.Random, pid: str, out: Path) -> dict:
    persona = C.PERSONAS[pid]
    d = out / pid
    if d.exists():
        shutil.rmtree(d)
    (d / "case_studies").mkdir(parents=True)
    (d / "repos").mkdir()
    projects = build_projects(rng, pid)

    # --- case studies + repos
    for p in projects:
        (d / "case_studies" / f"{p['project_id']}.md").write_text(case_study_md(rng, p, persona))
        r = d / "repos" / p["repo"]
        (r / "src").mkdir(parents=True)
        (r / "README.md").write_text(readme_md(rng, p))
        rel, code = code_file(p)
        (r / rel).write_text(code)

    # --- CV (markdown + PDF); lists only a subset of skills, like real CVs
    cv_skills = rng.sample(persona["core"], k=len(persona["core"]) * 2 // 3)
    exp = [f"- {p['year']}: {p['title']} — {p['metric']}." for p in sorted(projects, key=lambda x: -x["year"])[:8]]
    cv = (f"# {persona['name']}\n\n{persona['headline']}\n\n{persona['location']} · {persona['years']} years freelancing\n\n"
          f"## Skills\n{', '.join(C.display(s) for s in cv_skills)}\n\n## Selected work\n" + "\n".join(exp) +
          f"\n\n_This CV is synthetic ({SYNTH})._\n")
    (d / "cv.md").write_text(cv)
    write_pdf(d / "cv.pdf", cv.replace("#", "").replace("**", "").splitlines())

    # --- reviews (describe the same project a third way)
    reviews = []
    for p in projects:
        if rng.random() < 0.6:
            stars = rng.choice([5, 5, 5, 4, 4, 3])
            txt = rng.choice([
                "{n} delivered our {w} on time and explained every step.",
                "Great communication. The {w} works and our team actually uses it.",
                "{n} understood the {ind} context quickly; the {w} saved us real time.",
                "Solid work on the {w}, a few delays but good result.",
            ]).format(n=persona["name"].split()[0], w=p["archetype"].replace("_", " "), ind=p["industry"])
            reviews.append({"review_id": f"{p['project_id']}-rev", "project_id": p["project_id"],
                            "rating": stars, "text": txt, "date": f"{p['year']}-{rng.randint(1, 12):02d}-15",
                            "client_industry": p["industry"], "provenance": SYNTH})
    (d / "reviews.json").write_text(json.dumps(reviews, indent=2))

    # --- ~150 past proposals with outcomes (outcome depends on quality, not wording alone)
    proposals = []
    for i in range(150):
        cited = rng.sample(projects, k=rng.choice([0, 1, 1, 2]))
        specific_open = rng.random() < 0.5
        price_ok = rng.random() < 0.7
        ind = rng.choice(C.INDUSTRIES)
        skills = rng.sample(persona["core"], k=3)
        job_title = f"{C.display(skills[0])} work for a {ind} client"
        opening = (f"Your {ind} project needs {C.display(skills[0])} and {C.display(skills[1])}; I have shipped exactly that."
                   if specific_open else rng.choice(C.GENERIC_OPENINGS))
        body = " ".join(f"In '{p['title']}' I {p['metric']}." for p in cited) or \
            "I have a lot of experience and always deliver high quality work."
        text = f"{opening}\n\n{body}\n\nI can start this week. Happy to share a short plan on a call."
        p_reply = 0.06 + 0.32 * specific_open + 0.30 * bool(cited) + 0.12 * price_ok
        r = rng.random()
        outcome = "won" if r < p_reply * 0.35 else "replied" if r < p_reply else "ignored"
        proposals.append({"proposal_id": f"{pid}-prop{i + 1:03d}", "job_title": job_title, "category": persona["category"],
                          "client_industry": ind, "text": text, "cited_projects": [p["project_id"] for p in cited],
                          "outcome": outcome, "date": (NOW - timedelta(days=rng.randint(10, 700))).date().isoformat(),
                          "provenance": SYNTH})
    with open(d / "proposals.jsonl", "w") as f:
        for p in proposals:
            f.write(json.dumps(p) + "\n")

    profile = {"persona_id": pid, **{k: v for k, v in persona.items() if k != "core"}, "provenance": SYNTH}
    (d / "profile.json").write_text(json.dumps(profile, indent=2))
    manifest = {"persona_id": pid, "projects": projects,
                "note": "GROUND TRUTH for evaluation only. Never ingest this file."}
    (d / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return {"persona": persona, "projects": projects}


# ====================================================================== clients + jobs
def build_clients(rng: random.Random, n: int = 320) -> list[dict]:
    clients = []
    for i in range(n):
        shady = rng.random() < 0.12
        jobs_posted = 0 if shady and rng.random() < 0.6 else rng.randint(1, 60)
        hires = 0 if shady else rng.randint(0, jobs_posted)
        clients.append({
            "client_id": f"c{i + 1:04d}",
            "country": rng.choice(["US", "UK", "DE", "CA", "AU", "AE", "NL", "PK", "IN", "SG"]),
            "payment_verified": (not shady) or rng.random() < 0.2,
            "total_spent": 0.0 if shady else round(rng.lognormvariate(8, 1.3), 2),
            "hire_rate": round(hires / jobs_posted, 2) if jobs_posted else 0.0,
            "rating": None if hires == 0 else round(rng.uniform(3.6, 5.0), 1),
            "jobs_posted": jobs_posted, "hires": hires,
            "member_since": (NOW - timedelta(days=rng.randint(3 if shady else 90, 3000))).date().isoformat(),
            "contracts": [{"title": f"Contract {k + 1}", "amount": round(rng.uniform(100, 5000), 2),
                           "feedback_given": round(rng.uniform(3.5, 5), 1)} for k in range(min(hires, 3))],
            "_shady": shady,
            "_hidden": {"dislikes_generic_opening": rng.random() < 0.7,
                        "responsiveness": round(rng.uniform(0.5, 1.0), 2)},
            "provenance": SYNTH,
        })
    return clients


def make_job(rng: random.Random, jid: str, cat: str, clients: list[dict], stats: dict, kaggle: dict | None,
             *, scam: bool = False, inject: bool = False, extra_offdomain: bool = False) -> dict:
    pool = C.CATEGORY_SKILLS[cat]
    k = rng.choice([2, 3, 3, 4, 4, 5, 6])
    reqs = rng.sample(pool, k=min(k, len(pool)))
    if extra_offdomain:
        reqs.append(rng.choice(C.CATEGORY_SKILLS[rng.choice(["mobile", "design", "devops", "other"])]))
    ind = rng.choice(C.INDUSTRIES)
    provenance = SYNTH
    real_titles = (kaggle or {}).get("titles", {}).get(cat) or []
    if real_titles and rng.random() < 0.5:
        title, provenance = rng.choice(real_titles), "kaggle_title+synthetic_body"
    else:
        title = rng.choice(C.JOB_TITLES[cat]).format(s0=C.display(reqs[0]), s1=C.display(reqs[-1]), ind=ind)

    req_lines = [f"- {phrase(rng, s, 0.45)}" for s in reqs]
    deliverables = rng.sample(["working code in our repo", "short handover doc", "deployed demo",
                               "weekly progress updates", "test suite", "dashboard shared with the team"], k=2)
    desc = (f"We are a {ind} company. {rng.choice(['Looking for', 'Need', 'Hiring'])} someone for: {title.lower()}.\n\n"
            f"Requirements:\n" + "\n".join(req_lines) + "\n\nDeliverables: " + ", ".join(deliverables) + ".")
    scam_kind = None
    if scam:
        scam_kind, s = rng.choice(C.SCAM_TEMPLATES)
        desc += "\n\n" + s.format(thing=rng.choice(["dashboard", "app", "model"]), handle=f"hr_{rng.randint(100, 999)}",
                                  n=rng.randint(0, 99))
    if inject:
        desc += "\n\n" + rng.choice(C.INJECTIONS)

    client = rng.choice([c for c in clients if c["_shady"] == scam] or clients)
    hourly = rng.random() < 0.35
    st = stats[cat]["hourly" if hourly else "fixed"]
    mid = rng.uniform(st["p25"], st["p75"]) * (0.7 + 0.12 * len(reqs))
    if scam and scam_kind == "free_work":
        bmin = bmax = 0.0
    else:
        bmin, bmax = round(mid * 0.7, 0), round(mid * 1.25, 0)
    connects = 4 + 2 * min(int(bmax // 250), 6) if not hourly else 6 + 2 * min(int(bmax // 15), 5)
    qs = rng.sample(["Describe a similar project you delivered.", "How would you test this?",
                     "What is your availability?", "Which stack would you use and why?",
                     "Share a link to relevant work."], k=rng.choice([0, 1, 2]))
    return {
        "job_id": jid, "title": title, "description": desc, "skills": [C.display(s) for s in reqs],
        "category": cat, "budget_type": "hourly" if hourly else "fixed", "budget_min": bmin, "budget_max": bmax,
        "connects_required": connects, "client_id": client["client_id"], "screening_questions": qs,
        "posted_at": (NOW - timedelta(minutes=rng.randint(10, 60 * 24 * 30))).isoformat(),
        "provenance": provenance,
        "_gold": {"requirements": reqs, "scam": scam, "scam_kind": scam_kind, "injection": inject},
    }


def gold_for(job: dict, persona_ctx: dict, stats: dict) -> dict:
    persona, projects = persona_ctx["persona"], persona_ctx["projects"]
    reqs = job["_gold"]["requirements"]
    by_skill: dict[str, list[str]] = {}
    for p in projects:
        for s in p["skills"]:
            by_skill.setdefault(s, []).append(p["project_id"])
    supported = [r for r in reqs if r in by_skill]
    coverage = len(supported) / len(reqs)
    evidence = sorted({pid for r in supported for pid in by_skill[r]})
    if job["budget_type"] == "hourly":
        lo = max(job["budget_min"], persona["hourly_rate"] * 0.9)
        hi = min(job["budget_max"], persona["hourly_rate"] * 1.3)
    else:
        m = stats[job["category"]]["fixed"]["p50"] * (0.7 + 0.12 * len(reqs))
        lo = max(job["budget_min"], m * 0.75)
        hi = min(job["budget_max"], m * 1.15)
    if lo > hi:
        lo, hi = job["budget_min"], job["budget_max"]
    budget_ok = (job["budget_max"] >= persona["min_project"]) if job["budget_type"] == "fixed" \
        else job["budget_max"] >= persona["hourly_rate"] * 0.8
    bid = (not job["_gold"]["scam"]) and coverage >= 0.6 and budget_ok
    return {"bid": bid, "scam": job["_gold"]["scam"], "scam_kind": job["_gold"]["scam_kind"],
            "injection": job["_gold"]["injection"], "coverage": round(coverage, 2),
            "requirements": reqs, "supported_requirements": supported, "relevant_evidence": evidence,
            "fair_price_band": [round(lo, 2), round(hi, 2)], "budget_ok": budget_ok}


def hidden_prefs(rng: random.Random, job: dict, client: dict) -> dict:
    reqs = job["_gold"]["requirements"]
    return {
        "must_have_skills": rng.sample(reqs, k=min(len(reqs), rng.choice([1, 2, 2, 3]))),
        "budget_ceiling": round(job["budget_max"] * rng.uniform(0.85, 1.1), 2),
        "dislikes_generic_opening": client["_hidden"]["dislikes_generic_opening"],
        "responsiveness": client["_hidden"]["responsiveness"],
        "is_scammer": job["_gold"]["scam"],
    }


# ====================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dev-per-persona", type=int, default=200)
    ap.add_argument("--holdout-per-persona", type=int, default=50)
    ap.add_argument("--stream", type=int, default=300)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    kaggle = load_kaggle(ROOT / "data" / "raw")
    stats = market_stats(rng, kaggle)
    personas = {pid: build_persona(rng, pid, ROOT / "data" / "personas") for pid in C.PERSONAS}
    clients = build_clients(rng)
    by_client = {c["client_id"]: c for c in clients}

    jobs: list[dict] = []
    splits: dict[str, dict[str, list[dict]]] = {"dev": {}, "holdout": {}}
    other_cats = ["mobile", "design", "writing", "devops", "other"]
    for split, n in (("dev", args.dev_per_persona), ("holdout", args.holdout_per_persona)):
        for pid, ctx in personas.items():
            cat = ctx["persona"]["category"]
            adjacent = [c for c in ("ai_ml", "web", "data") if c != cat]
            rows = []
            for i in range(n):
                r = rng.random()
                kw = {}
                if r < 0.50:
                    jcat = cat
                    kw["extra_offdomain"] = rng.random() < 0.25
                elif r < 0.65:
                    jcat = rng.choice(adjacent)
                elif r < 0.80:
                    jcat = rng.choice(other_cats)
                elif r < 0.92:
                    jcat, kw["scam"] = cat, True
                else:
                    jcat, kw["inject"] = cat, True
                job = make_job(rng, f"{split}-{pid}-{i + 1:03d}", jcat, clients, stats, kaggle, **kw)
                jobs.append(job)
                rows.append({"job_id": job["job_id"], "persona": pid, "category": jcat,
                             "gold": gold_for(job, ctx, stats)})
            splits[split][pid] = rows

    # live stream: released over time by GigSim, with duplicates, scams and injections
    stream = []
    for i in range(args.stream):
        r = rng.random()
        cat = rng.choice(["ai_ml", "web", "data", "ai_ml", "web", "data"] + other_cats)
        job = make_job(rng, f"live-{i + 1:04d}", cat, clients, stats, kaggle,
                       scam=0.80 <= r < 0.90, inject=r >= 0.94)
        job["release_offset_min"] = i * 6           # one new job every ~6 sim-minutes
        stream.append(job)
        if rng.random() < 0.06 and stream:          # duplicate repost with small edits
            dup = json.loads(json.dumps(job))
            dup["job_id"] = f"live-{i + 1:04d}-dup"
            dup["description"] = dup["description"].replace("Requirements:", "Must have:")
            dup["release_offset_min"] += 30
            dup["_gold"]["duplicate_of"] = job["job_id"]
            stream.append(dup)
    jobs.extend(stream)

    hidden = {j["job_id"]: hidden_prefs(rng, j, by_client[j["client_id"]]) for j in jobs}

    # ---- write GigSim seed (public fields only; hidden prefs in their own file)
    seed = ROOT / "gigsim" / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    public_jobs = [{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs]
    public_clients = [{k: v for k, v in c.items() if not k.startswith("_")} for c in clients]
    (seed / "jobs.json").write_text(json.dumps(public_jobs))
    (seed / "clients.json").write_text(json.dumps(public_clients))
    (seed / "hidden_prefs.json").write_text(json.dumps(hidden))
    (seed / "market_stats.json").write_text(json.dumps(stats, indent=2))

    # ---- eval splits (gold labels live ONLY here)
    for split, per in splits.items():
        d = ROOT / "evals" / "datasets" / split
        d.mkdir(parents=True, exist_ok=True)
        for pid, rows in per.items():
            with open(d / f"{pid}.jsonl", "w") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")
    stream_gold = {j["job_id"]: j["_gold"] for j in stream}
    (ROOT / "evals" / "datasets" / "stream_gold.json").write_text(json.dumps(stream_gold))

    summary = {
        "seed": args.seed, "kaggle_used": bool(kaggle),
        "kaggle_files": (kaggle or {}).get("files", []), "kaggle_rows": (kaggle or {}).get("n_rows", 0),
        "personas": {pid: len(ctx["projects"]) for pid, ctx in personas.items()},
        "jobs_total": len(jobs), "stream_jobs": len(stream), "clients": len(clients),
        "dev": {pid: {"n": len(rows), "bid": sum(r["gold"]["bid"] for r in rows),
                      "scam": sum(r["gold"]["scam"] for r in rows),
                      "injection": sum(r["gold"]["injection"] for r in rows)}
                for pid, rows in splits["dev"].items()},
    }
    (ROOT / "evals" / "datasets" / "BUILD_SUMMARY.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

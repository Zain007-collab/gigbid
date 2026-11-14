"""R&D question A: does tool granularity change agent behaviour?

    python -m evals.runners.rq_a_tools [--model writer] [--n 40]

40 tasks with a known correct first call, asked to the same model with three tool sets:
  fine    — many narrow tools (get_job_budget, get_client_rating, ...)
  coarse  — two catch-all tools (marketplace_query(action, params), portfolio_query(...))
  ours    — the shipped MCP tool set with rewritten, example-bearing descriptions
Scores: tool-selection accuracy, argument accuracy (all gold args present and equal), tokens per task.
Tools are offered as function schemas only (selection is what is measured; nothing executes).
"""
from __future__ import annotations

import argparse
import json
import random
import time

from evals.runners.harness import ROOT, write


def T(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
I = {"type": "integer"}

TOOLSETS = {
    "fine": [
        T("get_job_title", "Get a job's title.", {"job_id": S}, ["job_id"]),
        T("get_job_description", "Get a job's description.", {"job_id": S}, ["job_id"]),
        T("get_job_budget", "Get a job's budget.", {"job_id": S}, ["job_id"]),
        T("get_job_connects", "Get Connects needed for a job.", {"job_id": S}, ["job_id"]),
        T("get_job_skills", "Get a job's skills.", {"job_id": S}, ["job_id"]),
        T("get_client_rating", "Get a client's rating.", {"client_id": S}, ["client_id"]),
        T("get_client_spend", "Get a client's total spend.", {"client_id": S}, ["client_id"]),
        T("get_client_hire_rate", "Get a client's hire rate.", {"client_id": S}, ["client_id"]),
        T("search_jobs_by_keyword", "Search jobs by keyword.", {"query": S}, ["query"]),
        T("search_jobs_by_skill", "Search jobs by skill.", {"skill": S}, ["skill"]),
        T("get_balance", "Get Connects balance.", {}, []),
        T("get_market_fixed_prices", "Fixed price stats.", {"category": S}, ["category"]),
        T("get_market_hourly_rates", "Hourly rate stats.", {"category": S}, ["category"]),
        T("list_threads", "List message threads.", {}, []),
        T("get_thread", "Get one thread's messages.", {"thread_id": S}, ["thread_id"]),
        T("search_case_studies", "Search case studies.", {"query": S}, ["query"]),
        T("search_repos", "Search repositories.", {"query": S}, ["query"]),
        T("search_reviews", "Search reviews.", {"query": S}, ["query"]),
        T("search_past_proposals", "Search past proposals.", {"query": S}, ["query"]),
        T("get_case_study", "Get a case study.", {"id": S}, ["id"]),
        T("list_skills", "List skills.", {}, []),
    ],
    "coarse": [
        T("marketplace_query", "Query the marketplace.", {"action": {"type": "string", "enum": [
            "search", "job", "client", "balance", "market_stats", "messages"]}, "params": {"type": "object"}}, ["action"]),
        T("portfolio_query", "Query the portfolio.", {"action": {"type": "string", "enum": [
            "search", "case_study", "skills"]}, "params": {"type": "object"}}, ["action"]),
    ],
    "ours": [
        T("search_jobs", "Search open jobs by free text and/or required skills. Returns summaries only — call get_job "
          "for the full post. Example: search_jobs(query='RAG chatbot', skills=['LangChain']).",
          {"query": S, "skills": {"type": "array", "items": S}, "limit": I}, []),
        T("get_job", "Full job post (description, budget, Connects cost, screening questions, client summary) for one "
          "job id such as 'dev-ai_ml-012'. Use for any question about one job's details or cost.", {"job_id": S}, ["job_id"]),
        T("get_client_history", "A client's track record: total spend, hire rate, rating, past contracts. Use to judge "
          "client quality or scam risk. Example: get_client_history(client_id='c0042').", {"client_id": S}, ["client_id"]),
        T("get_connects_balance", "The freelancer's available Connects (no arguments).", {}, []),
        T("get_market_stats", "Price percentiles (fixed budgets and hourly rates) for a job category: one of ai_ml, web, "
          "data, mobile, design, writing, devops, other.", {"category": S}, ["category"]),
        T("list_messages", "Client messages, optionally for one thread id like 't-p00012'.", {"thread_id": S}, []),
        T("search_evidence", "Find portfolio items (case studies, repos, code, reviews, past proposals) that prove ONE "
          "requirement. Example: search_evidence(requirement='background task queue with Celery').",
          {"requirement": S, "k": I}, ["requirement"]),
        T("get_case_study", "Full case study for a project id like 'ai_ml-p03'.", {"id": S}, ["id"]),
        T("list_skills", "Every skill evidenced in the portfolio, with counts (no arguments).", {}, []),
    ],
}

# gold first call per tool set: (tool, args) — args compared case-insensitively on the listed keys
def make_tasks(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    jobs = [j for j in json.loads((ROOT / "gigsim/seed/jobs.json").read_text()) if j["job_id"].startswith("dev-")]
    cats = ["ai_ml", "web", "data"]
    reqs = ["background task queue with Celery", "dbt models in Snowflake", "Next.js dashboard with Prisma",
            "fine-tuning a Hugging Face model", "A/B test analysis", "Stripe subscription billing"]
    templates = [
        (lambda j: f"How many Connects does it cost to apply to job {j['job_id']}?",
         {"fine": ("get_job_connects", {"job_id": "J"}), "coarse": ("marketplace_query", {"action": "job"}),
          "ours": ("get_job", {"job_id": "J"})}),
        (lambda j: f"Is the client behind job {j['job_id']} (client id {j['client_id']}) trustworthy? Check their history.",
         {"fine": ("get_client_rating", {"client_id": "C"}), "coarse": ("marketplace_query", {"action": "client"}),
          "ours": ("get_client_history", {"client_id": "C"})}),
        (lambda j: f"What is the typical fixed price for {j['category']} jobs?",
         {"fine": ("get_market_fixed_prices", {"category": "K"}), "coarse": ("marketplace_query", {"action": "market_stats"}),
          "ours": ("get_market_stats", {"category": "K"})}),
        (lambda j: "How many Connects do I have left?",
         {"fine": ("get_balance", {}), "coarse": ("marketplace_query", {"action": "balance"}),
          "ours": ("get_connects_balance", {})}),
        (lambda j: f"Find proof in my portfolio that I have done this: {j['_req']}.",
         {"fine": ("search_case_studies", {}), "coarse": ("portfolio_query", {"action": "search"}),
          "ours": ("search_evidence", {})}),
        (lambda j: "Find new open jobs about RAG chatbots.",
         {"fine": ("search_jobs_by_keyword", {}), "coarse": ("marketplace_query", {"action": "search"}),
          "ours": ("search_jobs", {})}),
        (lambda j: f"Show me the full post for job {j['job_id']}, including screening questions.",
         {"fine": ("get_job_description", {"job_id": "J"}), "coarse": ("marketplace_query", {"action": "job"}),
          "ours": ("get_job", {"job_id": "J"})}),
        (lambda j: "Which skills can I actually prove from my portfolio?",
         {"fine": ("list_skills", {}), "coarse": ("portfolio_query", {"action": "skills"}), "ours": ("list_skills", {})}),
    ]
    tasks = []
    for i in range(n):
        j = dict(rng.choice(jobs))
        j["_req"] = rng.choice(reqs)
        j["category"] = j["category"] if j["category"] in cats else rng.choice(cats)
        q, gold = templates[i % len(templates)]
        subst = {"J": j["job_id"], "C": j["client_id"], "K": j["category"]}
        tasks.append({"prompt": q(j), "gold": {k: (t, {a: subst.get(v, v) for a, v in args.items()})
                                               for k, (t, args) in gold.items()}})
    return tasks


def run(model_node: str, n: int) -> dict:
    from agent.llm import PROVIDERS, _chat, cost_usd
    from agent.settings import node_model

    spec = node_model(model_node)
    llm = _chat(spec)
    tasks = make_tasks(n)
    out = {"model": f"{spec['provider']}/{spec['model']}", "n_tasks": n, "toolsets": {}}
    for name, tools in TOOLSETS.items():
        bound = llm.bind_tools(tools, tool_choice="required")
        sel = arg = 0
        tok, lat, per = [], [], []
        for t in tasks:
            gold_tool, gold_args = t["gold"][name]
            t0 = time.perf_counter()
            try:
                msg = bound.invoke([("system", "You are a freelancer's assistant. Call exactly one tool to start answering."),
                                    ("user", t["prompt"])])
            except Exception as e:  # noqa: BLE001
                per.append({"prompt": t["prompt"], "error": str(e)[:200]})
                continue
            lat.append(time.perf_counter() - t0)
            calls = msg.tool_calls or []
            um = msg.usage_metadata or {}
            tok.append(um.get("input_tokens", 0) + um.get("output_tokens", 0))
            got = calls[0] if calls else {"name": None, "args": {}}
            ok_tool = got["name"] == gold_tool
            got_args = got.get("args") or {}
            if name == "coarse":
                got_args = {**got_args, **(got_args.get("params") or {})}
            ok_args = ok_tool and all(str(got_args.get(k, "")).lower() == str(v).lower() for k, v in gold_args.items())
            sel += ok_tool
            arg += ok_args
            per.append({"prompt": t["prompt"], "gold": [gold_tool, gold_args], "got": [got["name"], got_args],
                        "tool_ok": ok_tool, "args_ok": ok_args, "tokens": tok[-1]})
            time.sleep(0.5)                                  # stay inside free-tier rate limits
        done = len(tok)
        out["toolsets"][name] = {"n_tools": len(tools), "tool_selection_accuracy": round(sel / max(1, done), 4),
                                 "argument_accuracy": round(arg / max(1, done), 4),
                                 "tokens_per_task": round(sum(tok) / max(1, done), 1),
                                 "usd_per_task": round(cost_usd(spec, sum(tok) // max(1, done), 0), 6),
                                 "completed": done, "per_task": per}
        print(name, {k: v for k, v in out["toolsets"][name].items() if k != "per_task"}, flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="scout", help="config node whose model to use (scout = small model)")
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    print("wrote", write("rq_a_tools", run(a.model, a.n)))


if __name__ == "__main__":
    main()

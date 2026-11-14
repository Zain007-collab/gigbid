"""Outcome evaluation (B4): simulated client reply and hire rate on a fixed GigSim job set.

    python -m evals.runners.gigsim_outcomes [--per-persona 30] [--tag v2_hybrid]

The harness plays the human: it approves every proposal that reaches the approval interrupt (documented in the
report; in the product a person always decides). Simulated time then advances 10 days and proposal statuses are
read back through the marketplace MCP server. The job set is fixed: the first N gold-"bid" dev jobs per persona.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics

from evals.runners.harness import dev_rows, setup_gigsim, write


async def main_async(args) -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command

    from agent import mcp_client
    from agent.graphs.bid_graph import build_bid_graph, thread_id
    from agent.llm import online

    gs = setup_gigsim()
    graph = build_bid_graph(InMemorySaver(), InMemoryStore())
    results = []
    for persona in args.personas.split(","):
        user = f"out-{persona}"
        jobs = [r for r in dev_rows(persona) if r["gold"]["bid"]][: args.per_persona]
        for row in jobs:
            mcp_client.current_user.set(user)
            mcp_client.current_persona.set(persona)
            cfg = {"configurable": {"thread_id": thread_id(user, row["job_id"])}}
            await graph.ainvoke({"job_id": row["job_id"], "user_id": user, "persona": persona}, cfg)
            snap = await graph.aget_state(cfg)
            pending = [i.value for t in snap.tasks for i in t.interrupts]
            rec = {"job_id": row["job_id"], "persona": persona, "category": row["category"], "submitted": False}
            if pending:
                await graph.ainvoke(Command(resume={"action": "approve", "approval_id": f"eval-{row['job_id']}"}), cfg)
                v = (await graph.aget_state(cfg)).values
                sub = v.get("submission") or {}
                rec.update(submitted=v.get("status") == "submitted", proposal_id=sub.get("proposal_id"),
                           words=len(pending[0]["text"].split()))
            results.append(rec)
    gs.SIM.tick_min += 60 * 24 * 10                          # ten simulated days pass
    for persona in args.personas.split(","):
        mcp_client.current_user.set(f"out-{persona}")
        props = (await mcp_client.call("pipeline", "marketplace.list_proposals", {}))["proposals"]
        status = {p["proposal_id"]: p["status"] for p in props}
        for r in results:
            if r["persona"] == persona and r.get("proposal_id"):
                r["status"] = status.get(r["proposal_id"])
                r["client_model"] = {k: v for k, v in gs.SIM.proposals[r["proposal_id"]].get("_score", {}).items()}

    def agg(rs):
        sub = [r for r in rs if r["submitted"]]
        replied = [r for r in sub if r.get("status") in ("replied", "interview", "hired")]
        return {"jobs": len(rs), "submitted": len(sub),
                "reply_rate": round(len(replied) / len(sub), 4) if sub else None,
                "hire_rate": round(sum(r.get("status") == "hired" for r in sub) / len(sub), 4) if sub else None,
                "mean_p_reply_client_model": round(statistics.mean(r["client_model"].get("p_reply", 0) for r in sub), 4) if sub else None,
                "generic_opening_rate": round(statistics.mean(bool(r["client_model"].get("generic_opening")) for r in sub), 4) if sub else None,
                "must_have_coverage": round(statistics.mean(r["client_model"].get("coverage", 0) for r in sub), 4) if sub else None}

    out = {"tag": args.tag, "llm_online": online("writer"), "overall": agg(results),
           "by_persona": {p: agg([r for r in results if r["persona"] == p]) for p in args.personas.split(",")},
           "jobs": results}
    print(args.tag, out["overall"], {p: v["reply_rate"] for p, v in out["by_persona"].items()})
    print("wrote", write(f"gigsim_outcomes_{args.tag}", out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", default="ai_ml,fullstack,data_analyst")
    ap.add_argument("--per-persona", type=int, default=30)
    ap.add_argument("--tag", default="offline")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()

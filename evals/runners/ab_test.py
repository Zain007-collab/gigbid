"""Signature extension X2: proposal strategies as an A/B test in GigSim, with significance tests.

    python -m evals.runners.ab_test [--per-persona 60] [--a evidence_first] [--b concise_question]

Each gold-"bid" dev job is randomly (seeded) assigned to strategy A or B; the bid graph drafts with that strategy,
the harness approves (as a human would), GigSim's simulated clients respond over ten simulated days, and reply
rates are compared with a two-proportion z-test and Fisher's exact test.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
from math import comb

from evals.runners.harness import dev_rows, setup_gigsim, write


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0
    z = (x1 / n1 - x2 / n2) / se if se else 0.0
    return z, math.erfc(abs(z) / math.sqrt(2))          # two-sided p-value


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]."""
    n, r1, c1 = a + b + c + d, a + b, a + c
    def prob(x):
        return comb(c1, x) * comb(n - c1, r1 - x) / comb(n, r1)
    p0 = prob(a)
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p0 + 1e-12))


async def main_async(args) -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command

    from agent import mcp_client
    from agent.graphs.bid_graph import build_bid_graph, thread_id

    gs = setup_gigsim()
    graph = build_bid_graph(InMemorySaver(), InMemoryStore())
    rng = random.Random(args.seed)
    arms = {args.a: [], args.b: []}
    for persona in args.personas.split(","):
        for row in [r for r in dev_rows(persona) if r["gold"]["bid"]][: args.per_persona]:
            arm = rng.choice([args.a, args.b])
            user = f"ab-{persona}"
            mcp_client.current_user.set(user)
            mcp_client.current_persona.set(persona)
            cfg = {"configurable": {"thread_id": thread_id(user, row["job_id"])}}
            await graph.ainvoke({"job_id": row["job_id"], "user_id": user, "persona": persona, "strategy": arm}, cfg)
            snap = await graph.aget_state(cfg)
            if not [i for t in snap.tasks for i in t.interrupts]:
                continue
            await graph.ainvoke(Command(resume={"action": "approve", "approval_id": f"ab-{row['job_id']}"}), cfg)
            sub = (await graph.aget_state(cfg)).values.get("submission") or {}
            if sub.get("proposal_id"):
                arms[arm].append({"job_id": row["job_id"], "persona": persona, "proposal_id": sub["proposal_id"]})
    gs.SIM.tick_min += 60 * 24 * 10
    for arm, rs in arms.items():
        for r in rs:
            p = gs.SIM.proposals[r["proposal_id"]]
            r["status"], r["client_model"] = p["status"], p.get("_score", {})
    rep = {arm: sum(r["status"] in ("replied", "interview", "hired") for r in rs) for arm, rs in arms.items()}
    n = {arm: len(rs) for arm, rs in arms.items()}
    z, p_z = two_proportion_z(rep[args.a], n[args.a], rep[args.b], n[args.b])
    p_f = fisher_exact(rep[args.a], n[args.a] - rep[args.a], rep[args.b], n[args.b] - rep[args.b])
    out = {"a": args.a, "b": args.b, "seed": args.seed,
           "arms": {arm: {"n": n[arm], "replies": rep[arm], "reply_rate": round(rep[arm] / n[arm], 4) if n[arm] else None,
                          "hires": sum(r["status"] == "hired" for r in arms[arm])} for arm in arms},
           "z": round(z, 3), "p_value_z": round(p_z, 4), "p_value_fisher": round(p_f, 4),
           "significant_at_0.05": p_f < 0.05, "proposals": arms}
    print({k: v for k, v in out.items() if k != "proposals"})
    print("wrote", write(f"ab_test_{args.a}_vs_{args.b}", out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", default="ai_ml,fullstack,data_analyst")
    ap.add_argument("--per-persona", type=int, default=60)
    ap.add_argument("--a", default="evidence_first")
    ap.add_argument("--b", default="concise_question")
    ap.add_argument("--seed", type=int, default=11)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()

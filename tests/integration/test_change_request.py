"""Change request (2 Nov policy update, simulated as GigSim policy v2): absorbed by config, not code."""
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent import mcp_client, settings
from agent.graphs.bid_graph import build_bid_graph, thread_id

pytestmark = pytest.mark.anyio


async def test_policy_v2_absorbed_by_config(gigsim, monkeypatch):
    gigsim.SIM.policy_version = 2
    cfg = settings.load_config()
    monkeypatch.setitem(cfg["policy"], "bidding_rules", [{"rule": "max_proposals_per_client_per_week", "value": 2}])
    # three bid-worthy jobs from the same client
    jobs = json.loads((Path(__file__).resolve().parents[2] / "gigsim/seed/jobs.json").read_text())
    by_client: dict[str, list[str]] = {}
    for j in jobs:
        if j["job_id"].startswith("dev-ai_ml") and j["category"] == "ai_ml" and "release_offset_min" not in j:
            by_client.setdefault(j["client_id"], []).append(j["job_id"])
    g = build_bid_graph(InMemorySaver(), InMemoryStore())
    mcp_client.current_user.set("cr")
    mcp_client.current_persona.set("ai_ml")
    submitted = skipped_by_policy = 0
    for client, ids in by_client.items():
        if len(ids) < 3:
            continue
        for jid in ids[:4]:
            c = {"configurable": {"thread_id": thread_id("cr", jid)}}
            await g.ainvoke({"job_id": jid, "user_id": "cr", "persona": "ai_ml"}, c)
            snap = await g.aget_state(c)
            if [i for t in snap.tasks for i in t.interrupts]:
                await g.ainvoke(Command(resume={"action": "approve", "approval_id": f"cr-{jid}"}), c)
                submitted += (await g.aget_state(c)).values.get("status") == "submitted"
            elif "platform policy" in (snap.values.get("attention_reason") or ""):
                skipped_by_policy += 1
        if submitted >= 2:
            break
    per_client = {}
    for p in gigsim.SIM.proposals.values():
        per_client[p["client_id"]] = per_client.get(p["client_id"], 0) + 1
    assert max(per_client.values(), default=0) <= 2          # GigBid never tripped the platform rule

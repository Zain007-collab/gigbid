"""Bid graph end to end against GigSim (offline fallbacks, hash embeddings)."""
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent import mcp_client
from agent.graphs.bid_graph import build_bid_graph, thread_id

pytestmark = pytest.mark.anyio
DEV = [json.loads(l) for l in (Path(__file__).resolve().parents[2] / "evals/datasets/dev/ai_ml.jsonl").read_text().splitlines()]


def pick(pred):
    return next(r["job_id"] for r in DEV if pred(r["gold"]))


async def run(graph, job_id, user="u1", resume=None):
    mcp_client.current_user.set(user)
    mcp_client.current_persona.set("ai_ml")
    cfg = {"configurable": {"thread_id": thread_id(user, job_id)}}
    inp = Command(resume=resume) if resume is not None else {"job_id": job_id, "user_id": user, "persona": "ai_ml"}
    await graph.ainvoke(inp, cfg)
    return await graph.aget_state(cfg)


def pending(snap):
    return [i.value for t in snap.tasks for i in t.interrupts]


async def test_scam_is_skipped_without_proposal(gigsim):
    g = build_bid_graph(InMemorySaver(), InMemoryStore())
    job = pick(lambda gold: gold["scam"] and gold["scam_kind"] in ("off_platform_payment", "scam", "free_work"))
    snap = await run(g, job)
    assert snap.values["status"] == "skipped", snap.values.get("decision")
    assert not pending(snap) and not gigsim.SIM.proposals


async def test_bid_pauses_for_approval_then_submits_once(gigsim):
    saver = InMemorySaver()
    g = build_bid_graph(saver, InMemoryStore())
    job = pick(lambda gold: gold["bid"] and gold["coverage"] == 1.0 and not gold["injection"])
    snap = await run(g, job)
    assert snap.values.get("status") not in ("dead_letter",), snap.values.get("attention_reason")
    if snap.values.get("status") in ("skipped", "watch", "blocked"):
        pytest.skip(f"offline fallback did not bid on {job}: {snap.values.get('decision')}")
    [payload] = pending(snap)
    assert payload["preview"]["connects_cost"] > 0 and payload["claims"]
    assert not gigsim.SIM.proposals                      # nothing sent before approval

    # --- crash: the API process dies while the approval is pending; a new graph object takes over
    g2 = build_bid_graph(saver, InMemoryStore())
    snap = await run(g2, job, resume={"action": "approve", "approval_id": "appr_test1"})
    assert snap.values["status"] == "submitted"
    assert len(gigsim.SIM.proposals) == 1
    # a duplicate resume (double click / retry) must not submit again
    await run(g2, job, resume={"action": "approve", "approval_id": "appr_test1"})
    assert len(gigsim.SIM.proposals) == 1


async def test_reject_with_reason(gigsim):
    g = build_bid_graph(InMemorySaver(), InMemoryStore())
    job = pick(lambda gold: gold["bid"] and gold["coverage"] == 1.0)
    snap = await run(g, job)
    if not pending(snap):
        pytest.skip("no approval reached offline")
    snap = await run(g, job, resume={"action": "reject", "reason": "client too far timezone", "approval_id": "a2"})
    assert snap.values["status"] == "rejected" and "timezone" in snap.values["attention_reason"]
    assert not gigsim.SIM.proposals


async def test_edit_is_rechecked_and_feeds_style_memory(gigsim):
    store = InMemoryStore()
    g = build_bid_graph(InMemorySaver(), store)
    job = pick(lambda gold: gold["bid"] and gold["coverage"] == 1.0)
    snap = await run(g, job)
    if not pending(snap):
        pytest.skip("no approval reached offline")
    text = pending(snap)[0]["text"]
    edited = text.split(". ", 1)[1] if ". " in text else text      # user cuts the first sentence
    snap = await run(g, job, resume={"action": "edit", "edited_text": edited, "approval_id": "a3"})
    cands = await store.asearch(("style_candidates", "u1"))
    assert any(c.key == "cut_first_sentence" for c in cands)
    if pending(snap):                                    # compliance/claims re-check asked again
        snap = await run(g, job, resume={"action": "approve", "approval_id": "a3"})
    assert snap.values["status"] == "submitted"


async def test_marketplace_outage_pauses_and_resumes(gigsim):
    saver = InMemorySaver()
    g = build_bid_graph(saver, InMemoryStore())
    job = pick(lambda gold: gold["bid"] and gold["coverage"] == 1.0)
    gigsim.SIM.down = True
    with pytest.raises(mcp_client.MarketplaceDown):
        await run(g, job)
    gigsim.SIM.down = False
    cfg = {"configurable": {"thread_id": thread_id("u1", job)}}
    await g.ainvoke(None, cfg)                          # worker retry: resumes the failed node
    snap = await g.aget_state(cfg)
    assert snap.values.get("job") and snap.values.get("status") != "dead_letter"


async def test_past_proposals_never_count_as_proof(gigsim):
    """Regression: the LLM writer once cited a past *proposal* ('I have shipped exactly that') as proof."""
    g = build_bid_graph(InMemorySaver(), InMemoryStore())
    job = pick(lambda gold: gold["bid"] and gold["coverage"] == 1.0)
    snap = await run(g, job)
    fit = snap.values["fit"]
    assert all(e.type != "proposal" for m in fit.matches for e in m.evidence)
    for p in pending(snap):
        assert all(e["type"] != "proposal" for c in p["claims"] for e in c["evidence"])


def test_type_filter_applies_before_ranking():
    """Regression: filtering after top-k left 14% of requirements with no evidence (proposals filled the top-k)."""
    import os
    os.environ.setdefault("RAG_EMBED_BACKEND", "hash")
    from rag import retriever
    pipe = retriever.build("shipped", "ai_ml")
    ev = pipe.retrieve("python", ["python"], k=5, types={"case_study", "readme", "code", "repo", "review", "cv"})
    assert ev.hits and all(h.chunk.meta.get("type") != "proposal" for h in ev.hits)

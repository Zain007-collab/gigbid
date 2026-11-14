"""Bid graph (C1): one thread per job.

intake → (Job Analyst ‖ Risk Assessor ‖ Fit Matcher via Send) → decide ─skip/watch→ END
                                                                 └bid→ pricing → writer → verifier → critic
critic ─revise (≤2)→ writer ; ─ok→ compliance ─blocked→ END ; ─ok→ approval (interrupt) → submit → END
"""
from __future__ import annotations

import difflib
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from agent.handoffs import ApprovalDecision, Claim, ProposalDraft
from agent.memory import promotion_rules, store as memory
from agent.nodes.analysis import fit_matcher, job_analyst, risk_assessor
from agent.nodes.base import node, tool
from agent.nodes.proposal import check_claims, comply, compliance, critic, decide, pricing, verifier, writer
from agent.settings import section
from agent.state import HALT, BidState


def thread_id(user_id: str, job_id: str) -> str:
    return f"bid:{user_id}:{job_id}"


@node("intake")
async def intake(state: dict) -> dict:
    job = (await tool("intake", "marketplace.get_job", {"job_id": state["job_id"]}))["job"]
    return {"job": job, "status": "running"}


def fan_out(state: dict):
    if state.get("status") in HALT:
        return END
    return [Send("job_analyst", state), Send("risk_assessor", state), Send("fit_matcher", state)]


def after(nxt: str):
    def route(state: dict) -> str:
        return END if state.get("status") in HALT else nxt
    route.__name__ = f"to_{nxt}"
    return route


def after_decide(state: dict) -> str:
    if state.get("status") in HALT or state["decision"].action != "bid":
        return END
    return "pricing"


def after_critic(state: dict) -> str:
    if state.get("status") in HALT:
        return END
    c = state["critiques"][-1]
    if c.approve:
        return "compliance"
    return "writer" if state.get("revision", 0) <= section("budgets")["critic_max_revisions"] else "compliance"


def approval_payload(state: dict, text: str, preview: dict, note: str | None = None) -> dict:
    draft: ProposalDraft = state["drafts"][-1]
    ev = {e.id: {"id": e.id, "type": e.type, "project": e.project, "text": e.text[:600]}
          for m in state["fit"].matches for e in m.evidence}
    return {
        "type": "proposal_approval", "job_id": state["job_id"], "job_title": state["job"]["title"],
        "text": text, "draft_version": draft.version,
        "claims": [{**c.model_dump(), "evidence": [ev[i] for i in c.evidence_ids if i in ev]} for c in draft.claims],
        "screening_answers": [a.model_dump() for a in draft.screening_answers],
        "risk": state["risk"].model_dump(), "price": state["price"].model_dump(),
        "fit": state["fit"].model_dump(), "decision": state["decision"].model_dump(),
        "critiques": [c.model_dump() for c in state.get("critiques", [])],
        "compliance": state["compliance"].model_dump(), "preview": preview,
        "unsupported_before_verify": draft.unsupported_before_verify, "note": note,
    }


@node("approval")
async def approval(state: dict) -> Command:
    """Human approval (R4/C5). Nothing leaves the system without this interrupt being resumed."""
    text = state.get("pending_text") or state["compliance"].cleaned_text
    price = state["price"]
    preview = (await tool("approval", "marketplace.create_proposal_draft", {
        "job_id": state["job_id"], "cover_letter": text, "rate_type": price.rate_type, "bid_amount": price.amount,
        "idempotency_key": f"{thread_id(state['user_id'], state['job_id'])}:preview", "dry_run": True,
        "screening_answers": [{"question": a.question, "answer": a.answer} for a in state["drafts"][-1].screening_answers],
    }))["preview"]
    preview["submit_connects_cost"] = price.connects_cost
    raw = interrupt(approval_payload(state, text, preview, state.get("attention_reason")))
    decision = ApprovalDecision.model_validate(raw)

    if decision.action == "reject":
        return Command(goto=END, update={"approval": decision, "status": "rejected",
                                         "attention_reason": f"rejected by user: {decision.reason or 'no reason'}"})
    if decision.action == "approve":
        return Command(goto="submit", update={"approval": decision, "status": "running"})

    # edit: diff -> style memory candidates; re-check claims + compliance on the edited text
    edited = (decision.edited_text or "").strip()
    await promotion_rules.observe_edit(memory.get(), state["user_id"], state["job_id"], text, edited)
    old = {c.text: c.evidence_ids for c in state["drafts"][-1].claims}
    sentences = [s for s in _split(edited) if s]
    claims = await check_claims([Claim(text=s, evidence_ids=old.get(s, [])) for s in sentences], state["fit"], False)
    unsupported = [c.text for c in claims if c.status == "unsupported"]
    cleaned, violations = comply(edited)
    new_draft = state["drafts"][-1].model_copy(update={
        "version": state["drafts"][-1].version + 1, "cover_letter": edited, "claims": claims,
        "unsupported_after_verify": len(unsupported)})
    if unsupported or violations:
        note = ("Your edit was re-checked. " + (f"Unsupported claims: {unsupported}. " if unsupported else "")
                + (f"Compliance changed: {[v.rule for v in violations]}." if violations else ""))
        return Command(goto="approval", update={"drafts": [new_draft], "pending_text": cleaned,
                                                "attention_reason": note})
    return Command(goto="submit", update={"drafts": [new_draft], "pending_text": edited,
                                          "approval": decision, "status": "running",
                                          "edit_diff": "\n".join(difflib.unified_diff(
                                              _split(text), _split(edited), lineterm=""))})


def _split(t: str) -> list[str]:
    import re
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s.strip()]


@node("submit")
async def submit(state: dict) -> dict:
    """Exactly-once: both writes carry idempotency keys derived from the thread and draft version."""
    tid = thread_id(state["user_id"], state["job_id"])
    text = state.get("pending_text") or state["compliance"].cleaned_text
    price, appr = state["price"], state["approval"]
    version = state["drafts"][-1].version
    d = await tool("submit", "marketplace.create_proposal_draft", {
        "job_id": state["job_id"], "cover_letter": text, "rate_type": price.rate_type, "bid_amount": price.amount,
        "idempotency_key": f"{tid}:draft:v{version}", "dry_run": False, "approval_id": appr.approval_id,
        "screening_answers": [{"question": a.question, "answer": a.answer} for a in state["drafts"][-1].screening_answers],
    })
    s = await tool("submit", "marketplace.submit_proposal", {
        "job_id": state["job_id"], "draft_id": d["receipt"]["draft_id"], "idempotency_key": f"{tid}:submit",
        "dry_run": False, "approval_id": appr.approval_id})
    return {"submission": {**s["receipt"], "write_status": s["status"]}, "status": "submitted"}


class _BidState(BidState, total=False):
    pending_text: str
    edit_diff: str


def build_bid_graph(checkpointer=None, store=None):
    from agent.graphs.serde import serializer
    if checkpointer is not None and hasattr(checkpointer, "serde"):
        checkpointer.serde = serializer()
    if store is not None:
        memory.configure(store)
    g = StateGraph(_BidState)
    for name, fn in [("intake", intake), ("job_analyst", job_analyst), ("risk_assessor", risk_assessor),
                     ("fit_matcher", fit_matcher), ("decide", decide), ("pricing", pricing), ("writer", writer),
                     ("verifier", verifier), ("critic", critic), ("compliance", compliance), ("submit", submit)]:
        g.add_node(name, fn)
    g.add_node("approval", approval, destinations=("submit", "approval", END))
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", fan_out, ["job_analyst", "risk_assessor", "fit_matcher", END])
    for n in ("job_analyst", "risk_assessor", "fit_matcher"):
        g.add_edge(n, "decide")
    g.add_conditional_edges("decide", after_decide, ["pricing", END])
    g.add_conditional_edges("pricing", after("writer"), ["writer", END])
    g.add_conditional_edges("writer", after("verifier"), ["verifier", END])
    g.add_conditional_edges("verifier", after("critic"), ["critic", END])
    g.add_conditional_edges("critic", after_critic, ["writer", "compliance", END])
    g.add_conditional_edges("compliance", after("approval"), ["approval", END])
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer, store=store)


def new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:12]}"

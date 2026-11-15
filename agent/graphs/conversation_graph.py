"""Conversation graph (C1): one thread per client conversation.

fetch → classify intent (question | negotiation | interview_request | scam_signal | other)
      → gather (evidence / calendar slots / price floor) → draft reply → compliance → approval (interrupt) → send
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent import llm, prompting
from agent.handoffs import ApprovalDecision, Claim, ComplianceVerdict, MessageIntent, ReplyDraft
from agent.nodes.analysis import RISK_PATTERNS
from agent.nodes.base import node, tool
from agent.nodes.proposal import comply, persona_profile
from agent.state import HALT, ConversationState


def conv_thread_id(user_id: str, thread_id: str) -> str:
    return f"conv:{user_id}:{thread_id}"


@node("liaison_fetch")
async def fetch(state: dict) -> dict:
    msgs = (await tool("liaison", "marketplace.list_messages", {"thread_id": state["thread_id"]}))["messages"]
    if not msgs or msgs[-1]["sender"] != "client":
        return {"messages": msgs, "status": "skipped", "attention_reason": "no new client message"}
    return {"messages": msgs, "status": "running"}


INTENT_RULES = [
    ("scam_signal", re.compile(RISK_PATTERNS["off_platform_contact"][0] + "|" + RISK_PATTERNS["off_platform_payment"][0]
                               + "|" + RISK_PATTERNS["free_work"][0], re.I)),
    ("interview_request", re.compile(r"\b(call|interview|meet(ing)?|chat|zoom|slots?|available|free for)\b", re.I)),
    ("negotiation", re.compile(r"(\$\s?\d|\bprice\b|\bbudget\b|\brate\b|discount|could you do it for|lower)", re.I)),
    ("question", re.compile(r"\?")),
]


@node("liaison_intent")
async def classify(state: dict) -> dict:
    last = state["messages"][-1]["body"]
    inj = bool(re.search(RISK_PATTERNS["prompt_injection"][0], last, re.I))
    intent = next((name for name, pat in INTENT_RULES if pat.search(last)), "other")
    res = MessageIntent(thread_id=state["thread_id"], intent=intent, confidence=0.7, injection_detected=inj)
    if llm.online("intent"):
        out, _ = llm.structured("intent", MessageIntent,
                                prompting.render("liaison_intent", untrusted_rules=prompting.load("_untrusted_rules")),
                                prompting.untrusted("client_messages", "\n".join(
                                    f"{m['sender']}: {m['body']}" for m in state["messages"][-6:])))
        if out is not None:
            # rules win for scam signals (never let an LLM talk us out of a safety flag)
            res = out if intent != "scam_signal" else res
            res.injection_detected = res.injection_detected or inj
            res.thread_id = state["thread_id"]
    return {"intent": res}


@node("liaison_gather")
async def gather(state: dict) -> dict:
    intent = state["intent"].intent
    last = state["messages"][-1]["body"]
    out: dict = {"evidence": [], "slots": []}
    if intent == "question":
        res = await tool("liaison", "portfolio.search_evidence", {"requirement": last[:300], "k": 3})
        out["evidence"] = res.get("items", [])
    if intent == "interview_request":
        start = date.today() + timedelta(days=1)
        res = await tool("liaison", "workspace.free_slots", {"from_date": start.isoformat(), "days": 5,
                                                             "duration_min": 30, "tz": "Asia/Karachi", "max_slots": 3})
        out["slots"] = res.get("slots", [])[:2]
    return out


@node("liaison_draft")
async def draft(state: dict) -> dict:
    intent = state["intent"].intent
    last = state["messages"][-1]["body"]
    prof = persona_profile(state["persona"])
    floor = f"${prof['hourly_rate']}/hour or ${prof['min_project']} minimum per project"
    reply = None
    if llm.online("liaison"):
        sys = prompting.render("liaison_reply", floor=floor, untrusted_rules=prompting.load("_untrusted_rules"))
        user = (f"INTENT: {intent}\nSLOTS: {state.get('slots')}\nEVIDENCE: "
                + " | ".join(f"[{e['id']}] {e['snippet'][:300]}" for e in state.get("evidence", []))
                + "\n\n" + prompting.untrusted("client_messages", "\n".join(
                    f"{m['sender']}: {m['body']}" for m in state["messages"][-6:])))
        reply, _ = llm.structured("liaison", ReplyDraft, sys, user)
    if reply is None:
        reply = template_reply(state["thread_id"], intent, last, state.get("slots", []), state.get("evidence", []), prof)
    reply.thread_id = state["thread_id"]
    return {"reply": reply}


def template_reply(thread_id: str, intent: str, last: str, slots: list[dict], evidence: list[dict], prof: dict) -> ReplyDraft:
    claims: list[Claim] = []
    if intent == "scam_signal":
        body = ("Thanks for the message. I keep all communication and payments on the platform, so let's continue "
                "here. Happy to answer any questions about the project in this thread.")
    elif intent == "interview_request" and slots:
        fmt = lambda s: s["start"].replace("T", " ")[:16]  # noqa: E731
        body = (f"Thanks, happy to talk. I'm free {fmt(slots[0])}" + (f" or {fmt(slots[1])}" if len(slots) > 1 else "")
                + " (PKT). Which works best for you?")
    elif intent == "negotiation":
        # the client's offer is the amount they ask for ("could you do it for $940"), not our bid they quote
        asked = re.search(r"(?:for|at|to|offer|pay)\s*\$\s?(\d[\d,]*)", last, re.I)
        amounts = re.findall(r"\$\s?(\d[\d,]*)", last)
        raw = asked.group(1) if asked else (amounts[-1] if amounts else None)
        offer = float(raw.replace(",", "")) if raw else None
        if offer is not None and offer >= prof["min_project"]:
            body = f"${offer:.0f} works for me if we keep the scope as described. Shall I start with the first milestone?"
        else:
            body = (f"I can't go below ${prof['min_project']} for this scope, but I can trim it: I'd deliver the core "
                    "feature first and leave the extras for a second milestone. Would that work?")
    elif intent == "question" and re.search(r"\b(walk me through|structure|approach|plan|first week|how would you|timeline)\b", last, re.I):
        body = ("Here's how I'd run the first week: day 1, a short call to agree the success metric and get access; "
                "days 2-3, a working first slice of the core feature on a branch you can review; days 4-5, tests, a "
                "short handover note and a demo. I'd send progress updates twice a week. Does that fit your timeline?")
    elif intent == "question" and evidence:
        e = evidence[0]
        title = re.search(r"^#\s*(.+)$", e["snippet"], re.M)
        result = re.search(r"We (.+?)\.", e["snippet"])
        body = ("Good question. " + (f"On “{title.group(1).strip()}” I {result.group(1).strip()}." if title and result
                                     else "I've done closely related work in my portfolio.")
                + " Happy to walk you through how I'd apply that here.")
        claims.append(Claim(text=body, evidence_ids=[e["id"]]))
    else:
        body = "Thanks for getting back to me. Could you tell me a bit more about what you need next?"
    return ReplyDraft(thread_id=thread_id, body=body, claims=claims)


@node("liaison_compliance")
async def compliance(state: dict) -> dict:
    cleaned, violations = comply(state["reply"].body)
    allowed = len(cleaned.split()) >= 5
    v = ComplianceVerdict(job_id=state.get("job_id") or state["thread_id"], allowed=allowed, violations=violations,
                          cleaned_text=cleaned, block_reason=None if allowed else "reply empty after compliance")
    out = {"compliance": v}
    if not allowed:
        out.update(status="blocked", attention_reason=v.block_reason)
    return out


@node("liaison_approval")
async def approval(state: dict) -> Command:
    text = state.get("pending_text") or state["compliance"].cleaned_text
    preview = (await tool("approval", "marketplace.send_message", {
        "thread_id": state["thread_id"], "body": text, "idempotency_key": f"{state['_tid']}:preview",
        "dry_run": True}))["preview"]
    raw = interrupt({"type": "message_approval", "thread_id": state["thread_id"], "text": text,
                     "intent": state["intent"].model_dump(), "slots": state.get("slots", []),
                     "client_messages": state["messages"][-6:], "evidence": state.get("evidence", []),
                     "compliance": state["compliance"].model_dump(), "preview": preview,
                     "note": state.get("attention_reason")})
    d = ApprovalDecision.model_validate(raw)
    if d.action == "reject":
        return Command(goto=END, update={"approval": d, "status": "rejected",
                                         "attention_reason": f"rejected by user: {d.reason or 'no reason'}"})
    if d.action == "edit":
        cleaned, violations = comply(d.edited_text or "")
        if violations:
            return Command(goto="liaison_approval", update={"pending_text": cleaned, "attention_reason":
                                                            f"edit re-checked; compliance changed {[v.rule for v in violations]}"})
        return Command(goto="liaison_send", update={"approval": d, "pending_text": cleaned})
    return Command(goto="liaison_send", update={"approval": d})


@node("liaison_send")
async def send(state: dict) -> dict:
    text = state.get("pending_text") or state["compliance"].cleaned_text
    n = len(state["messages"])
    r = await tool("send", "marketplace.send_message", {
        "thread_id": state["thread_id"], "body": text, "idempotency_key": f"{state['_tid']}:reply:{n}",
        "dry_run": False, "approval_id": state["approval"].approval_id})
    out = {"sent": r["receipt"], "status": "sent"}
    if state["intent"].intent == "interview_request" and state.get("slots"):
        hold = await tool("send", "workspace.create_interview_hold", {
            "start": state["slots"][0]["start"], "idempotency_key": f"{state['_tid']}:hold:{n}",
            "title": f"Client interview (tentative) — {state['thread_id']}"})
        out["sent"] = {**out["sent"], "calendar_hold": hold}
    return out


class _ConvState(ConversationState, total=False):
    pending_text: str
    _tid: str


def after(nxt: str):
    def route(state: dict) -> str:
        return END if state.get("status") in HALT else nxt
    route.__name__ = f"to_{nxt}"
    return route


def build_conversation_graph(checkpointer=None, store=None):
    from agent.graphs.serde import serializer
    if checkpointer is not None and hasattr(checkpointer, "serde"):
        checkpointer.serde = serializer()
    g = StateGraph(_ConvState)
    g.add_node("liaison_fetch", fetch)
    g.add_node("liaison_intent", classify)
    g.add_node("liaison_gather", gather)
    g.add_node("liaison_draft", draft)
    g.add_node("liaison_compliance", compliance)
    g.add_node("liaison_approval", approval, destinations=("liaison_send", "liaison_approval", END))
    g.add_node("liaison_send", send)
    g.add_edge(START, "liaison_fetch")
    g.add_conditional_edges("liaison_fetch", after("liaison_intent"), ["liaison_intent", END])
    g.add_edge("liaison_intent", "liaison_gather")
    g.add_edge("liaison_gather", "liaison_draft")
    g.add_edge("liaison_draft", "liaison_compliance")
    g.add_conditional_edges("liaison_compliance", after("liaison_approval"), ["liaison_approval", END])
    g.add_edge("liaison_send", END)
    return g.compile(checkpointer=checkpointer, store=store)

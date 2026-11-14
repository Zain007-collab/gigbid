"""Typed graph state (C2).

Three things are kept apart:
  * State (this file): what one job / conversation needs right now.
  * Execution trace (`trace` field, append-only): every node, tool call, args
    (PII-masked), result summary, tokens and cost — for audit and debugging.
  * Long-term memory (agent/memory): only promoted knowledge, written to the
    LangGraph store by explicit rules. Raw tool output never goes there.

Parallel branches (analyst / risk / matcher) write disjoint keys; shared
accumulators (trace, usage, steps, errors) have reducers.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from agent.handoffs import (
    ApprovalDecision, BidDecision, ComplianceVerdict, Critique, FitReport, JobSpec, MessageIntent,
    PriceQuote, ProposalDraft, ReplyDraft, RiskReport,
)

Status = Literal[
    "running", "awaiting_approval", "submitted", "skipped", "watch", "rejected", "blocked",
    "needs_attention", "dead_letter", "degraded_queued", "sent",
]
HALT = {"skipped", "watch", "rejected", "blocked", "needs_attention", "dead_letter", "degraded_queued"}


def merge_usage(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / USD / latency per node across parallel branches and loops."""
    out = {k: dict(v) for k, v in (a or {}).items()}
    for node, u in (b or {}).items():
        cur = out.setdefault(node, {"tokens_in": 0, "tokens_out": 0, "usd": 0.0, "latency_s": 0.0, "calls": 0})
        for k in ("tokens_in", "tokens_out", "usd", "latency_s", "calls"):
            cur[k] = round(cur.get(k, 0) + u.get(k, 0), 6)
        if u.get("model"):
            cur["model"] = u["model"]
    return out


def last_wins(a, b):
    return b if b is not None else a


class BidState(TypedDict, total=False):
    # identity
    job_id: str
    user_id: str
    persona: str
    # inputs
    job: dict                                   # marketplace Job (untrusted text inside)
    # parallel analyses (disjoint keys)
    job_spec: JobSpec
    risk: RiskReport
    fit: FitReport
    # decision + downstream
    decision: BidDecision
    price: PriceQuote
    drafts: Annotated[list[ProposalDraft], operator.add]
    critiques: Annotated[list[Critique], operator.add]
    revision: int
    compliance: ComplianceVerdict
    preview: dict                               # dry_run preview shown on the approval screen
    approval: ApprovalDecision
    submission: dict
    status: Annotated[Status, last_wins]
    attention_reason: str
    # accumulators
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    usage: Annotated[dict, merge_usage]
    steps: Annotated[int, operator.add]


class ConversationState(TypedDict, total=False):
    thread_id: str
    job_id: str
    user_id: str
    persona: str
    messages: list[dict]                        # untrusted client text inside
    intent: MessageIntent
    evidence: list[dict]
    slots: list[dict]
    reply: ReplyDraft
    compliance: ComplianceVerdict
    approval: ApprovalDecision
    sent: dict
    status: Annotated[Status, last_wins]
    attention_reason: str
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    usage: Annotated[dict, merge_usage]
    steps: Annotated[int, operator.add]

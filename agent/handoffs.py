"""Structured hand-offs between agents (C3).

Anything a later node decides on travels as one of these Pydantic objects,
never as free text.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Score = float  # 0.0 – 1.0 everywhere


# ---------------------------------------------------------------- evidence
class EvidenceItem(BaseModel):
    id: str
    type: Literal["case_study", "repo", "code", "cv", "review", "proposal"]
    source: str = Field(description="file path, repo URL or proposal id")
    project: str | None = None
    date: str | None = None
    outcome: Literal["won", "replied", "ignored"] | None = None
    text: str
    score: float = 0.0


# ---------------------------------------------------------------- analysis
class ScreeningQuestion(BaseModel):
    id: str
    text: str


class JobSpec(BaseModel):
    """Job Analyst output."""
    job_id: str
    title: str
    deliverables: list[str]
    requirements: list[str] = Field(description="atomic, one skill/ability each")
    skills: list[str]
    budget_type: Literal["fixed", "hourly", "unknown"]
    budget_min: float | None = None
    budget_max: float | None = None
    timeline: str | None = None
    screening_questions: list[ScreeningQuestion] = []
    hidden_needs: list[str] = []
    category: str = "other"


class RiskFlag(BaseModel):
    kind: Literal[
        "scam", "free_work", "off_platform_contact", "off_platform_payment",
        "prompt_injection", "low_client_quality", "unrealistic_budget", "other",
    ]
    evidence: str = Field(description="quoted span from the post or client history")
    severity: Score


class RiskReport(BaseModel):
    job_id: str
    client_quality: Score
    risk_score: Score
    flags: list[RiskFlag] = []
    injection_detected: bool = False
    summary: str


class RequirementMatch(BaseModel):
    requirement: str
    evidence: list[EvidenceItem]
    supported: bool
    reason: str


class FitReport(BaseModel):
    job_id: str
    fit_score: Score
    matches: list[RequirementMatch]
    gaps: list[str] = []
    reasons: list[str] = []


# ---------------------------------------------------------------- decision
class BidDecision(BaseModel):
    job_id: str
    action: Literal["bid", "watch", "skip"]
    reasons: list[str]
    auto: bool = Field(False, description="True when decided by autonomy setting, not a human")


class PriceQuote(BaseModel):
    job_id: str
    rate_type: Literal["fixed", "hourly"]
    amount: float = Field(gt=0)
    market_p25: float | None = None
    market_p50: float | None = None
    market_p75: float | None = None
    user_floor: float
    connects_cost: int = Field(ge=0)
    connects_worth_it: bool
    rationale: str


# ---------------------------------------------------------------- writing
class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = []
    status: Literal["supported", "partial", "unsupported", "unchecked"] = "unchecked"
    note: str | None = None


class ScreeningAnswer(BaseModel):
    question_id: str
    question: str
    answer: str
    claims: list[Claim] = []


class ProposalDraft(BaseModel):
    job_id: str
    version: int = 1
    cover_letter: str
    claims: list[Claim] = []
    screening_answers: list[ScreeningAnswer] = []
    price: PriceQuote | None = None
    unsupported_before_verify: int = 0
    unsupported_after_verify: int = 0


class Critique(BaseModel):
    job_id: str
    draft_version: int
    approve: bool
    grounding_ok: bool
    personalisation_ok: bool
    length_ok: bool
    issues: list[str] = []
    revision_instructions: str | None = None


class ComplianceViolation(BaseModel):
    rule: str = Field(description="policy pack rule id")
    span: str
    action: Literal["removed", "rewritten", "blocked"]


class ComplianceVerdict(BaseModel):
    job_id: str
    allowed: bool
    violations: list[ComplianceViolation] = []
    cleaned_text: str
    block_reason: str | None = None


# ---------------------------------------------------------------- conversation graph
class MessageIntent(BaseModel):
    thread_id: str
    intent: Literal["question", "negotiation", "interview_request", "scam_signal", "other"]
    confidence: Score
    injection_detected: bool = False


class ReplyDraft(BaseModel):
    thread_id: str
    body: str
    proposed_slots: list[datetime] = []
    claims: list[Claim] = []


# ---------------------------------------------------------------- human approval
class ApprovalDecision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = None
    reason: str | None = None
    approval_id: str

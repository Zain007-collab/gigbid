"""Request/response models — the OpenAPI schema the TypeScript client is generated from."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Email = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)


class RegisterIn(BaseModel):
    email: str = Email
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(None, max_length=80)


class LoginIn(BaseModel):
    email: str = Email
    password: str


class SavedSearch(BaseModel):
    name: str = Field(max_length=60)
    query: str = Field("", max_length=200)
    skills: list[str] = []
    min_budget: float | None = None
    interval_min: int = Field(30, ge=15, le=1440)


class Settings(BaseModel):
    marketplace: Literal["gigsim", "upwork"] = "gigsim"
    hourly_rate: float | None = Field(None, ge=1, le=1000)
    min_project: float | None = Field(None, ge=1)
    weekly_connects_budget: int = Field(80, ge=0, le=1000)
    autonomy: Literal["suggest", "auto_skip_low_fit"] = "suggest"
    timezone: str = "Asia/Karachi"
    saved_searches: list[SavedSearch] = []


class OnboardingIn(BaseModel):
    persona: Literal["ai_ml", "fullstack", "data_analyst"]
    settings: Settings


class Me(BaseModel):
    id: str
    email: str
    display_name: str | None
    persona: str | None
    onboarded: bool
    settings: Settings


class InboxItem(BaseModel):
    job_id: str
    thread_id: str
    title: str | None
    category: str | None
    status: str
    action: str | None
    fit: float | None
    risk: float | None
    risk_flags: list[str] = []
    price: float | None
    rate_type: str | None
    connects: int | None
    budget_min: float | None
    budget_max: float | None
    posted_at: str | None
    summary: str | None
    updated_at: str


class JobDetail(BaseModel):
    job_id: str
    status: str
    job: dict[str, Any] | None
    job_spec: dict[str, Any] | None
    risk: dict[str, Any] | None
    fit: dict[str, Any] | None
    decision: dict[str, Any] | None
    price: dict[str, Any] | None
    drafts: list[dict[str, Any]] = []
    critiques: list[dict[str, Any]] = []
    compliance: dict[str, Any] | None
    submission: dict[str, Any] | None
    attention_reason: str | None
    pending_approval: dict[str, Any] | None
    usage: dict[str, Any] = {}


class ApprovalIn(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(None, max_length=6000)
    reason: str | None = Field(None, max_length=500)


class RegenerateIn(BaseModel):
    sentence_index: int = Field(ge=0)
    instruction: str | None = Field(None, max_length=300)


class RegenerateOut(BaseModel):
    text: str
    evidence_ids: list[str]


class Accepted(BaseModel):
    status: str
    detail: str | None = None


class ApprovalSummary(BaseModel):
    thread_id: str
    kind: Literal["proposal", "message"]
    job_id: str | None
    title: str | None
    created_at: str | None


class PipelineCard(BaseModel):
    job_id: str
    title: str | None
    column: Literal["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]
    proposal_id: str | None = None
    thread_id: str | None = None
    connects: int | None = None
    bid: float | None = None


class Conversation(BaseModel):
    thread_id: str
    job_id: str | None
    status: str
    intent: str | None
    last_message: str | None
    last_message_at: str | None


class MemoryItem(BaseModel):
    namespace: list[str]
    key: str
    data: dict[str, Any]


class Health(BaseModel):
    ok: bool
    marketplace_up: bool
    marketplace_mode: str
    llm_online: bool
    detail: str | None = None


class Analytics(BaseModel):
    by_category: list[dict[str, Any]]
    connects_spent: int
    proposals: int
    replies: int
    hires: int
    cost_per_proposal_usd: float | None
    p50_latency_s: float | None
    p95_latency_s: float | None
    p95_latency_job_s: float | None
    cost_per_job_usd: float | None
    top_rejection_reasons: list[dict[str, Any]]
    funnel: dict[str, int]

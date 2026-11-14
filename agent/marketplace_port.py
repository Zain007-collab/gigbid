"""The single marketplace interface (R1).

Two adapters implement it — GigSim (REST) and live Upwork (official MCP
server) — and config picks one. The Marketplace MCP server is the only code
that calls a port; agents only ever see MCP tools.
"""
from __future__ import annotations

import abc
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- domain models
class ClientSummary(BaseModel):
    client_id: str
    country: str | None = None
    payment_verified: bool | None = None
    total_spent: float | None = None
    hire_rate: float | None = None
    rating: float | None = None


class Job(BaseModel):
    job_id: str
    title: str
    description: str
    skills: list[str] = []
    category: str | None = None
    budget_type: Literal["fixed", "hourly", "unknown"] = "unknown"
    budget_min: float | None = None
    budget_max: float | None = None
    connects_required: int = 0
    posted_at: datetime | None = None
    client: ClientSummary
    screening_questions: list[str] = []
    url: str | None = None


class JobSummary(BaseModel):
    job_id: str
    title: str
    skills: list[str] = []
    budget_type: Literal["fixed", "hourly", "unknown"] = "unknown"
    budget_min: float | None = None
    budget_max: float | None = None
    connects_required: int = 0
    posted_at: datetime | None = None
    client_id: str


class PastContract(BaseModel):
    title: str
    amount: float | None = None
    feedback_given: float | None = None
    feedback_text: str | None = None


class ClientHistory(BaseModel):
    client: ClientSummary
    jobs_posted: int = 0
    hires: int = 0
    contracts: list[PastContract] = []
    member_since: datetime | None = None


class ConnectsBalance(BaseModel):
    available: int
    weekly_budget: int | None = None
    spent_this_week: int | None = None


class Message(BaseModel):
    message_id: str
    thread_id: str
    sender: Literal["client", "freelancer", "system"]
    body: str
    sent_at: datetime


class Percentiles(BaseModel):
    p25: float
    p50: float
    p75: float


class MarketStats(BaseModel):
    category: str
    fixed: Percentiles
    hourly: Percentiles
    source: str = "unknown"


class ProposalStatus(BaseModel):
    proposal_id: str
    job_id: str
    client_id: str | None = None
    thread_id: str | None = None
    status: Literal["submitted", "viewed", "replied", "interview", "hired", "lost",
                    "pending_user_confirmation"]
    connects_spent: int = 0
    bid_amount: float | None = None
    submitted_at: datetime | None = None


class JobQuery(BaseModel):
    query: str = ""
    skills: list[str] = []
    min_budget: float | None = None
    posted_after: datetime | None = None
    limit: int = Field(20, ge=1, le=50)


class ProposalPayload(BaseModel):
    job_id: str
    cover_letter: str
    rate_type: Literal["fixed", "hourly"]
    bid_amount: float
    screening_answers: list[dict[str, str]] = []


class DraftReceipt(BaseModel):
    draft_id: str
    job_id: str


class SubmitReceipt(BaseModel):
    proposal_id: str
    job_id: str
    connects_spent: int
    status: Literal["submitted", "pending_user_confirmation"]


class MessageReceipt(BaseModel):
    message_id: str
    thread_id: str
    status: Literal["sent", "pending_user_confirmation"]


# ---------------------------------------------------------------- errors
class MarketplaceError(Exception):
    """Base error; message is safe to show the user."""


class MarketplaceUnavailable(MarketplaceError):
    """Upstream down or timing out after retries -> degraded mode."""


class RateLimited(MarketplaceError):
    def __init__(self, retry_after_s: float, msg: str = "rate limited"):
        super().__init__(msg)
        self.retry_after_s = retry_after_s


class NotFound(MarketplaceError):
    pass


# ---------------------------------------------------------------- the port
class MarketplacePort(abc.ABC):
    name: str

    # reads
    @abc.abstractmethod
    async def search_jobs(self, user_id: str, q: JobQuery) -> list[JobSummary]: ...

    @abc.abstractmethod
    async def get_job(self, user_id: str, job_id: str) -> Job: ...

    @abc.abstractmethod
    async def get_client_history(self, user_id: str, client_id: str) -> ClientHistory: ...

    @abc.abstractmethod
    async def get_connects_balance(self, user_id: str) -> ConnectsBalance: ...

    @abc.abstractmethod
    async def list_messages(
        self, user_id: str, thread_id: str | None = None, since: datetime | None = None
    ) -> list[Message]: ...

    @abc.abstractmethod
    async def get_market_stats(self, user_id: str, category: str) -> MarketStats: ...

    @abc.abstractmethod
    async def list_proposals(self, user_id: str) -> list[ProposalStatus]: ...

    # writes — every write carries an idempotency key the adapter forwards upstream
    @abc.abstractmethod
    async def create_proposal_draft(
        self, user_id: str, payload: ProposalPayload, idempotency_key: str
    ) -> DraftReceipt: ...

    @abc.abstractmethod
    async def submit_proposal(
        self, user_id: str, draft_id: str, idempotency_key: str
    ) -> SubmitReceipt: ...

    @abc.abstractmethod
    async def send_message(
        self, user_id: str, thread_id: str, body: str, idempotency_key: str
    ) -> MessageReceipt: ...

    async def reconcile(self, user_id: str, tool: str, idempotency_key: str) -> dict[str, Any] | None:
        """After a crash mid-write: did this key already land upstream?

        Return the receipt as a dict if yes, None if it definitely did not.
        Adapters that cannot tell should raise MarketplaceError so the caller
        refuses to retry blindly.
        """
        raise MarketplaceError("adapter cannot reconcile; refusing blind retry")

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

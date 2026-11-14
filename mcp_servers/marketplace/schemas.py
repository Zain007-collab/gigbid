"""Tool input constraints and output schemas for the Marketplace MCP server."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from agent.marketplace_port import ClientHistory, ConnectsBalance, Job, JobSummary, Message

JobId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
                             description="Marketplace job id, e.g. 'j_123'")]
ClientId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
ThreadId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
DraftId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
IdempotencyKey = Annotated[str, Field(
    min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$",
    description="Stable per logical action (e.g. '<thread_id>:<node>:<n>'). Reusing it never repeats the write.",
)]
ApprovalId = Annotated[str | None, Field(
    default=None, max_length=64,
    description="Id of the human approval in GigBid. Required when dry_run is false.",
)]
DryRun = Annotated[bool, Field(
    default=True, description="True (default): return exactly what would be sent and what it costs; send nothing.",
)]


class ScreeningAnswerIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=3000)


# ---------------------------------------------------------------- outputs
class JobSearchResult(BaseModel):
    jobs: list[JobSummary]


class JobResult(BaseModel):
    job: Job


class ClientHistoryResult(BaseModel):
    history: ClientHistory


class ConnectsResult(BaseModel):
    balance: ConnectsBalance


class MessagesResult(BaseModel):
    messages: list[Message]


class Preview(BaseModel):
    action: Literal["create_proposal_draft", "submit_proposal", "send_message"]
    payload: dict[str, Any] = Field(description="exactly what will be sent upstream")
    connects_cost: int = Field(ge=0)
    connects_balance: int | None = None
    balance_after: int | None = None
    sufficient_connects: bool = True
    marketplace: str


class WriteResult(BaseModel):
    status: Literal["dry_run", "done", "replayed"]
    action: Literal["create_proposal_draft", "submit_proposal", "send_message"]
    preview: Preview | None = None
    receipt: dict[str, Any] | None = None
    idempotency_key: str | None = None
    trace_id: str

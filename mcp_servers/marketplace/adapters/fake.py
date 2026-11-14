"""In-memory marketplace for unit tests and offline development.

Also lets tests simulate an outage (`down = True`) and a crash between the
upstream write and our idempotency bookkeeping.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any

from agent.marketplace_port import (
    ClientHistory, ClientSummary, ConnectsBalance, DraftReceipt, Job, JobQuery, JobSummary,
    MarketStats, Percentiles, ProposalStatus, MarketplaceError, MarketplacePort, MarketplaceUnavailable, Message, MessageReceipt, NotFound,
    ProposalPayload, SubmitReceipt,
)


class FakeMarketplace(MarketplacePort):
    name = "fake"

    def __init__(self, connects: int = 100):
        self.down = False
        self.connects = connects
        self._ids = itertools.count(1)
        self.jobs: dict[str, Job] = {}
        self.drafts: dict[str, ProposalPayload] = {}
        self.submitted: dict[str, SubmitReceipt] = {}     # proposal_id -> receipt
        self.by_key: dict[str, dict[str, Any]] = {}       # idempotency key -> receipt
        self.sent: list[MessageReceipt] = []
        self.messages: list[Message] = []
        self.submit_calls = 0
        self._seed()

    def _seed(self) -> None:
        client = ClientSummary(client_id="c1", country="US", payment_verified=True,
                               total_spent=12000, hire_rate=0.7, rating=4.8)
        self.jobs["j1"] = Job(
            job_id="j1", title="RAG chatbot over internal PDFs",
            description="Need a LangChain + Pinecone RAG bot with evals.",
            skills=["python", "langchain", "pinecone"], budget_type="fixed",
            budget_min=800, budget_max=1500, connects_required=12, client=client,
            screening_questions=["Describe a similar project."],
        )
        self.messages.append(Message(message_id="m1", thread_id="t1", sender="client",
                                     body="Can you start Monday?", sent_at=datetime.now(timezone.utc)))

    def _check(self) -> None:
        if self.down:
            raise MarketplaceUnavailable("marketplace is down")

    async def search_jobs(self, user_id: str, q: JobQuery) -> list[JobSummary]:
        self._check()
        return [JobSummary(client_id=j.client.client_id, **j.model_dump(include=set(JobSummary.model_fields) - {"client_id"}))
                for j in self.jobs.values()][: q.limit]

    async def get_job(self, user_id: str, job_id: str) -> Job:
        self._check()
        if job_id not in self.jobs:
            raise NotFound(job_id)
        return self.jobs[job_id]

    async def get_client_history(self, user_id: str, client_id: str) -> ClientHistory:
        self._check()
        for j in self.jobs.values():
            if j.client.client_id == client_id:
                return ClientHistory(client=j.client, jobs_posted=5, hires=3)
        raise NotFound(client_id)

    async def get_connects_balance(self, user_id: str) -> ConnectsBalance:
        self._check()
        return ConnectsBalance(available=self.connects)

    async def list_messages(self, user_id: str, thread_id: str | None = None, since: datetime | None = None) -> list[Message]:
        self._check()
        return [m for m in self.messages if thread_id is None or m.thread_id == thread_id]

    async def get_market_stats(self, user_id: str, category: str) -> MarketStats:
        self._check()
        return MarketStats(category=category, fixed=Percentiles(p25=500, p50=900, p75=1400),
                           hourly=Percentiles(p25=25, p50=40, p75=60), source="fake")

    async def list_proposals(self, user_id: str) -> list[ProposalStatus]:
        self._check()
        return [ProposalStatus(proposal_id=r.proposal_id, job_id=r.job_id, status="submitted",
                               connects_spent=r.connects_spent) for r in self.submitted.values()]

    async def create_proposal_draft(self, user_id: str, payload: ProposalPayload, idempotency_key: str) -> DraftReceipt:
        self._check()
        if idempotency_key in self.by_key:
            return DraftReceipt(**self.by_key[idempotency_key])
        draft_id = f"d{next(self._ids)}"
        self.drafts[draft_id] = payload
        r = DraftReceipt(draft_id=draft_id, job_id=payload.job_id)
        self.by_key[idempotency_key] = r.model_dump()
        return r

    async def submit_proposal(self, user_id: str, draft_id: str, idempotency_key: str) -> SubmitReceipt:
        self._check()
        if idempotency_key in self.by_key:
            return SubmitReceipt(**self.by_key[idempotency_key])
        if draft_id not in self.drafts:
            raise NotFound(draft_id)
        job = self.jobs[self.drafts[draft_id].job_id]
        if self.connects < job.connects_required:
            raise MarketplaceError("not enough Connects")
        self.submit_calls += 1
        self.connects -= job.connects_required
        r = SubmitReceipt(proposal_id=f"p{next(self._ids)}", job_id=job.job_id,
                          connects_spent=job.connects_required, status="submitted")
        self.submitted[r.proposal_id] = r
        self.by_key[idempotency_key] = r.model_dump()
        return r

    async def send_message(self, user_id: str, thread_id: str, body: str, idempotency_key: str) -> MessageReceipt:
        self._check()
        if idempotency_key in self.by_key:
            return MessageReceipt(**self.by_key[idempotency_key])
        r = MessageReceipt(message_id=f"m{next(self._ids)}", thread_id=thread_id, status="sent")
        self.sent.append(r)
        self.by_key[idempotency_key] = r.model_dump()
        return r

    async def reconcile(self, user_id: str, tool: str, idempotency_key: str) -> dict[str, Any] | None:
        return self.by_key.get(idempotency_key)

    async def health(self) -> bool:
        return not self.down

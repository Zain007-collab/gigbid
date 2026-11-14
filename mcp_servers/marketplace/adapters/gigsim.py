"""GigSim REST adapter.

Targets the GigSim REST API in gigsim/app.py. Nothing outside this file
depends on GigSim's wire format (endpoints in ENDPOINTS, mapping in _to_*).

Handles GigSim's rate limits (429 + Retry-After) and occasional 5xx errors
with bounded exponential backoff and jitter.
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime
from typing import Any

import httpx

from agent.marketplace_port import (
    ClientHistory, ClientSummary, ConnectsBalance, DraftReceipt, Job, JobQuery, JobSummary,
    MarketStats, ProposalStatus, MarketplaceError, MarketplacePort, MarketplaceUnavailable, Message, MessageReceipt, NotFound,
    PastContract, ProposalPayload, RateLimited, SubmitReceipt,
)

ENDPOINTS = {
    "search_jobs": ("GET", "/jobs"),
    "get_job": ("GET", "/jobs/{job_id}"),
    "get_client": ("GET", "/clients/{client_id}"),
    "get_connects": ("GET", "/freelancers/{user_id}/connects"),
    "list_messages": ("GET", "/freelancers/{user_id}/messages"),
    "create_draft": ("POST", "/freelancers/{user_id}/proposals/drafts"),
    "submit_proposal": ("POST", "/freelancers/{user_id}/proposals/drafts/{draft_id}/submit"),
    "send_message": ("POST", "/freelancers/{user_id}/threads/{thread_id}/messages"),
    "market_stats": ("GET", "/market/stats/{category}"),
    "list_proposals": ("GET", "/freelancers/{user_id}/proposals"),
    "lookup_idempotency": ("GET", "/freelancers/{user_id}/idempotency/{key}"),
    "health": ("GET", "/health"),
}


class GigSimAdapter(MarketplacePort):
    name = "gigsim"

    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url=os.environ.get(cfg["base_url_env"], "http://localhost:8080"),
            timeout=cfg["timeout_s"],
            headers={"Authorization": f"Bearer {os.environ.get(cfg['api_key_env'], '')}"},
        )

    # ------------------------------------------------------------------ http
    async def _call(
        self,
        op: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        method, tmpl = ENDPOINTS[op]
        url = tmpl.format(**(path_params or {}))
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        retries, base, cap = self._cfg["max_retries"], self._cfg["backoff_base_s"], self._cfg["backoff_max_s"]

        for attempt in range(retries + 1):
            try:
                r = await self._client.request(method, url, params=params, json=json, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt == retries:
                    raise MarketplaceUnavailable(f"GigSim unreachable: {type(e).__name__}") from e
                await asyncio.sleep(_backoff(attempt, base, cap))
                continue

            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", _backoff(attempt, base, cap)))
                if attempt == retries:
                    raise RateLimited(wait)
                await asyncio.sleep(min(wait, cap))
                continue
            if r.status_code >= 500:
                if attempt == retries:
                    raise MarketplaceUnavailable(f"GigSim error {r.status_code}")
                await asyncio.sleep(_backoff(attempt, base, cap))
                continue
            if r.status_code == 404:
                raise NotFound(f"{op}: not found")
            if r.status_code >= 400:
                raise MarketplaceError(f"{op}: GigSim rejected request ({r.status_code}): {r.text[:200]}")
            return r.json() if r.content else {}
        raise MarketplaceUnavailable("unreachable")  # pragma: no cover

    # ----------------------------------------------------------------- reads
    async def search_jobs(self, user_id: str, q: JobQuery) -> list[JobSummary]:
        params = {"q": q.query, "skills": ",".join(q.skills), "limit": q.limit}
        if q.min_budget is not None:
            params["min_budget"] = q.min_budget
        if q.posted_after is not None:
            params["posted_after"] = q.posted_after.isoformat()
        data = await self._call("search_jobs", params=params)
        return [_to_job_summary(j) for j in _items(data)]

    async def get_job(self, user_id: str, job_id: str) -> Job:
        return _to_job(await self._call("get_job", path_params={"job_id": job_id}))

    async def get_client_history(self, user_id: str, client_id: str) -> ClientHistory:
        d = await self._call("get_client", path_params={"client_id": client_id})
        return ClientHistory(
            client=_to_client(d),
            jobs_posted=d.get("jobs_posted", 0),
            hires=d.get("hires", 0),
            contracts=[PastContract(**_pick(c, PastContract)) for c in d.get("contracts", [])],
            member_since=d.get("member_since"),
        )

    async def get_connects_balance(self, user_id: str) -> ConnectsBalance:
        d = await self._call("get_connects", path_params={"user_id": user_id})
        return ConnectsBalance(available=d.get("available", d.get("balance", 0)))

    async def list_messages(
        self, user_id: str, thread_id: str | None = None, since: datetime | None = None
    ) -> list[Message]:
        params: dict[str, Any] = {}
        if thread_id:
            params["thread_id"] = thread_id
        if since:
            params["since"] = since.isoformat()
        data = await self._call("list_messages", path_params={"user_id": user_id}, params=params)
        return [Message(**_pick(m, Message)) for m in _items(data)]

    async def get_market_stats(self, user_id: str, category: str) -> MarketStats:
        d = await self._call("market_stats", path_params={"category": category})
        return MarketStats(category=category, fixed=d["fixed"], hourly=d["hourly"], source=d.get("source", "gigsim"))

    async def list_proposals(self, user_id: str) -> list[ProposalStatus]:
        data = await self._call("list_proposals", path_params={"user_id": user_id})
        return [ProposalStatus(**_pick(p, ProposalStatus)) for p in _items(data)]

    # ---------------------------------------------------------------- writes
    async def create_proposal_draft(
        self, user_id: str, payload: ProposalPayload, idempotency_key: str
    ) -> DraftReceipt:
        d = await self._call(
            "create_draft", path_params={"user_id": user_id},
            json=payload.model_dump(), idempotency_key=idempotency_key,
        )
        return DraftReceipt(draft_id=str(d.get("draft_id") or d["id"]), job_id=payload.job_id)

    async def submit_proposal(self, user_id: str, draft_id: str, idempotency_key: str) -> SubmitReceipt:
        d = await self._call(
            "submit_proposal", path_params={"user_id": user_id, "draft_id": draft_id},
            idempotency_key=idempotency_key,
        )
        return SubmitReceipt(
            proposal_id=str(d.get("proposal_id") or d["id"]),
            job_id=d.get("job_id", ""),
            connects_spent=d.get("connects_spent", 0),
            status="submitted",
        )

    async def send_message(
        self, user_id: str, thread_id: str, body: str, idempotency_key: str
    ) -> MessageReceipt:
        d = await self._call(
            "send_message", path_params={"user_id": user_id, "thread_id": thread_id},
            json={"body": body}, idempotency_key=idempotency_key,
        )
        return MessageReceipt(message_id=str(d.get("message_id") or d["id"]), thread_id=thread_id, status="sent")

    async def reconcile(self, user_id: str, tool: str, idempotency_key: str) -> dict[str, Any] | None:
        try:
            return await self._call(
                "lookup_idempotency", path_params={"user_id": user_id, "key": idempotency_key}
            )
        except NotFound:
            return None

    async def health(self) -> bool:
        try:
            await self._call("health")
            return True
        except MarketplaceError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------- mapping helpers
def _backoff(attempt: int, base: float, cap: float) -> float:
    return min(cap, base * 2**attempt) * (0.5 + random.random() / 2)


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    return data.get("items") or data.get("results") or data.get("jobs") or data.get("messages") or []


def _pick(d: dict[str, Any], model: type) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k in model.model_fields}


def _to_client(d: dict[str, Any]) -> ClientSummary:
    c = d.get("client", d)
    return ClientSummary(client_id=str(c.get("client_id") or c.get("id")), **{
        k: v for k, v in _pick(c, ClientSummary).items() if k != "client_id"
    })


def _to_job(d: dict[str, Any]) -> Job:
    base = _pick(d, Job)
    base["job_id"] = str(d.get("job_id") or d.get("id"))
    base["client"] = _to_client(d)
    base["screening_questions"] = [
        q if isinstance(q, str) else q.get("text", "") for q in d.get("screening_questions", [])
    ]
    return Job(**base)


def _to_job_summary(d: dict[str, Any]) -> JobSummary:
    base = _pick(d, JobSummary)
    base["job_id"] = str(d.get("job_id") or d.get("id"))
    base["client_id"] = str(d.get("client_id") or (d.get("client") or {}).get("id", ""))
    return JobSummary(**base)

"""Live Upwork adapter: official Upwork MCP server over Streamable HTTP (A4).

Rules this adapter enforces:
  * Talks ONLY to the official MCP server (no scraping, no browser automation,
    no session cookies).
  * Uses the user's OAuth 2.1 access token, decrypted per call from the
    encrypted token store (apps/api/auth). Tokens are never logged.
  * Write operations create platform drafts; the user confirms them on
    Upwork, so receipts come back as `pending_user_confirmation`.

Upwork's tool names are mapped in config.yaml (marketplace.upwork.tool_map)
so a renamed upstream tool is a config change, not a code change.
"""
from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from agent.marketplace_port import (
    ClientHistory, ConnectsBalance, DraftReceipt, Job, JobQuery, JobSummary, MarketplaceError, MarketStats,
    ProposalStatus,
    MarketplacePort, MarketplaceUnavailable, Message, MessageReceipt, ProposalPayload, SubmitReceipt,
)

TokenProvider = Callable[[str], Awaitable[str]]  # user_id -> OAuth access token


class UpworkLiveAdapter(MarketplacePort):
    name = "upwork"

    def __init__(self, cfg: dict[str, Any], token_provider: TokenProvider):
        self._cfg = cfg
        self._url = os.environ.get(cfg["url_env"], "https://mcp.upwork.com/mcp")
        self._tokens = token_provider
        self._tool_map: dict[str, str] = cfg.get("tool_map", {})

    async def _call(self, user_id: str, op: str, args: dict[str, Any]) -> Any:
        tool_name = self._tool_map.get(op)
        if not tool_name:
            raise MarketplaceError(f"live mode: no Upwork tool mapped for {op!r} (config marketplace.upwork.tool_map)")
        from langchain_mcp_adapters.client import MultiServerMCPClient  # lazy: heavy import

        token = await self._tokens(user_id)
        client = MultiServerMCPClient({
            "upwork": {
                "transport": "streamable_http",
                "url": self._url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        })
        try:
            async with client.session("upwork") as session:
                result = await session.call_tool(tool_name, args)
        except Exception as e:  # network / auth / server error
            raise MarketplaceUnavailable(f"Upwork MCP unavailable: {type(e).__name__}") from e
        if result.isError:
            raise MarketplaceError(f"Upwork tool {tool_name} failed")
        if result.structuredContent is not None:
            return result.structuredContent
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    # Field mapping is finalised once the live tools/list output is inspected;
    # until then each method passes the upstream payload through our domain models.
    async def search_jobs(self, user_id: str, q: JobQuery) -> list[JobSummary]:
        data = await self._call(user_id, "search_jobs", {"query": q.query, "limit": q.limit})
        return [JobSummary.model_validate(j) for j in data.get("jobs", data if isinstance(data, list) else [])]

    async def get_job(self, user_id: str, job_id: str) -> Job:
        return Job.model_validate(await self._call(user_id, "get_job", {"job_id": job_id}))

    async def get_client_history(self, user_id: str, client_id: str) -> ClientHistory:
        return ClientHistory.model_validate(await self._call(user_id, "get_client_history", {"client_id": client_id}))

    async def get_connects_balance(self, user_id: str) -> ConnectsBalance:
        return ConnectsBalance.model_validate(await self._call(user_id, "get_connects_balance", {}))

    async def list_messages(self, user_id: str, thread_id: str | None = None, since: datetime | None = None) -> list[Message]:
        args: dict[str, Any] = {"thread_id": thread_id} if thread_id else {}
        data = await self._call(user_id, "list_messages", args)
        return [Message.model_validate(m) for m in data.get("messages", [])]

    async def get_market_stats(self, user_id: str, category: str) -> MarketStats:
        return MarketStats.model_validate({**await self._call(user_id, "get_market_stats", {"category": category}),
                                           "category": category})

    async def list_proposals(self, user_id: str) -> list[ProposalStatus]:
        data = await self._call(user_id, "list_proposals", {})
        return [ProposalStatus.model_validate(p) for p in data.get("proposals", [])]

    async def create_proposal_draft(self, user_id: str, payload: ProposalPayload, idempotency_key: str) -> DraftReceipt:
        d = await self._call(user_id, "create_proposal_draft", payload.model_dump())
        return DraftReceipt(draft_id=str(d.get("draft_id") or d.get("id")), job_id=payload.job_id)

    async def submit_proposal(self, user_id: str, draft_id: str, idempotency_key: str) -> SubmitReceipt:
        d = await self._call(user_id, "submit_proposal", {"draft_id": draft_id})
        return SubmitReceipt(proposal_id=str(d.get("proposal_id") or d.get("id") or draft_id),
                             job_id=d.get("job_id", ""), connects_spent=d.get("connects_spent", 0),
                             status="pending_user_confirmation")

    async def send_message(self, user_id: str, thread_id: str, body: str, idempotency_key: str) -> MessageReceipt:
        d = await self._call(user_id, "send_message", {"thread_id": thread_id, "body": body})
        return MessageReceipt(message_id=str(d.get("message_id") or d.get("id")), thread_id=thread_id,
                              status="pending_user_confirmation")

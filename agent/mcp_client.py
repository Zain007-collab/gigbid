"""MCP client for the agents (A6): per-agent tool allowlists, timeouts, bounded retries, degraded mode.

Two transports, same protocol:
  in-memory  (GIGBID_MCP_TRANSPORT=memory, default for tests/evals) — connects to the
             FastMCP server objects in-process via MCP's memory transport
  http       (deployment) — MultiServerMCPClient over Streamable HTTP with bearer tokens

Agents never import a server module or call GigSim directly; they call `call()`.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
from contextvars import ContextVar
from typing import Any

# Which agent may call which tool (filtered by name).
ALLOWLIST: dict[str, set[str]] = {
    "scout": {"marketplace.search_jobs", "marketplace.get_job"},
    "intake": {"marketplace.get_job"},
    "risk_assessor": {"marketplace.get_client_history"},
    "fit_matcher": {"portfolio.search_evidence", "portfolio.list_skills"},
    "pricing": {"marketplace.get_connects_balance", "marketplace.get_market_stats", "marketplace.list_proposals"},
    "writer": {"portfolio.search_evidence", "portfolio.get_case_study"},
    "verifier": {"portfolio.search_evidence", "portfolio.get_case_study"},
    "critic": {"portfolio.search_evidence"},
    "compliance": set(),
    "approval": {"marketplace.create_proposal_draft", "marketplace.send_message"},   # dry_run previews only
    "submit": {"marketplace.create_proposal_draft", "marketplace.submit_proposal"},
    "liaison": {"marketplace.list_messages", "portfolio.search_evidence", "workspace.free_slots"},
    "send": {"marketplace.send_message", "workspace.create_interview_hold"},
    "pipeline": {"marketplace.list_proposals"},
}

TIMEOUT_S = {"marketplace": 20, "portfolio": 30, "workspace": 10}
RETRIES = 3

# request-scoped identity, set by the API/worker before invoking a graph
current_user: ContextVar[str] = ContextVar("current_user", default="local")
current_persona: ContextVar[str] = ContextVar("current_persona", default="ai_ml")
current_trace: ContextVar[str | None] = ContextVar("current_trace", default=None)


class ToolNotAllowed(PermissionError):
    pass


class MarketplaceDown(RuntimeError):
    """Raised when the marketplace server is unreachable or reports MARKETPLACE_UNAVAILABLE."""


class ToolFailed(RuntimeError):
    pass


def _server_modules() -> dict[str, Any]:
    from mcp_servers.marketplace import server as m
    from mcp_servers.portfolio import server as p
    from mcp_servers.workspace import server as w
    return {"marketplace": m.mcp, "portfolio": p.mcp, "workspace": w.mcp}


async def _call_memory(server: str, tool: str, args: dict) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    srv = _server_modules()[server]
    async with create_connected_server_and_client_session(srv._mcp_server) as session:
        return await session.call_tool(tool, args)


async def _call_http(server: str, tool: str, args: dict) -> Any:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    env = server.upper()
    headers = {"Authorization": f"Bearer {os.environ.get(f'{env}_SERVICE_TOKEN', '')}",
               "X-GigBid-User": current_user.get(), "X-GigBid-Persona": current_persona.get()}
    if (t := current_trace.get()):
        headers["X-Trace-Id"] = t
    client = MultiServerMCPClient({server: {"transport": "streamable_http",
                                            "url": os.environ[f"{env}_MCP_URL"], "headers": headers}})
    async with client.session(server) as session:
        return await session.call_tool(tool, args)


async def marketplace_healthy(timeout_s: float = 3.0) -> bool:
    """Degraded-mode probe for the UI banner: one fast check, no retry ladder."""
    try:
        if os.environ.get("GIGBID_MCP_TRANSPORT", "memory") == "memory":
            from mcp_servers.marketplace.server import runtime
            return await asyncio.wait_for(runtime().adapter.health(), timeout_s)
        import httpx
        base = os.environ["MARKETPLACE_MCP_URL"].rsplit("/mcp", 1)[0]
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.get(f"{base}/healthz")
            return r.status_code == 200 and bool(r.json().get("marketplace_up", False))
    except Exception:  # noqa: BLE001 - any failure means "not healthy"
        return False


async def call(agent: str, qualified_tool: str, args: dict | None = None) -> dict:
    """Call `server.tool` on behalf of `agent`. Returns structured content as a dict."""
    if qualified_tool not in ALLOWLIST.get(agent, set()):
        raise ToolNotAllowed(f"{agent} may not call {qualified_tool}")
    server, tool = qualified_tool.split(".", 1)
    transport = os.environ.get("GIGBID_MCP_TRANSPORT", "memory")
    fn = _call_memory if transport == "memory" else _call_http
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            result = await asyncio.wait_for(fn(server, tool, args or {}), TIMEOUT_S[server])
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            last = e
        except Exception as e:  # httpx/anyio connection errors surface as various types
            if "connect" in type(e).__name__.lower() or "connect" in str(e).lower():
                last = e
            else:
                raise
        else:
            if result.isError:
                text = " ".join(getattr(c, "text", "") for c in result.content)
                # FastMCP prefixes "Error executing tool X: " — match the code anywhere
                if any(code in text for code in ("MARKETPLACE_UNAVAILABLE", "RATE_LIMITED")):
                    last = MarketplaceDown(text) if "MARKETPLACE_UNAVAILABLE" in text else ToolFailed(text)
                    if attempt < RETRIES:
                        # polite: honour the server's retry-after hint when rate limited
                        m = re.search(r"retry[ _]after[= ]?([\d.]+)", text)
                        wait = float(m.group(1)) + 0.1 if m else 0.5 * 2 ** attempt
                        await asyncio.sleep(min(8.0, wait) * (1 + 0.25 * random.random()))
                        continue
                    break
                raise ToolFailed(text)
            if result.structuredContent is not None:
                return result.structuredContent
            text = "".join(getattr(c, "text", "") for c in result.content)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
        if attempt < RETRIES:
            await asyncio.sleep(min(4.0, 0.5 * 2 ** attempt) * (0.5 + random.random()))
    if server == "marketplace":
        raise MarketplaceDown(str(last))
    raise ToolFailed(f"{qualified_tool} failed: {last}")

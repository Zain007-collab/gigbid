"""Shared node runtime: budgets, retries, degraded mode, dead-letter, execution trace (C4, C7).

Every graph node is wrapped by `@node("name")`:
  * before running: step and token budgets are checked -> status "needs_attention"
  * transient failures (tool errors, timeouts) are retried with backoff (bounded)
  * MarketplaceDown and LangGraph interrupts propagate: the checkpoint keeps the node pending,
    so the worker resumes exactly that node once the marketplace is back (degraded mode)
  * anything else after retries -> status "dead_letter" with the error
  * tool calls made inside the node and LLM usage are appended to the trace
"""
from __future__ import annotations

import asyncio
import functools
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from agent import mcp_client
from agent.settings import section
from shared.pii import mask_obj

trace_sink: ContextVar[list | None] = ContextVar("trace_sink", default=None)
usage_sink: ContextVar[list | None] = ContextVar("usage_sink", default=None)

NODE_RETRIES = 2


def record_usage(u: dict) -> None:
    sink = usage_sink.get()
    if sink is not None:
        sink.append(u)


async def tool(agent: str, qualified: str, args: dict | None = None) -> dict:
    """MCP call that is also written to the node's execution trace (args PII-masked)."""
    t0 = time.perf_counter()
    ok, summary = True, None
    try:
        res = await mcp_client.call(agent, qualified, args)
        summary = {k: (f"<{len(v)} items>" if isinstance(v, list) else "…") for k, v in (res or {}).items()}
        return res
    except Exception as e:
        ok, summary = False, f"{type(e).__name__}: {str(e)[:200]}"
        raise
    finally:
        sink = trace_sink.get()
        if sink is not None:
            sink.append({"tool": qualified, "args": mask_obj(args or {}), "ok": ok, "result": summary,
                         "ms": round((time.perf_counter() - t0) * 1000)})


def node(name: str) -> Callable[[Callable[..., Awaitable[dict]]], Callable[..., Awaitable[dict]]]:
    def deco(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *a: Any, **kw: Any) -> dict:
            budgets = section("budgets")
            steps = state.get("steps", 0)
            tokens = sum(u.get("tokens_in", 0) + u.get("tokens_out", 0) for u in (state.get("usage") or {}).values())
            if steps >= budgets["max_steps_per_thread"]:
                return {"status": "needs_attention", "attention_reason": f"step budget ({steps}) exceeded at {name}"}
            if tokens >= budgets["max_tokens_per_thread"]:
                return {"status": "needs_attention", "attention_reason": f"token budget ({tokens}) exceeded at {name}"}

            calls: list = []
            usages: list = []
            t_tok, u_tok = trace_sink.set(calls), usage_sink.set(usages)
            t0 = time.perf_counter()
            out: dict = {}
            err: str | None = None
            try:
                for attempt in range(NODE_RETRIES + 1):
                    try:
                        out = await fn(state, *a, **kw) or {}
                        err = None
                        break
                    except (GraphBubbleUp, mcp_client.MarketplaceDown):
                        raise  # interrupt() / outage: let LangGraph checkpoint and pause
                    except (mcp_client.ToolFailed, asyncio.TimeoutError, ConnectionError) as e:
                        err = f"{type(e).__name__}: {e}"
                        if attempt < NODE_RETRIES:
                            await asyncio.sleep(0.5 * 2 ** attempt)
                            continue
                        out = {"status": "dead_letter", "attention_reason": f"{name} failed: {err[:300]}"}
                    except Exception as e:  # bug or bad data: no point retrying
                        err = f"{type(e).__name__}: {e}"
                        out = {"status": "dead_letter", "attention_reason": f"{name} failed: {err[:300]}"}
                        break
            finally:
                trace_sink.reset(t_tok)
                usage_sink.reset(u_tok)

            usage: dict = {}
            for u in usages:
                cur = usage.setdefault(name, {"tokens_in": 0, "tokens_out": 0, "usd": 0.0, "latency_s": 0.0, "calls": 0})
                cur["tokens_in"] += u.get("tokens_in", 0)
                cur["tokens_out"] += u.get("tokens_out", 0)
                cur["usd"] += u.get("usd", 0.0)
                cur["latency_s"] += u.get("latency_s", 0.0)
                cur["calls"] += 1
                if u.get("model"):
                    cur["model"] = u["model"]
            cmd = out if isinstance(out, Command) else None
            upd = dict(cmd.update or {}) if cmd else dict(out)
            entry = {"node": name, "ms": round((time.perf_counter() - t0) * 1000), "tools": calls,
                     "llm": [{k: u.get(k) for k in ("model", "tokens_in", "tokens_out", "usd", "error") if u.get(k)}
                             for u in usages], "status": upd.get("status")}
            if err:
                entry["error"] = err[:300]
            upd["trace"] = [entry]
            upd["steps"] = 1
            if usage:
                upd["usage"] = usage
            if err:
                upd["errors"] = [f"{name}: {err[:300]}"]
            return Command(goto=cmd.goto, update=upd) if cmd else upd
        return wrapper
    return deco

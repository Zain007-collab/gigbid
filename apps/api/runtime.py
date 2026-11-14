"""Graph runtime shared by the API and the worker: checkpointer, store, graphs, event bus, run execution.

Checkpointer/store: Postgres (DATABASE_URL=postgresql://...) in production, SQLite locally.
Event bus: Redis pub/sub when REDIS_URL is set (API and worker in different processes), else in-process.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from contextlib import AsyncExitStack
from typing import Any

from langgraph.types import Command

from agent import mcp_client
from agent.graphs.bid_graph import build_bid_graph, thread_id as bid_tid
from agent.graphs.conversation_graph import build_conversation_graph, conv_thread_id
from agent.state import HALT
from apps.api import db
from shared.pii import mask_obj

_stack = AsyncExitStack()
BID = None
CONV = None
STORE = None


async def startup() -> None:
    global BID, CONV, STORE
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
        saver = await _stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
        await saver.setup()
        STORE = await _stack.enter_async_context(AsyncPostgresStore.from_conn_string(url))
        await STORE.setup()
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.memory import InMemoryStore
        path = os.environ.get("CHECKPOINT_DB", "data/processed/checkpoints.sqlite3")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        saver = await _stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
        STORE = InMemoryStore()
    BID = build_bid_graph(saver, STORE)
    CONV = build_conversation_graph(saver, STORE)


async def shutdown() -> None:
    await _stack.aclose()


# ---------------------------------------------------------------- event bus
class Bus:
    def __init__(self) -> None:
        self.local: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self.redis = None
        if os.environ.get("REDIS_URL"):
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(os.environ["REDIS_URL"])

    async def publish(self, channel: str, event: dict) -> None:
        data = json.dumps(event, default=str)
        if self.redis is not None:
            await self.redis.publish(f"gigbid:{channel}", data)
            await self.redis.rpush(f"gigbid:log:{channel}", data)
            await self.redis.ltrim(f"gigbid:log:{channel}", -300, -1)
            await self.redis.expire(f"gigbid:log:{channel}", 86400)
        else:
            LOG[channel].append(data)
            del LOG[channel][:-300]
        for q in self.local.get(channel, []):
            q.put_nowait(data)

    async def history(self, channel: str) -> list[str]:
        if self.redis is not None:
            return [x.decode() for x in await self.redis.lrange(f"gigbid:log:{channel}", 0, -1)]
        return list(LOG[channel])

    async def subscribe(self, channel: str):
        if self.redis is not None:
            ps = self.redis.pubsub()
            await ps.subscribe(f"gigbid:{channel}")
            try:
                while True:
                    msg = await ps.get_message(ignore_subscribe_messages=True, timeout=15)
                    yield msg["data"].decode() if msg else None
            finally:
                await ps.unsubscribe()
        else:
            q: asyncio.Queue = asyncio.Queue()
            self.local[channel].append(q)
            try:
                while True:
                    try:
                        yield await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield None
            finally:
                self.local[channel].remove(q)


LOG: dict[str, list[str]] = defaultdict(list)
BUS = Bus()


# ---------------------------------------------------------------- run execution (worker or inline)
def _identity(user_id: str) -> tuple[str, str]:
    u = db.one("SELECT persona FROM users WHERE id=:u", u=user_id)
    return user_id, (u or {}).get("persona") or "ai_ml"


def _summary(update: dict) -> dict:
    """Small, PII-masked view of a node update for the live run viewer."""
    out = {}
    for k, v in update.items():
        if k in ("trace", "usage", "steps", "errors"):
            continue
        if hasattr(v, "model_dump"):
            v = v.model_dump()
        if isinstance(v, list) and v and hasattr(v[-1], "model_dump"):
            v = v[-1].model_dump()
        out[k] = v
    return mask_obj(json.loads(json.dumps(out, default=str)))


async def _execute(graph, kind: str, user_id: str, persona: str, tid: str, inp: Any, job_id: str | None) -> dict:
    run_id = uuid.uuid4().hex[:12]
    trace_id = uuid.uuid4().hex
    mcp_client.current_user.set(user_id)
    mcp_client.current_persona.set(persona)
    mcp_client.current_trace.set(trace_id)
    db.q("INSERT INTO runs (run_id, user_id, thread_id, kind, status, trace_id, started_at) "
         "VALUES (:r,:u,:t,:k,'running',:tr,:s)", r=run_id, u=user_id, t=tid, k=kind, tr=trace_id, s=db.now())
    cfg = {"configurable": {"thread_id": tid}, "run_name": f"{kind}:{job_id or tid}",
           "metadata": {"user_id": user_id, "trace_id": trace_id, "job_id": job_id}, "callbacks": _callbacks(trace_id)}
    t0 = time.perf_counter()
    await BUS.publish(tid, {"type": "run_started", "run_id": run_id, "trace_id": trace_id, "kind": kind})
    status, error = "done", None
    try:
        async for chunk in graph.astream(inp, cfg, stream_mode="updates"):
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    await BUS.publish(tid, {"type": "interrupt", "node": "approval"})
                    continue
                upd = update or {}
                tr = (upd.get("trace") or [{}])[-1] if isinstance(upd, dict) else {}
                await BUS.publish(tid, {"type": "node", "node": node_name, "ms": tr.get("ms"), "tools": tr.get("tools", []),
                                        "llm": tr.get("llm", []), "status": upd.get("status") if isinstance(upd, dict) else None,
                                        "summary": _summary(upd) if isinstance(upd, dict) else {}})
    except mcp_client.MarketplaceDown as e:
        status, error = "degraded_queued", str(e)
    except Exception as e:  # unexpected: dead-letter the thread
        status, error = "dead_letter", f"{type(e).__name__}: {e}"
    latency = time.perf_counter() - t0
    snap = await graph.aget_state(cfg)
    vals = snap.values or {}
    pending = [i.value for t in snap.tasks for i in t.interrupts]
    usage = vals.get("usage") or {}
    tokens = sum(u.get("tokens_in", 0) + u.get("tokens_out", 0) for u in usage.values())
    usd = round(sum(u.get("usd", 0) for u in usage.values()), 6)
    final = status if status != "done" else ("awaiting_approval" if pending else vals.get("status", "done"))
    db.q("UPDATE runs SET status=:s, ended_at=:e, error=:err, latency_s=:l, tokens=:tk, usd=:usd WHERE run_id=:r",
         s=final, e=db.now(), err=error, l=round(latency, 3), tk=tokens, usd=usd, r=run_id)
    if kind == "bid" and job_id:
        project_bid(user_id, job_id, vals, final, tokens, usd, latency)
    if kind == "conversation":
        intent = vals.get("intent")
        db.q("UPDATE conversations SET status=:s, intent=:i, updated_at=:n WHERE user_id=:u AND thread_id=:t",
             s=final, i=getattr(intent, "intent", None), n=db.now(), u=user_id, t=vals.get("thread_id"))
    await BUS.publish(tid, {"type": "run_finished", "status": final, "error": error, "latency_s": round(latency, 3),
                            "tokens": tokens, "usd": usd})
    return {"status": final, "error": error, "pending": bool(pending), "trace_id": trace_id}


def project_bid(user_id: str, job_id: str, v: dict, status: str, tokens: int, usd: float, latency: float) -> None:
    job, fit, risk, price, dec = v.get("job") or {}, v.get("fit"), v.get("risk"), v.get("price"), v.get("decision")
    drafts = v.get("drafts") or []
    db.upsert_inbox(
        user_id, job_id, thread_id=bid_tid(user_id, job_id), title=job.get("title"), category=job.get("category"),
        status=status, action=getattr(dec, "action", None), fit=getattr(fit, "fit_score", None),
        risk=getattr(risk, "risk_score", None), risk_flags=[f.kind for f in getattr(risk, "flags", [])],
        price=getattr(price, "amount", None), rate_type=getattr(price, "rate_type", None),
        connects=getattr(price, "connects_cost", None) or job.get("connects_required"),
        budget_min=job.get("budget_min"), budget_max=job.get("budget_max"), posted_at=job.get("posted_at"),
        summary=v.get("attention_reason") or "; ".join(getattr(dec, "reasons", []) or []),
        unsupported_before=drafts[-1].unsupported_before_verify if drafts else None,
        unsupported_after=drafts[-1].unsupported_after_verify if drafts else None,
        tokens=tokens, usd=usd, latency_s=round(latency, 3))


def _callbacks(trace_id: str) -> list:
    if not os.environ.get("LANGFUSE_SECRET_KEY"):
        return []
    try:
        from langfuse.langchain import CallbackHandler
        return [CallbackHandler()]
    except Exception:
        return []


async def run_bid(user_id: str, job_id: str, resume: dict | None = None, force_bid: bool = False) -> dict:
    user_id, persona = _identity(user_id)
    tid = bid_tid(user_id, job_id)
    if resume is None:
        inp: Any = {"job_id": job_id, "user_id": user_id, "persona": persona, "force_bid": force_bid}
        db.upsert_inbox(user_id, job_id, status="running")
    elif resume.get("retry"):
        inp = None                              # resume the node that failed (degraded mode recovery)
    else:
        inp = Command(resume=resume)
    return await _execute(BID, "bid", user_id, persona, tid, inp, job_id)


async def run_conversation(user_id: str, thread_id: str, resume: dict | None = None) -> dict:
    user_id, persona = _identity(user_id)
    tid = conv_thread_id(user_id, thread_id)
    inp = Command(resume=resume) if resume else {"thread_id": thread_id, "user_id": user_id, "persona": persona,
                                                 "_tid": tid}
    if resume is None:
        # a new client message starts a fresh run on the same thread id; LangGraph keeps history
        pass
    return await _execute(CONV, "conversation", user_id, persona, tid, inp, None)


async def pending_approval(tid: str, kind: str = "bid") -> dict | None:
    graph = BID if kind == "bid" else CONV
    snap = await graph.aget_state({"configurable": {"thread_id": tid}})
    vals = [i.value for t in snap.tasks for i in t.interrupts]
    return vals[0] if vals else None


def is_halted(status: str | None) -> bool:
    return status in HALT

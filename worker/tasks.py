"""Background work (E1): scouting on a schedule and long graph runs, executed by an Arq worker on Redis.

    arq worker.tasks.WorkerSettings

Without REDIS_URL (local quick start) the same functions run as in-process background tasks.
Idempotency: Arq `_job_id`s are derived from (user, job, action), so a re-enqueue while a job is
queued/running is dropped; graph writes carry their own idempotency keys, so a worker restart
mid-run can never double-submit.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

from agent import mcp_client
from agent.nodes.base import tool
from agent.settings import section
from apps.api import db, runtime

_tasks: set[asyncio.Task] = set()
_inline_slots: asyncio.Semaphore | None = None
INLINE_CONCURRENCY = int(os.environ.get("INLINE_CONCURRENCY", "3"))   # mirrors WorkerSettings.max_jobs


async def _bounded(coro):
    global _inline_slots
    if _inline_slots is None:
        _inline_slots = asyncio.Semaphore(INLINE_CONCURRENCY)
    async with _inline_slots:
        return await coro


async def enqueue(fn_name: str, job_key: str, **kwargs) -> str:
    if os.environ.get("REDIS_URL") and os.environ.get("WORKER_MODE", "arq") == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
        job = await pool.enqueue_job(fn_name, _job_id=job_key, **kwargs)
        await pool.aclose()
        return "queued" if job else "duplicate"
    fn = FUNCTIONS[fn_name]
    t = asyncio.create_task(_bounded(fn({}, **kwargs)))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return "inline"


# ---------------------------------------------------------------- task functions
async def analyze_job(ctx, user_id: str, job_id: str, force_bid: bool = False) -> dict:
    res = await runtime.run_bid(user_id, job_id, force_bid=force_bid)
    if res["status"] == "degraded_queued":
        await _retry_later("retry_bid", f"retry:{user_id}:{job_id}", user_id=user_id, job_id=job_id)
    return res


async def resume_bid(ctx, user_id: str, job_id: str, decision: dict) -> dict:
    res = await runtime.run_bid(user_id, job_id, resume=decision)
    if res["status"] == "degraded_queued":
        await _retry_later("retry_bid", f"retry:{user_id}:{job_id}", user_id=user_id, job_id=job_id)
    return res


async def retry_bid(ctx, user_id: str, job_id: str) -> dict:
    """Degraded-mode recovery: resume the node that failed when the marketplace was down."""
    res = await runtime.run_bid(user_id, job_id, resume={"retry": True})
    if res["status"] == "degraded_queued":
        await _retry_later("retry_bid", f"retry:{user_id}:{job_id}", user_id=user_id, job_id=job_id)
    return res


async def converse(ctx, user_id: str, thread_id: str, decision: dict | None = None) -> dict:
    return await runtime.run_conversation(user_id, thread_id, resume=decision)


async def _retry_later(fn_name: str, key: str, delay_s: int = 60, **kw) -> None:
    if os.environ.get("REDIS_URL") and os.environ.get("WORKER_MODE", "arq") == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
        await pool.enqueue_job(fn_name, _job_id=f"{key}:{int(datetime.now().timestamp()) // delay_s}",
                               _defer_by=timedelta(seconds=delay_s), **kw)
        await pool.aclose()
    else:
        async def later():
            await asyncio.sleep(delay_s)
            await FUNCTIONS[fn_name]({}, **kw)
        t = asyncio.create_task(later())
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


def fingerprint(job: dict) -> str:
    """Duplicate reposts keep title, client and budget; ids and small wording edits change."""
    key = f"{job.get('title', '').strip().lower()}|{job.get('client_id')}|{job.get('budget_max')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


async def scout(ctx, user_id: str | None = None) -> dict:
    """Run every saved search for one user (or all users), dedupe, queue new jobs for analysis."""
    users = [db.one("SELECT id, persona FROM users WHERE id=:u", u=user_id)] if user_id else \
        db.q("SELECT id, persona FROM users WHERE onboarded=1")
    queued = dupes = 0
    cap = section("scheduling")["max_jobs_per_scout_run"]
    for u in [u for u in users if u]:
        mcp_client.current_user.set(u["id"])
        mcp_client.current_persona.set(u["persona"] or "ai_ml")
        for search in db.user_settings(u["id"]).get("saved_searches", []):
            try:
                res = await tool("scout", "marketplace.search_jobs", {
                    "query": search.get("query", ""), "skills": search.get("skills", []),
                    "min_budget": search.get("min_budget"), "limit": cap})
            except mcp_client.MarketplaceDown:
                return {"status": "degraded", "queued": queued}
            for j in res["jobs"]:
                fp = fingerprint(j)
                if db.one("SELECT 1 AS x FROM seen_jobs WHERE user_id=:u AND (job_id=:j OR fingerprint=:f)",
                          u=u["id"], j=j["job_id"], f=fp):
                    dupes += 1
                    continue
                db.q("INSERT INTO seen_jobs VALUES (:u,:j,:f,:t)", u=u["id"], j=j["job_id"], f=fp, t=db.now())
                db.upsert_inbox(u["id"], j["job_id"], status="queued", title=j["title"],
                                budget_min=j.get("budget_min"), budget_max=j.get("budget_max"),
                                connects=j.get("connects_required"), posted_at=j.get("posted_at"))
                await enqueue("analyze_job", f"analyze:{u['id']}:{j['job_id']}", user_id=u["id"], job_id=j["job_id"])
                queued += 1
    return {"status": "ok", "queued": queued, "duplicates_skipped": dupes}


async def sync_pipeline(ctx) -> dict:
    """Poll proposal statuses and new client messages; start conversation runs for new client replies."""
    n = 0
    for u in db.q("SELECT id, persona FROM users WHERE onboarded=1"):
        mcp_client.current_user.set(u["id"])
        try:
            msgs = (await mcp_client.call("liaison", "marketplace.list_messages", {}))["messages"]
        except mcp_client.MarketplaceDown:
            continue
        latest: dict[str, dict] = {}
        for m in msgs:
            latest[m["thread_id"]] = m
        for tid, m in latest.items():
            row = db.one("SELECT last_message_at FROM conversations WHERE user_id=:u AND thread_id=:t", u=u["id"], t=tid)
            if row and row["last_message_at"] == m["sent_at"]:
                continue
            if row:
                db.q("UPDATE conversations SET last_message=:b, last_message_at=:a, status='new', updated_at=:n "
                     "WHERE user_id=:u AND thread_id=:t", b=m["body"][:300], a=m["sent_at"], n=db.now(), u=u["id"], t=tid)
            else:
                db.q("INSERT INTO conversations (user_id, thread_id, job_id, status, last_message, last_message_at, updated_at) "
                     "VALUES (:u,:t,:j,'new',:b,:a,:n)", u=u["id"], t=tid, j=m.get("job_id"), b=m["body"][:300],
                     a=m["sent_at"], n=db.now())
            if m["sender"] == "client":
                await enqueue("converse", f"converse:{u['id']}:{tid}:{m['message_id']}", user_id=u["id"], thread_id=tid)
                n += 1
    return {"conversations_started": n}


FUNCTIONS = {f.__name__: f for f in (analyze_job, resume_bid, retry_bid, converse, scout, sync_pipeline)}


async def _startup(ctx):
    db.init()
    await runtime.startup()


async def _shutdown(ctx):
    await runtime.shutdown()


def _cron_jobs():
    from arq import cron
    mode = section("marketplace")["mode"]
    every = section("scheduling")["upwork_scout_interval_min" if mode == "upwork" else "gigsim_scout_interval_min"]
    minutes = set(range(0, 60, max(1, min(every, 60))))
    return [cron(scout, minute=minutes, run_at_startup=False), cron(sync_pipeline, minute=set(range(0, 60, 10)))]


class WorkerSettings:
    functions = list(FUNCTIONS.values())
    on_startup = _startup
    on_shutdown = _shutdown
    max_jobs = 4
    job_timeout = 600
    try:
        from arq.connections import RedisSettings
        redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        cron_jobs = _cron_jobs()
    except Exception:  # arq import-time config only matters inside the worker process
        pass

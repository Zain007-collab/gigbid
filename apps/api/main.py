"""GigBid API (FastAPI).

    uvicorn apps.api.main:app --port 8000

Single-container deploys can set GIGSIM_EMBEDDED=1 (mounts the simulator at /gigsim) and serve the built
web app from apps/web/dist. docker compose runs each piece as its own service instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent import mcp_client
from apps.api import db, runtime
from apps.api.routers.core import router
from shared.pii import mask

ROOT = Path(__file__).resolve().parents[2]


class PIIMaskingFilter(logging.Filter):
    """Mask emails, phones, payment details and tokens in every log record (E4)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask(record.msg)
        if isinstance(record.args, tuple):  # keep arity: formatters (e.g. uvicorn.access) unpack args
            record.args = tuple(mask(a) if isinstance(a, str) else a for a in record.args)
        return True


for h in logging.getLogger().handlers + [logging.StreamHandler()]:
    h.addFilter(PIIMaskingFilter())
logging.basicConfig(level=logging.INFO)
for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "mcp"):
    logging.getLogger(name).addFilter(PIIMaskingFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)


def seed_reviewer() -> str | None:
    """Reviewer login preloaded with a persona (credentials only from env)."""
    email, pw = os.environ.get("REVIEWER_EMAIL"), os.environ.get("REVIEWER_PASSWORD")
    if not email or not pw:
        return None
    from apps.api.auth.users import create_user
    u = db.one("SELECT id FROM users WHERE email=:e", e=email.lower())
    uid = u["id"] if u else create_user(email, pw, "Reviewer", "ai_ml")
    settings = {**db.DEFAULT_SETTINGS, "hourly_rate": 45, "min_project": 400}
    db.q("UPDATE users SET persona='ai_ml', onboarded=1, settings_json=:s WHERE id=:u", s=json.dumps(settings), u=uid)
    return uid


async def _warm_reviewer(uid: str) -> None:
    """Fill an empty reviewer inbox once so graders see real runs immediately."""
    if db.one("SELECT 1 AS x FROM inbox WHERE user_id=:u", u=uid):
        return
    from agent.settings import section
    from worker import tasks
    await asyncio.sleep(3)
    if n := os.environ.get("WARM_REVIEWER_JOBS"):        # small instances: analyse fewer jobs on first start
        section("scheduling")["max_jobs_per_scout_run"] = int(n)
    await tasks.scout({}, user_id=uid)


async def recover_orphans() -> dict:
    """After a restart (deploy, crash, free-tier recycle) nothing executes runs the DB still marks running/queued.
    Running threads resume from their last Postgres checkpoint; queued jobs are re-enqueued. Idempotency keys make
    any resumed submit exactly-once."""
    from worker import tasks
    rows = db.q("SELECT user_id, job_id, status FROM inbox WHERE status IN ('running', 'queued', 'degraded_queued')")
    for r in rows:
        if r["status"] == "queued":
            await tasks.enqueue("analyze_job", f"recover:{r['user_id']}:{r['job_id']}", user_id=r["user_id"], job_id=r["job_id"])
        else:
            await tasks.enqueue("retry_bid", f"recover:{r['user_id']}:{r['job_id']}", user_id=r["user_id"], job_id=r["job_id"])
    db.q("UPDATE runs SET status='interrupted', ended_at=:e WHERE status='running'", e=db.now())
    return {"resumed": sum(r["status"] != "queued" for r in rows), "requeued": sum(r["status"] == "queued" for r in rows)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await runtime.startup()
    if os.environ.get("RECOVER_ON_START", "1") == "1":
        logging.getLogger("gigbid").info("startup recovery: %s", await recover_orphans())
    uid = seed_reviewer()
    if uid and os.environ.get("WARM_REVIEWER", "1") == "1":
        asyncio.create_task(_warm_reviewer(uid))
    yield
    await runtime.shutdown()


app = FastAPI(title="GigBid API", version="1.0", lifespan=lifespan,
              description="Multi-agent freelancer copilot. Every proposal and message waits for human approval.")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def trace_id(request: Request, call_next):
    """One trace id from the UI request through the API, graph, MCP servers and the audit log (E3)."""
    tid = request.headers.get("x-trace-id") or uuid.uuid4().hex
    mcp_client.current_trace.set(tid)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    return response


app.include_router(router, prefix="/api")

if os.environ.get("GIGSIM_EMBEDDED") == "1":
    from gigsim.app import app as gigsim_app
    app.mount("/gigsim", gigsim_app)

WEB_DIST = ROOT / "apps" / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = WEB_DIST / path
        return FileResponse(f if path and f.is_file() else WEB_DIST / "index.html")

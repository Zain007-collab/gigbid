"""App database (users, inbox projection, runs, threads). SQLAlchemy Core: Postgres in prod, SQLite locally.

Every query that touches user data filters by user_id (tenant scoping, E4).
LangGraph checkpoints and long-term memory live in their own tables (apps/api/runtime.py).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

_url = os.environ.get("DATABASE_URL", "sqlite:///data/processed/gigbid_app.sqlite3")
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)
if _url.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(_url.removeprefix("sqlite:///")) or ".", exist_ok=True)
engine = create_engine(_url, pool_pre_ping=True, future=True)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        display_name TEXT, persona TEXT, settings_json TEXT NOT NULL DEFAULT '{}',
        onboarded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS inbox (
        user_id TEXT NOT NULL, job_id TEXT NOT NULL, thread_id TEXT NOT NULL, title TEXT, category TEXT,
        status TEXT NOT NULL, action TEXT, fit REAL, risk REAL, risk_flags TEXT, price REAL, rate_type TEXT,
        connects INTEGER, budget_min REAL, budget_max REAL, posted_at TEXT, summary TEXT,
        unsupported_before INTEGER, unsupported_after INTEGER, tokens INTEGER, usd REAL, latency_s REAL,
        updated_at TEXT NOT NULL, PRIMARY KEY (user_id, job_id))""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL, kind TEXT NOT NULL,
        status TEXT NOT NULL, trace_id TEXT, started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
        latency_s REAL, tokens INTEGER, usd REAL)""",
    """CREATE TABLE IF NOT EXISTS conversations (
        user_id TEXT NOT NULL, thread_id TEXT NOT NULL, job_id TEXT, proposal_id TEXT, status TEXT NOT NULL,
        last_message TEXT, last_message_at TEXT, intent TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, thread_id))""",
    """CREATE TABLE IF NOT EXISTS seen_jobs (
        user_id TEXT NOT NULL, job_id TEXT NOT NULL, fingerprint TEXT NOT NULL, seen_at TEXT NOT NULL,
        PRIMARY KEY (user_id, job_id))""",
    """CREATE TABLE IF NOT EXISTS rejections (
        user_id TEXT NOT NULL, thread_id TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL)""",
]


def init() -> None:
    with engine.begin() as c:
        for ddl in SCHEMA:
            c.execute(text(ddl))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(sql: str, **params: Any) -> list[dict]:
    with engine.begin() as c:
        res = c.execute(text(sql), params)
        return [dict(r._mapping) for r in res] if res.returns_rows else []


def one(sql: str, **params: Any) -> dict | None:
    rows = q(sql, **params)
    return rows[0] if rows else None


def upsert_inbox(user_id: str, job_id: str, **cols: Any) -> None:
    cols = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in cols.items()}
    cols["updated_at"] = now()
    existing = one("SELECT job_id FROM inbox WHERE user_id=:u AND job_id=:j", u=user_id, j=job_id)
    if existing:
        sets = ", ".join(f"{k}=:{k}" for k in cols)
        q(f"UPDATE inbox SET {sets} WHERE user_id=:user_id AND job_id=:job_id", user_id=user_id, job_id=job_id, **cols)
    else:
        cols.setdefault("status", "queued")
        cols.setdefault("thread_id", f"bid:{user_id}:{job_id}")
        keys = ["user_id", "job_id", *cols]
        q(f"INSERT INTO inbox ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)})",
          user_id=user_id, job_id=job_id, **cols)


def user_settings(user_id: str) -> dict:
    u = one("SELECT settings_json FROM users WHERE id=:u", u=user_id)
    return json.loads(u["settings_json"]) if u else {}


DEFAULT_SETTINGS = {
    "marketplace": "gigsim", "hourly_rate": None, "min_project": None, "weekly_connects_budget": 80,
    "autonomy": "suggest", "timezone": "Asia/Karachi",
    "saved_searches": [{"name": "Default", "query": "", "skills": [], "min_budget": None, "interval_min": 30}],
}

"""Encrypted per-user OAuth token store (A4, E4).

Tokens are Fernet-encrypted (TOKEN_ENCRYPTION_KEY) before they reach the database, every query is scoped by
tenant, and token values are never logged. Uses the app DB layer, so it works on Postgres (Neon) and SQLite.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from apps.api import db

DDL = """CREATE TABLE IF NOT EXISTS oauth_tokens (
    tenant_id TEXT NOT NULL, provider TEXT NOT NULL, access_ct TEXT NOT NULL, refresh_ct TEXT,
    expires_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (tenant_id, provider))"""


class TokenMissing(Exception):
    """User has not connected (or must reconnect) this provider."""


def _fernet() -> Fernet:
    key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def init() -> None:
    db.q(DDL)


def save_tokens(tenant_id: str, provider: str, access_token: str, refresh_token: str | None, expires_in_s: int) -> None:
    init()
    f = _fernet()
    vals = dict(t=tenant_id, p=provider, a=f.encrypt(access_token.encode()).decode(),
                r=f.encrypt(refresh_token.encode()).decode() if refresh_token else None,
                e=(datetime.now(timezone.utc) + timedelta(seconds=expires_in_s)).isoformat(), u=db.now())
    if db.one("SELECT 1 AS x FROM oauth_tokens WHERE tenant_id=:t AND provider=:p", t=tenant_id, p=provider):
        db.q("UPDATE oauth_tokens SET access_ct=:a, refresh_ct=:r, expires_at=:e, updated_at=:u "
             "WHERE tenant_id=:t AND provider=:p", **vals)
    else:
        db.q("INSERT INTO oauth_tokens VALUES (:t,:p,:a,:r,:e,:u)", **vals)


def delete_tokens(tenant_id: str, provider: str) -> None:
    init()
    db.q("DELETE FROM oauth_tokens WHERE tenant_id=:t AND provider=:p", t=tenant_id, p=provider)


def status(tenant_id: str, provider: str) -> dict:
    init()
    row = db.one("SELECT expires_at, updated_at FROM oauth_tokens WHERE tenant_id=:t AND provider=:p", t=tenant_id, p=provider)
    return {"connected": bool(row), "expires_at": row["expires_at"] if row else None}


def refresh_token(tenant_id: str, provider: str) -> str | None:
    init()
    row = db.one("SELECT refresh_ct FROM oauth_tokens WHERE tenant_id=:t AND provider=:p", t=tenant_id, p=provider)
    return _fernet().decrypt(row["refresh_ct"].encode()).decode() if row and row["refresh_ct"] else None


async def get_access_token(tenant_id: str, provider: str) -> str:
    init()
    row = db.one("SELECT access_ct, expires_at FROM oauth_tokens WHERE tenant_id=:t AND provider=:p",
                 t=tenant_id, p=provider)
    if row is None:
        raise TokenMissing(f"{provider} not connected")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc) + timedelta(seconds=30):
        from apps.api.auth import upwork_oauth
        return await upwork_oauth.refresh(tenant_id)          # raises TokenMissing if refresh fails
    return _fernet().decrypt(row["access_ct"].encode()).decode()


async def get_upwork_access_token(tenant_id: str) -> str:
    return await get_access_token(tenant_id, "upwork")

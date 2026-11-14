"""Encrypted per-user OAuth token store (A4, E4).

Tokens are Fernet-encrypted (TOKEN_ENCRYPTION_KEY) before they touch
Postgres, are scoped by tenant on every query, and are never logged.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.fernet import Fernet

DDL = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    tenant_id   TEXT NOT NULL,
    provider    TEXT NOT NULL,
    access_ct   BYTEA NOT NULL,
    refresh_ct  BYTEA,
    expires_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider)
);
"""


class TokenMissing(Exception):
    """User has not connected (or must reconnect) this provider."""


def _fernet() -> Fernet:
    key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


async def _conn() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True)


async def init() -> None:
    async with await _conn() as c:
        await c.execute(DDL)


async def save_tokens(tenant_id: str, provider: str, access_token: str,
                      refresh_token: str | None, expires_in_s: int) -> None:
    f = _fernet()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_s)
    async with await _conn() as c:
        await c.execute(
            """INSERT INTO oauth_tokens (tenant_id, provider, access_ct, refresh_ct, expires_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, provider) DO UPDATE
               SET access_ct = EXCLUDED.access_ct, refresh_ct = EXCLUDED.refresh_ct,
                   expires_at = EXCLUDED.expires_at, updated_at = now()""",
            (tenant_id, provider, f.encrypt(access_token.encode()),
             f.encrypt(refresh_token.encode()) if refresh_token else None, expires_at),
        )


async def delete_tokens(tenant_id: str, provider: str) -> None:
    async with await _conn() as c:
        await c.execute("DELETE FROM oauth_tokens WHERE tenant_id=%s AND provider=%s", (tenant_id, provider))


async def get_access_token(tenant_id: str, provider: str) -> str:
    async with await _conn() as c:
        row = await (await c.execute(
            "SELECT access_ct, expires_at FROM oauth_tokens WHERE tenant_id=%s AND provider=%s",
            (tenant_id, provider),
        )).fetchone()
    if row is None:
        raise TokenMissing(f"{provider} not connected")
    access_ct, expires_at = row
    if expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30):
        # Refresh is handled by the OAuth router (apps/api/routers/auth.py) once built.
        raise TokenMissing(f"{provider} token expired; reconnect in Settings")
    return _fernet().decrypt(access_ct).decode()


async def get_upwork_access_token(tenant_id: str) -> str:
    return await get_access_token(tenant_id, "upwork")

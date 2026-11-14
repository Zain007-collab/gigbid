"""Rate limiting, idempotency and the append-only audit log for write tools.

State lives in SQLite owned by this server (MARKETPLACE_STATE_DB), so a
restart never forgets which idempotency keys already landed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from shared.pii import mask_obj

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
    user_id     TEXT NOT NULL,
    tool        TEXT NOT NULL,
    key         TEXT NOT NULL,
    args_hash   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'done')),
    result_json TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (user_id, tool, key)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    user_id    TEXT NOT NULL,
    tool       TEXT NOT NULL,
    args_json  TEXT NOT NULL,
    result_json TEXT,
    status     TEXT NOT NULL,
    trace_id   TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


class GuardError(Exception):
    pass


class IdempotencyConflict(GuardError):
    """Same key reused with different arguments."""


class RateLimitExceeded(GuardError):
    def __init__(self, retry_after_s: float):
        super().__init__(f"per-user rate limit exceeded; retry after {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class TokenBucketLimiter:
    """Per-user token bucket, sized below GigSim's limits (config: marketplace.rate_limit)."""
    rate: float
    burst: int
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self, user_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            b = self._buckets.setdefault(user_id, _Bucket(self.burst, now))
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens < 1:
                raise RateLimitExceeded((1 - b.tokens) / self.rate)
            b.tokens -= 1


def args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


class StateStore:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ idempotency
    def begin(
        self, user_id: str, tool: str, key: str, ahash: str
    ) -> tuple[Literal["new", "replay", "pending"], dict[str, Any] | None]:
        """Claim a key. Returns ('new', None) if this call should do the write,
        ('replay', result) if it already completed, ('pending', None) if a
        previous attempt crashed mid-write and needs reconciliation."""
        with self._lock:
            row = self._db.execute(
                "SELECT args_hash, status, result_json FROM idempotency "
                "WHERE user_id=? AND tool=? AND key=?",
                (user_id, tool, key),
            ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO idempotency VALUES (?,?,?,?, 'pending', NULL, ?)",
                    (user_id, tool, key, ahash, time.time()),
                )
                return "new", None
            stored_hash, status, result_json = row
            if stored_hash != ahash:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} was already used with different arguments"
                )
            if status == "done":
                return "replay", json.loads(result_json)
            return "pending", None

    def complete(self, user_id: str, tool: str, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE idempotency SET status='done', result_json=? "
                "WHERE user_id=? AND tool=? AND key=?",
                (json.dumps(result, default=str), user_id, tool, key),
            )

    def release(self, user_id: str, tool: str, key: str) -> None:
        """Write definitely did not land upstream: free the key for a retry."""
        with self._lock:
            self._db.execute(
                "DELETE FROM idempotency WHERE user_id=? AND tool=? AND key=? AND status='pending'",
                (user_id, tool, key),
            )

    # ------------------------------------------------------------ audit log
    def audit(
        self,
        user_id: str,
        tool: str,
        args: dict[str, Any],
        result: Any,
        status: str,
        trace_id: str | None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO audit_log (ts, user_id, tool, args_json, result_json, status, trace_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    time.time(),
                    user_id,
                    tool,
                    json.dumps(mask_obj(args), default=str),
                    json.dumps(mask_obj(result), default=str),
                    status,
                    trace_id,
                ),
            )

    def audit_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT ts, user_id, tool, args_json, result_json, status, trace_id "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def raw(self) -> sqlite3.Connection:
        return self._db

"""GigBid Workspace MCP server (A3): calendar slots, interview holds, email drafts.

Backed by a local mock (SQLite) — the agent only ever sees these MCP tools, so
swapping in Google Calendar / Gmail later changes this file only.

Run:  python -m mcp_servers.workspace.server          (streamable HTTP on :8103)
      MCP_TRANSPORT=stdio python -m mcp_servers.workspace.server
"""
# no `from __future__ import annotations`: FastMCP needs evaluated annotations
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

mcp = FastMCP("gigbid-workspace", instructions="Calendar availability, tentative interview holds and email drafts for the freelancer. Nothing here sends email.")

WORK_START = time(int(os.environ.get("WORK_START_HOUR", "9")))
WORK_END = time(int(os.environ.get("WORK_END_HOUR", "18")))
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, start TEXT, end TEXT, title TEXT,
    status TEXT, idem_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, recipient TEXT, subject TEXT,
    body TEXT, created TEXT, idem_key TEXT UNIQUE);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(os.environ.get("WORKSPACE_DB", "workspace.sqlite3"),
                                check_same_thread=False, isolation_level=None)
        _conn.executescript(_SCHEMA)
    return _conn


def reset(path: str = ":memory:") -> None:
    """Tests: start from an empty calendar."""
    global _conn
    os.environ["WORKSPACE_DB"] = path
    _conn = None


class Slot(BaseModel):
    start: datetime
    end: datetime


class SlotsResult(BaseModel):
    timezone: str
    slots: list[Slot]


class HoldResult(BaseModel):
    event_id: str
    start: datetime
    end: datetime
    status: str
    replayed: bool = False


class DraftResult(BaseModel):
    draft_id: str
    recipient: str
    subject: str
    replayed: bool = False


Tz = Annotated[str, Field(max_length=64, description="IANA timezone, e.g. 'Asia/Karachi'")]
IdemKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


def _busy() -> list[tuple[datetime, datetime]]:
    rows = _db().execute("SELECT start, end FROM events WHERE status != 'cancelled'").fetchall()
    return [(datetime.fromisoformat(s), datetime.fromisoformat(e)) for s, e in rows]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def free_slots(
    from_date: date,
    days: Annotated[int, Field(ge=1, le=14)] = 5,
    duration_min: Annotated[int, Field(ge=15, le=180)] = 30,
    tz: Tz = "UTC",
    max_slots: Annotated[int, Field(ge=1, le=20)] = 6,
) -> SlotsResult:
    """Free interview slots within working hours (weekdays), avoiding existing holds."""
    try:
        zone = ZoneInfo(tz)
    except Exception as e:
        raise ToolError(f"unknown timezone {tz!r}") from e
    busy, out, step = _busy(), [], timedelta(minutes=duration_min)
    now = datetime.now(timezone.utc)
    for d in range(days):
        day = from_date + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        cur = datetime.combine(day, WORK_START, zone)
        end_of_day = datetime.combine(day, WORK_END, zone)
        while cur + step <= end_of_day and len(out) < max_slots:
            nxt = cur + step
            if cur > now and not any(cur < e and nxt > s for s, e in busy):
                out.append(Slot(start=cur, end=nxt))
                cur = nxt + timedelta(minutes=60)   # spread suggestions across the day
            else:
                cur = cur + timedelta(minutes=30)
    return SlotsResult(timezone=tz, slots=out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def create_interview_hold(
    start: datetime,
    idempotency_key: IdemKey,
    duration_min: Annotated[int, Field(ge=15, le=180)] = 30,
    title: Annotated[str, Field(min_length=1, max_length=200)] = "Client interview (tentative)",
) -> HoldResult:
    """Place a TENTATIVE hold on the freelancer's calendar. Invites nobody."""
    if start.tzinfo is None:
        raise ToolError("start must include a timezone offset")
    end = start + timedelta(minutes=duration_min)
    with _lock:
        row = _db().execute("SELECT id, start, end, status FROM events WHERE idem_key=?", (idempotency_key,)).fetchone()
        if row:
            return HoldResult(event_id=row[0], start=row[1], end=row[2], status=row[3], replayed=True)
        if any(start < e and end > s for s, e in _busy()):
            raise ToolError("SLOT_TAKEN: overlaps an existing hold")
        eid = f"ev_{uuid.uuid4().hex[:10]}"
        _db().execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                      (eid, start.isoformat(), end.isoformat(), title, "tentative", idempotency_key))
    return HoldResult(event_id=eid, start=start, end=end, status="tentative")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def create_email_draft(
    recipient_label: Annotated[str, Field(min_length=1, max_length=200, description="name or address; stored, never sent")],
    subject: Annotated[str, Field(min_length=1, max_length=300)],
    body: Annotated[str, Field(min_length=1, max_length=10_000)],
    idempotency_key: IdemKey,
) -> DraftResult:
    """Save an email DRAFT for the freelancer to review. Never sends."""
    with _lock:
        row = _db().execute("SELECT id, recipient, subject FROM drafts WHERE idem_key=?", (idempotency_key,)).fetchone()
        if row:
            return DraftResult(draft_id=row[0], recipient=row[1], subject=row[2], replayed=True)
        did = f"dr_{uuid.uuid4().hex[:10]}"
        _db().execute("INSERT INTO drafts VALUES (?,?,?,?,?,?)",
                      (did, recipient_label, subject, body, datetime.now(timezone.utc).isoformat(), idempotency_key))
    return DraftResult(draft_id=did, recipient=recipient_label, subject=subject)


def main() -> None:
    if os.environ.get("MCP_TRANSPORT") == "stdio":
        mcp.run("stdio")
        return
    import uvicorn

    from shared.mcp_auth import BearerAuth

    token = os.environ.get("WORKSPACE_SERVICE_TOKEN")
    if not token:
        raise SystemExit("WORKSPACE_SERVICE_TOKEN must be set for the HTTP transport")
    uvicorn.run(BearerAuth(mcp.streamable_http_app(), token),
                host="0.0.0.0", port=int(os.environ.get("PORT", "8103")))


if __name__ == "__main__":
    main()

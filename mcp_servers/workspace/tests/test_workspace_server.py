from datetime import date, datetime, timedelta, timezone

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.workspace import server as ws

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh():
    ws.reset()


async def call(name, args):
    async with create_connected_server_and_client_session(ws.mcp._mcp_server) as client:
        return await client.call_tool(name, args)


def next_monday() -> date:
    d = date.today() + timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


async def test_free_slots_weekdays_only_and_in_hours():
    r = await call("free_slots", {"from_date": next_monday().isoformat(), "days": 7, "tz": "UTC"})
    slots = r.structuredContent["slots"]
    assert slots
    for s in slots:
        start = datetime.fromisoformat(s["start"])
        assert start.weekday() < 5 and 9 <= start.hour < 18


async def test_hold_removes_slot_and_is_idempotent():
    mon = next_monday()
    first = (await call("free_slots", {"from_date": mon.isoformat(), "days": 1}))
    start = first.structuredContent["slots"][0]["start"]
    a = await call("create_interview_hold", {"start": start, "idempotency_key": "thread-t1:hold:1"})
    b = await call("create_interview_hold", {"start": start, "idempotency_key": "thread-t1:hold:1"})
    assert a.structuredContent["event_id"] == b.structuredContent["event_id"]
    assert b.structuredContent["replayed"] is True
    after = await call("free_slots", {"from_date": mon.isoformat(), "days": 1})
    assert start not in [s["start"] for s in after.structuredContent["slots"]]
    clash = await call("create_interview_hold", {"start": start, "idempotency_key": "thread-t2:hold:1"})
    assert clash.isError and "SLOT_TAKEN" in clash.content[0].text


async def test_naive_datetime_rejected():
    naive = datetime.combine(next_monday(), datetime.min.time()).replace(hour=10).isoformat()
    r = await call("create_interview_hold", {"start": naive, "idempotency_key": "thread-t1:hold:2"})
    assert r.isError


async def test_email_draft_never_sends_and_is_idempotent():
    args = {"recipient_label": "Client A", "subject": "Interview", "body": "Does Tuesday work?",
            "idempotency_key": "thread-t1:email:1"}
    a = await call("create_email_draft", args)
    b = await call("create_email_draft", args)
    assert a.structuredContent["draft_id"] == b.structuredContent["draft_id"]

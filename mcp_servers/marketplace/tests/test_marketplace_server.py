"""Marketplace MCP server tests through an in-memory MCP client session."""
import sqlite3

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.marketplace import server as srv
from mcp_servers.marketplace.adapters.fake import FakeMarketplace
from mcp_servers.marketplace.guards import StateStore, TokenBucketLimiter

pytestmark = pytest.mark.anyio

LETTER = "Hi — I built a LangChain + Pinecone RAG system with an eval harness last quarter. " * 2


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake():
    f = FakeMarketplace(connects=100)
    srv.set_runtime(srv.Runtime(adapter=f, store=StateStore(":memory:"),
                                limiter=TokenBucketLimiter(rate=1000, burst=1000)))
    return f


async def call(name, args):
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as client:
        return await client.call_tool(name, args)


async def test_tools_have_schemas_and_annotations(fake):
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "search_jobs", "get_job", "get_client_history", "get_connects_balance", "list_messages",
        "get_market_stats", "list_proposals", "create_proposal_draft", "submit_proposal", "send_message",
    }
    for name in ("search_jobs", "get_job", "get_client_history", "get_connects_balance", "list_messages"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["submit_proposal"].annotations.destructiveHint is True
    for name in ("create_proposal_draft", "submit_proposal", "send_message"):
        t = tools[name]
        assert t.annotations.readOnlyHint is False and t.annotations.idempotentHint is True
        assert "idempotency_key" in t.inputSchema["required"]
        assert t.outputSchema is not None


async def test_get_job_structured(fake):
    r = await call("get_job", {"job_id": "j1"})
    assert not r.isError
    assert r.structuredContent["job"]["connects_required"] == 12


async def test_input_constraints_enforced(fake):
    r = await call("get_job", {"job_id": "bad id with spaces"})
    assert r.isError


async def test_dry_run_previews_cost_and_sends_nothing(fake):
    draft = await call("create_proposal_draft", {
        "job_id": "j1", "cover_letter": LETTER, "rate_type": "fixed", "bid_amount": 1000,
        "idempotency_key": "job-j1:draft:1", "dry_run": False, "approval_id": "appr-1"})
    draft_id = draft.structuredContent["receipt"]["draft_id"]

    r = await call("submit_proposal", {"job_id": "j1", "draft_id": draft_id,
                                       "idempotency_key": "job-j1:submit:1"})
    assert r.structuredContent["status"] == "dry_run"
    assert r.structuredContent["preview"]["connects_cost"] == 12
    assert r.structuredContent["preview"]["balance_after"] == 88
    assert fake.submit_calls == 0 and fake.connects == 100


async def test_real_write_requires_approval(fake):
    r = await call("send_message", {"thread_id": "t1", "body": "Monday works.",
                                    "idempotency_key": "thread-t1:reply:1", "dry_run": False})
    assert r.isError and "APPROVAL_REQUIRED" in r.content[0].text
    assert fake.sent == []


async def test_retry_never_double_submits_or_double_spends(fake):
    d = await call("create_proposal_draft", {
        "job_id": "j1", "cover_letter": LETTER, "rate_type": "fixed", "bid_amount": 1000,
        "idempotency_key": "job-j1:draft:1", "dry_run": False, "approval_id": "appr-1"})
    draft_id = d.structuredContent["receipt"]["draft_id"]
    args = {"job_id": "j1", "draft_id": draft_id, "idempotency_key": "job-j1:submit:1",
            "dry_run": False, "approval_id": "appr-1"}

    first = await call("submit_proposal", args)
    second = await call("submit_proposal", args)
    assert first.structuredContent["status"] == "done"
    assert second.structuredContent["status"] == "replayed"
    assert second.structuredContent["receipt"] == first.structuredContent["receipt"]
    assert fake.submit_calls == 1 and fake.connects == 88


async def test_crash_after_upstream_write_is_reconciled(fake):
    """Key claimed + upstream write landed, but the server died before recording it."""
    rt = srv.runtime()
    d = await call("create_proposal_draft", {
        "job_id": "j1", "cover_letter": LETTER, "rate_type": "fixed", "bid_amount": 1000,
        "idempotency_key": "job-j1:draft:1", "dry_run": False, "approval_id": "appr-1"})
    draft_id = d.structuredContent["receipt"]["draft_id"]
    key = "job-j1:submit:crash"
    await fake.submit_proposal("local", draft_id, key)              # landed upstream
    from mcp_servers.marketplace.guards import args_hash
    rt.store.begin("local", "submit_proposal", key, args_hash({"job_id": "j1", "draft_id": draft_id}))
    # ...process died here; on restart the same call comes in again:
    r = await call("submit_proposal", {"job_id": "j1", "draft_id": draft_id, "idempotency_key": key,
                                       "dry_run": False, "approval_id": "appr-1"})
    assert r.structuredContent["status"] == "replayed"
    assert fake.submit_calls == 1


async def test_key_reuse_with_different_args_rejected(fake):
    base = {"thread_id": "t1", "idempotency_key": "thread-t1:reply:1", "dry_run": False, "approval_id": "a"}
    assert not (await call("send_message", {**base, "body": "Monday works."})).isError
    r = await call("send_message", {**base, "body": "Actually Tuesday."})
    assert r.isError and "IDEMPOTENCY_CONFLICT" in r.content[0].text
    assert len(fake.sent) == 1


async def test_marketplace_down_reports_unavailable(fake):
    fake.down = True
    r = await call("get_job", {"job_id": "j1"})
    assert r.isError and "MARKETPLACE_UNAVAILABLE" in r.content[0].text


async def test_per_user_rate_limit(fake):
    srv.runtime().limiter = TokenBucketLimiter(rate=0.001, burst=2)
    assert not (await call("get_connects_balance", {})).isError
    assert not (await call("get_connects_balance", {})).isError
    r = await call("get_connects_balance", {})
    assert r.isError and "RATE_LIMITED" in r.content[0].text


async def test_audit_log_append_only_and_masked(fake):
    await call("send_message", {"thread_id": "t1", "body": "Email me at bob@example.com or +1 415 555 0100",
                                "idempotency_key": "thread-t1:reply:9"})
    store = srv.runtime().store
    rows = store.audit_rows()
    assert rows and "bob@example.com" not in rows[0]["args_json"]
    assert "[EMAIL]" in rows[0]["args_json"] and "[PHONE]" in rows[0]["args_json"]
    with pytest.raises(sqlite3.DatabaseError):
        store.raw().execute("UPDATE audit_log SET status='x'")
    with pytest.raises(sqlite3.DatabaseError):
        store.raw().execute("DELETE FROM audit_log")

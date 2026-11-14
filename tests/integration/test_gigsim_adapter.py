"""GigSim (in-process ASGI) <- GigSimAdapter <- Marketplace MCP server <- MCP client."""
import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from gigsim import app as gs
from mcp_servers.marketplace import server as srv
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.guards import StateStore, TokenBucketLimiter

pytestmark = pytest.mark.anyio
CFG = {"base_url_env": "X_UNSET", "api_key_env": "X_UNSET", "timeout_s": 5,
       "max_retries": 3, "backoff_base_s": 0.01, "backoff_max_s": 0.05}
LETTER = ("Your logistics team needs a RAG bot over its PDFs; I shipped one that cut lookup time from "
          "8 minutes to 2 seconds using LangChain and FastAPI. ") * 2


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(gs, "BURST", 10_000)            # rate limiting has its own test below
    gs.SIM.reset()
    gs.SIM.error_rate = 0.0
    a = GigSimAdapter(CFG, transport=httpx.ASGITransport(app=gs.app))
    srv.set_runtime(srv.Runtime(adapter=a, store=StateStore(":memory:"),
                                limiter=TokenBucketLimiter(rate=1000, burst=1000)))
    return a


async def call(name, args):
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as c:
        return await c.call_tool(name, args)


async def test_read_tools_against_gigsim(adapter):
    jobs = (await call("search_jobs", {"query": "", "limit": 5})).structuredContent["jobs"]
    assert jobs
    job = (await call("get_job", {"job_id": jobs[0]["job_id"]})).structuredContent["job"]
    assert job["client"]["client_id"]
    hist = await call("get_client_history", {"client_id": job["client"]["client_id"]})
    assert not hist.isError
    stats = (await call("get_market_stats", {"category": "ai_ml"})).structuredContent["stats"]
    assert stats["fixed"]["p25"] <= stats["fixed"]["p50"] <= stats["fixed"]["p75"]


async def test_submit_exactly_once_and_client_responds(adapter):
    job_id = "dev-ai_ml-001"
    job = (await call("get_job", {"job_id": job_id})).structuredContent["job"]
    bal0 = (await call("get_connects_balance", {})).structuredContent["balance"]["available"]
    d = await call("create_proposal_draft", {
        "job_id": job_id, "cover_letter": LETTER, "rate_type": "fixed",
        "bid_amount": max(50, job["budget_min"] or 100), "idempotency_key": "it:draft:1",
        "dry_run": False, "approval_id": "a1"})
    draft_id = d.structuredContent["receipt"]["draft_id"]
    args = {"job_id": job_id, "draft_id": draft_id, "idempotency_key": "it:submit:1",
            "dry_run": False, "approval_id": "a1"}
    first = (await call("submit_proposal", args)).structuredContent
    again = (await call("submit_proposal", args)).structuredContent
    assert first["status"] == "done" and again["status"] == "replayed"
    bal1 = (await call("get_connects_balance", {})).structuredContent["balance"]["available"]
    assert bal0 - bal1 == first["receipt"]["connects_spent"]
    assert len(gs.SIM.proposals) == 1
    gs.SIM.tick_min += 60 * 24 * 8                       # a week passes
    lp = await call("list_proposals", {})
    assert not lp.isError, lp.content[0].text
    props = lp.structuredContent["proposals"]
    assert props[0]["status"] in ("viewed", "replied", "interview", "hired", "lost")


async def test_retries_through_5xx_and_reports_outage(adapter):
    import random
    random.seed(1)
    gs.SIM.error_rate = 0.3                             # flaky but recoverable with 3 retries
    for _ in range(5):
        assert not (await call("get_market_stats", {"category": "web"})).isError
    gs.SIM.down = True
    r = await call("get_job", {"job_id": "dev-ai_ml-001"})
    assert r.isError and "MARKETPLACE_UNAVAILABLE" in r.content[0].text


async def test_policy_v2_change_request(adapter):
    job = next(j for j in gs.SIM.jobs.values() if (j.get("budget_max") or 0) >= 1000 and "release_offset_min" not in j)
    base = (await call("get_job", {"job_id": job["job_id"]})).structuredContent["job"]["connects_required"]
    gs.SIM.policy_version = 2
    v2 = (await call("get_job", {"job_id": job["job_id"]})).structuredContent["job"]["connects_required"]
    assert v2 == base + 2


async def test_gigsim_429_surfaces_as_rate_limited(adapter, monkeypatch):
    monkeypatch.setattr(gs, "BURST", 2)
    monkeypatch.setattr(gs, "RPS", 0.01)
    gs.SIM.buckets.clear()
    results = [await call("get_market_stats", {"category": "web"}) for _ in range(4)]
    assert not results[0].isError
    assert any(r.isError and "RATE_LIMITED" in r.content[0].text for r in results[2:])


async def test_admin_requires_token_even_when_mounted(monkeypatch):
    """Regression: under a mount the middleware saw '/gigsim/admin/...' and skipped the token check."""
    from fastapi import FastAPI
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", "secret")
    host = FastAPI()
    host.mount("/gigsim", gs.app)
    gs.SIM.reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://t") as c:
        assert (await c.post("/gigsim/admin/chaos", json={"down": True})).status_code == 403
        ok = await c.post("/gigsim/admin/chaos", json={"down": True}, headers={"x-admin-token": "secret"})
        assert ok.status_code == 200 and gs.SIM.down
        back = await c.post("/gigsim/admin/chaos", json={"down": False}, headers={"x-admin-token": "secret"})
        assert back.status_code == 200 and not gs.SIM.down     # admin still reachable while "down"

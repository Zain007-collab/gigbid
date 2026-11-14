"""Shared harness: GigSim in-process <- GigSim adapter <- Marketplace MCP (memory transport), offline LLMs."""
import os

import httpx
import pytest

os.environ.setdefault("GIGBID_OFFLINE", "1")          # deterministic node fallbacks, no API keys
os.environ.setdefault("RAG_EMBED_BACKEND", "hash")    # CI-speed embeddings (never used for reported numbers)
os.environ.setdefault("GIGBID_MCP_TRANSPORT", "memory")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def gigsim(monkeypatch):
    from gigsim import app as gs
    from mcp_servers.marketplace import server as srv
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    from mcp_servers.marketplace.guards import StateStore, TokenBucketLimiter

    monkeypatch.setattr(gs, "BURST", 10_000)
    gs.SIM.reset()
    gs.SIM.error_rate = 0.0
    cfg = {"base_url_env": "X_UNSET", "api_key_env": "X_UNSET", "timeout_s": 5, "max_retries": 3,
           "backoff_base_s": 0.01, "backoff_max_s": 0.05}
    srv.set_runtime(srv.Runtime(adapter=GigSimAdapter(cfg, transport=httpx.ASGITransport(app=gs.app)),
                                store=StateStore(":memory:"), limiter=TokenBucketLimiter(rate=1000, burst=1000)))
    return gs

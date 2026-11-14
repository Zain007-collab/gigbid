"""Shared eval harness: GigSim in-process, Marketplace MCP over the memory transport, fresh state per run."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("GIGBID_MCP_TRANSPORT", "memory")


def setup_gigsim(error_rate: float = 0.0):
    from gigsim import app as gs
    from mcp_servers.marketplace import server as srv
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    from mcp_servers.marketplace.guards import StateStore, TokenBucketLimiter

    gs.BURST, gs.RPS = 100_000, 100_000
    gs.SIM.reset()
    gs.SIM.error_rate = error_rate
    cfg = {"base_url_env": "X_UNSET", "api_key_env": "X_UNSET", "timeout_s": 10, "max_retries": 3,
           "backoff_base_s": 0.01, "backoff_max_s": 0.1}
    srv.set_runtime(srv.Runtime(adapter=GigSimAdapter(cfg, transport=httpx.ASGITransport(app=gs.app)),
                                store=StateStore(":memory:"), limiter=TokenBucketLimiter(rate=1e6, burst=10**6)))
    return gs


def dev_rows(persona: str, split: str = "dev", limit: int | None = None) -> list[dict]:
    rows = [json.loads(l) for l in (ROOT / "evals" / "datasets" / split / f"{persona}.jsonl").read_text().splitlines()]
    return rows[:limit] if limit else rows


def pct(xs: list[float], p: float) -> float | None:
    xs = sorted(xs)
    return round(xs[int(p * (len(xs) - 1))], 4) if xs else None


def write(name: str, data: dict) -> Path:
    out = ROOT / "evals" / "results" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, default=str))
    return out

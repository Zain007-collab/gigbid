"""Adapter factory: config decides which marketplace the port talks to (R1)."""
from __future__ import annotations

from agent.marketplace_port import MarketplacePort
from agent.settings import section


def build_adapter(token_provider=None) -> MarketplacePort:
    cfg = section("marketplace")
    if cfg["mode"] == "gigsim":
        from .gigsim import GigSimAdapter
        return GigSimAdapter(cfg["gigsim"])
    if cfg["mode"] == "upwork":
        if token_provider is None:
            from apps.api.auth.token_store import get_upwork_access_token as token_provider  # noqa: N813
        from .upwork_live import UpworkLiveAdapter
        return UpworkLiveAdapter(cfg["upwork"], token_provider)
    raise ValueError(f"unknown marketplace mode {cfg['mode']!r}")

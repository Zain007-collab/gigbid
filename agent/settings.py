"""Loads agent/config.yaml once and applies environment overrides.

Nothing else in the codebase should read config.yaml directly.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(os.environ.get("GIGBID_CONFIG", Path(__file__).with_name("config.yaml")))

try:  # local development: secrets from the git-ignored .env at the repo root (never committed)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if profile := os.environ.get("GIGBID_PROFILE"):
        cfg["active_profile"] = profile
    if mode := os.environ.get("MARKETPLACE_MODE"):
        cfg["marketplace"]["mode"] = mode
    if cfg["active_profile"] not in cfg["profiles"]:
        raise ValueError(f"unknown profile {cfg['active_profile']!r}")
    if cfg["marketplace"]["mode"] not in ("gigsim", "upwork"):
        raise ValueError(f"unknown marketplace mode {cfg['marketplace']['mode']!r}")
    if cfg["autonomy"]["default_level"] == "auto_send":
        raise ValueError("auto_send is never allowed (R4)")
    return cfg


def node_model(node: str) -> dict[str, Any]:
    """Model settings for a graph node under the active profile."""
    cfg = load_config()
    profile = cfg["profiles"][cfg["active_profile"]]
    return dict(profile["nodes"].get(node, profile["defaults"]))


def fallback_model() -> dict[str, Any]:
    cfg = load_config()
    return dict(cfg["profiles"][cfg["active_profile"]]["fallback"])


def section(name: str) -> dict[str, Any]:
    return load_config()[name]

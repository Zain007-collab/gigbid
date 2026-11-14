"""Per-node chat models from config (never hard-coded), with fallback and usage accounting.

All hosted providers are reached through their OpenAI-compatible endpoints.
If no API key is configured, `structured()` returns None and nodes use their
deterministic fallback path (keeps CI and offline runs working).
"""
from __future__ import annotations

import os
import time
from typing import TypeVar

from pydantic import BaseModel

from agent.settings import fallback_model, node_model, section

T = TypeVar("T", bound=BaseModel)

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": (os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1", None),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}


def _available(spec: dict) -> bool:
    if os.environ.get("GIGBID_OFFLINE") == "1":
        return False
    base, key_env = PROVIDERS.get(spec["provider"], (None, None))
    return base is not None and (key_env is None or bool(os.environ.get(key_env)))


def _chat(spec: dict):
    from langchain_openai import ChatOpenAI

    base, key_env = PROVIDERS[spec["provider"]]
    return ChatOpenAI(model=spec["model"], base_url=base, api_key=os.environ.get(key_env or "", "ollama") or "none",
                      temperature=spec.get("temperature", 0), timeout=spec.get("timeout_s", 30),
                      max_retries=spec.get("max_retries", 2))


def cost_usd(spec: dict, tin: int, tout: int) -> float:
    table = section("pricing_usd_per_mtok")
    key = f"{spec['provider']}/{spec['model']}"
    p = table.get(key) or table.get(f"{spec['provider']}/*") or {"input": 0, "output": 0}
    return (tin * p["input"] + tout * p["output"]) / 1e6


def online(node: str) -> bool:
    return _available(node_model(node))


def structured(node: str, schema: type[T], system: str, user: str) -> tuple[T | None, dict]:
    """Call the node's model for a structured object. Returns (object|None, usage)."""
    usage = {"node": node, "tokens_in": 0, "tokens_out": 0, "usd": 0.0, "latency_s": 0.0, "model": None}
    specs = [node_model(node)]
    fb = fallback_model()
    if fb and fb != specs[0]:
        specs.append(fb)
    last_err = None
    for spec in specs:
        if not _available(spec):
            continue
        t0 = time.perf_counter()
        try:
            llm = _chat(spec).with_structured_output(schema, method="function_calling", include_raw=True)
            out = llm.invoke([("system", system), ("user", user)])
        except Exception as e:  # provider down / rate-limited / bad JSON → try fallback
            last_err = e
            continue
        raw, parsed = out.get("raw"), out.get("parsed")
        um = getattr(raw, "usage_metadata", None) or {}
        tin, tout = um.get("input_tokens", 0), um.get("output_tokens", 0)
        usage.update(tokens_in=tin, tokens_out=tout, usd=round(cost_usd(spec, tin, tout), 6),
                     latency_s=round(time.perf_counter() - t0, 3), model=f"{spec['provider']}/{spec['model']}")
        if parsed is not None:
            return parsed, usage
        last_err = out.get("parsing_error")
    if last_err is not None:
        usage["error"] = f"{type(last_err).__name__}: {str(last_err)[:200]}"
    return None, usage

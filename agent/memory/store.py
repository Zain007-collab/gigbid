"""Long-term memory access (LangGraph BaseStore). Reads for prompts; list/delete for the Settings memory viewer.

Writes happen only in agent/memory/promotion_rules.py.
"""
from __future__ import annotations

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

_STORE: BaseStore = InMemoryStore()


def configure(store: BaseStore) -> None:
    global _STORE
    _STORE = store


def get() -> BaseStore:
    return _STORE


async def style_rules(user_id: str) -> list[str]:
    return [i.value["rule"] for i in await _STORE.asearch(("style", user_id), limit=50)]


async def outcome_insights(user_id: str, category: str) -> list[str]:
    return [i.value["insight"] for i in await _STORE.asearch(("outcome", user_id, category), limit=20)]


async def list_memories(user_id: str) -> list[dict]:
    out = []
    for ns_prefix in (("style", user_id), ("outcome", user_id)):
        for i in await _STORE.asearch(ns_prefix, limit=200):
            out.append({"namespace": list(i.namespace), "key": i.key, **i.value})
    return out


async def delete_memory(user_id: str, namespace: list[str], key: str) -> bool:
    if len(namespace) < 2 or namespace[1] != user_id or namespace[0] not in ("style", "outcome"):
        return False  # tenant scoping: users can only delete their own memories
    await _STORE.adelete(tuple(namespace), key)
    # forget the candidate too, so the same edits must recur before it is promoted again
    if namespace[0] == "style":
        await _STORE.adelete(("style_candidates", user_id), key)
    return True

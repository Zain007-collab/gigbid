"""Checkpoint serializer that explicitly allows GigBid's hand-off types (no blanket pickle)."""
from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent import handoffs

ALLOWED = [("agent.handoffs", name) for name in dir(handoffs)
           if isinstance(getattr(handoffs, name), type) and getattr(handoffs, name).__module__ == "agent.handoffs"]


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED)

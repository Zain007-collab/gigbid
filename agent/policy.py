"""Config-driven platform policy (Connects cost and bidding rules).

The 2 Nov "platform policy update" should be absorbable by editing the
`policy:` block of config.yaml; code here only interprets it.
"""
from __future__ import annotations

import math

from agent.settings import section


def connects_cost(listed_cost: int) -> int:
    """Connects a proposal actually costs after platform policy is applied."""
    p = section("policy")["connects"]
    return int(math.ceil(listed_cost * float(p["multiplier"]))) + int(p["extra_per_proposal"])


def bidding_rules() -> list[dict]:
    return list(section("policy").get("bidding_rules") or [])

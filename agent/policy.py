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


def check_bidding_rules(job: dict, my_proposals: list[dict], now) -> str | None:
    """Return a human-readable violation for the first config rule this bid would break, else None.

    Supported rule types (config `policy.bidding_rules`):
      {rule: max_proposals_per_client_per_week, value: N}
      {rule: min_client_rating, value: X}
      {rule: max_connects_per_proposal, value: N}
    """
    from datetime import datetime, timedelta
    for r in bidding_rules():
        kind, value = r.get("rule"), r.get("value")
        if kind == "max_proposals_per_client_per_week":
            week_ago = now - timedelta(days=7)
            recent = [p for p in my_proposals if p.get("client_id") == job["client"]["client_id"]
                      and p.get("submitted_at") and datetime.fromisoformat(str(p["submitted_at"])) > week_ago]
            if len(recent) >= value:
                return f"platform policy: at most {value} proposals to the same client per 7 days ({len(recent)} sent)"
        elif kind == "min_client_rating" and (job["client"].get("rating") or 0) < value:
            return f"your rule: client rating below {value}"
        elif kind == "max_connects_per_proposal" and job.get("connects_required", 0) > value:
            return f"your rule: costs more than {value} Connects"
    return None

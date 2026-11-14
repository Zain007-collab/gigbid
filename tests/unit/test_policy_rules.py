from datetime import datetime, timedelta, timezone

from agent import policy, settings


def test_max_proposals_per_client_rule(monkeypatch):
    cfg = settings.load_config()
    monkeypatch.setitem(cfg["policy"], "bidding_rules", [{"rule": "max_proposals_per_client_per_week", "value": 2}])
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    job = {"client": {"client_id": "c1", "rating": 4.9}, "connects_required": 8}
    two = [{"client_id": "c1", "submitted_at": (now - timedelta(days=d)).isoformat()} for d in (1, 3)]
    assert "at most 2" in policy.check_bidding_rules(job, two, now)
    old = [{"client_id": "c1", "submitted_at": (now - timedelta(days=9)).isoformat()}] * 2
    assert policy.check_bidding_rules(job, old, now) is None            # outside the 7-day window
    other = [{"client_id": "c2", "submitted_at": now.isoformat()}] * 5
    assert policy.check_bidding_rules(job, other, now) is None          # different client


def test_no_rules_no_effect():
    assert policy.check_bidding_rules({"client": {"client_id": "c"}}, [], datetime.now(timezone.utc)) is None

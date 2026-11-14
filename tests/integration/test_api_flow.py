"""API end to end (inline worker mode): onboarding -> analyze -> approval -> submitted; degraded health."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.anyio
DEV = [json.loads(l) for l in (Path(__file__).resolve().parents[2] / "evals/datasets/dev/ai_ml.jsonl").read_text().splitlines()]


@pytest.fixture
async def api(gigsim, tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "cp.sqlite3"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REVIEWER_EMAIL", raising=False)
    from sqlalchemy import create_engine

    from apps.api import db, runtime
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path / 'app.sqlite3'}"))
    db.init()
    await runtime.startup()
    from apps.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await runtime.shutdown()


async def wait_status(c, job_id, want, timeout=60):
    for _ in range(timeout * 4):
        r = await c.get(f"/api/jobs/{job_id}")
        if r.status_code == 200 and r.json()["status"] in want:
            return r.json()
        await asyncio.sleep(0.25)
    raise AssertionError(f"{job_id} never reached {want}: {r.json() if r.status_code == 200 else r.text}")


async def test_onboard_analyze_approve_submit(api, gigsim):
    r = await api.post("/api/auth/register", json={"email": "a@b.co", "password": "longpassword1"})
    assert r.status_code == 200
    r = await api.post("/api/auth/onboarding", json={"persona": "ai_ml", "settings": {"weekly_connects_budget": 60}})
    assert r.json()["onboarded"] is True and r.json()["settings"]["hourly_rate"] == 45
    job = next(x["job_id"] for x in DEV if x["gold"]["bid"] and x["gold"]["coverage"] == 1.0)
    assert (await api.post(f"/api/jobs/{job}/analyze")).status_code == 200
    d = await wait_status(api, job, {"awaiting_approval", "skipped", "watch", "blocked", "dead_letter"})
    assert d["status"] == "awaiting_approval", d.get("attention_reason")
    assert d["pending_approval"]["preview"]["connects_cost"] > 0
    inbox = (await api.get("/api/jobs")).json()
    assert inbox[0]["job_id"] == job and inbox[0]["fit"] is not None
    r = await api.post(f"/api/approvals/proposal/{job}", json={"action": "approve"})
    assert r.status_code == 200
    d = await wait_status(api, job, {"submitted", "dead_letter"})
    assert d["status"] == "submitted" and len(gigsim.SIM.proposals) == 1
    # approving twice is refused: nothing is pending any more
    assert (await api.post(f"/api/approvals/proposal/{job}", json={"action": "approve"})).status_code == 409
    trace = (await api.get(f"/api/runs/bid:{(await api.get('/api/auth/me')).json()['id']}:{job}/trace")).json()
    assert {t["node"] for t in trace["trace"]} >= {"intake", "job_analyst", "risk_assessor", "fit_matcher", "submit"}


async def test_health_reports_degraded_mode(api, gigsim):
    assert (await api.get("/api/health")).json()["marketplace_up"] is True
    gigsim.SIM.down = True
    h = (await api.get("/api/health")).json()
    assert h["marketplace_up"] is False and "unavailable" in h["detail"].lower()


async def test_other_users_cannot_read_threads(api, gigsim):
    await api.post("/api/auth/register", json={"email": "x@y.co", "password": "longpassword1"})
    r = await api.get("/api/runs/bid:someone_else:dev-ai_ml-001/trace")
    assert r.status_code == 403

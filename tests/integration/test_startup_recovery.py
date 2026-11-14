"""A run interrupted by a process restart is resumed from its checkpoint on the next startup."""
import asyncio

import pytest
from sqlalchemy import create_engine

pytestmark = pytest.mark.anyio


async def test_orphaned_running_job_resumes_after_restart(gigsim, tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from apps.api import db, main, runtime
    from apps.api.auth.users import create_user
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path / 'a.sqlite3'}"))
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "cp.sqlite3"))
    db.init()
    await runtime.startup()
    uid = create_user("r@x.io", "longpassword1", persona="ai_ml")
    db.q("UPDATE users SET onboarded=1 WHERE id=:u", u=uid)
    dev = [json.loads(l) for l in (Path(__file__).resolve().parents[2] / "evals/datasets/dev/ai_ml.jsonl").read_text().splitlines()]
    job = next(r["job_id"] for r in dev if r["gold"]["bid"] and r["gold"]["coverage"] == 1.0)
    # the process "died" mid-run: the DB says running, nothing is executing
    db.upsert_inbox(uid, job, status="running")
    res = await main.recover_orphans()
    assert res == {"resumed": 1, "requeued": 0}
    for _ in range(120):
        await asyncio.sleep(0.25)
        if db.one("SELECT status FROM inbox WHERE job_id=:j", j=job)["status"] not in ("running", "queued"):
            break
    assert db.one("SELECT status FROM inbox WHERE job_id=:j", j=job)["status"] == "awaiting_approval"
    await runtime.shutdown()

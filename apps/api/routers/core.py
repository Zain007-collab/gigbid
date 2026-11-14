"""All HTTP routes. Grouped by tag (auth, jobs, approvals, runs, pipeline, messages, analytics, settings)."""
from __future__ import annotations

import asyncio
import json
import statistics
from collections import Counter

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from agent import llm, mcp_client
from agent.graphs.bid_graph import thread_id as bid_tid
from agent.graphs.conversation_graph import conv_thread_id
from agent.memory import store as memory
from agent.settings import section
from apps.api import db, runtime
from apps.api.auth.users import COOKIE, SESSION_TTL_S, CurrentUser, create_user, make_token, verify_password
from apps.api.schemas.models import (
    Accepted, Analytics, ApprovalIn, ApprovalSummary, Conversation, Health, InboxItem, JobDetail, LoginIn, Me,
    MemoryItem, OnboardingIn, PipelineCard, RegenerateIn, RegenerateOut, RegisterIn, Settings,
)
from worker import tasks

router = APIRouter()


def _me(u: dict) -> Me:
    return Me(id=u["id"], email=u["email"], display_name=u.get("display_name"), persona=u.get("persona"),
              onboarded=bool(u["onboarded"]), settings=Settings(**{**db.DEFAULT_SETTINGS, **u["settings"]}))


def _dump(v):
    if v is None:
        return None
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    return json.loads(json.dumps(v, default=str))


# ====================================================================== auth + onboarding
@router.post("/auth/register", response_model=Me, tags=["auth"])
def register(body: RegisterIn, response: Response):
    if db.one("SELECT id FROM users WHERE email=:e", e=body.email.lower()):
        raise HTTPException(409, "email already registered")
    uid = create_user(body.email, body.password, body.display_name)
    response.set_cookie(COOKIE, make_token(uid), httponly=True, samesite="lax", max_age=SESSION_TTL_S)
    u = db.one("SELECT * FROM users WHERE id=:u", u=uid)
    u["settings"] = json.loads(u.pop("settings_json"))
    return _me(u)


@router.post("/auth/login", response_model=Me, tags=["auth"])
def login(body: LoginIn, response: Response):
    u = db.one("SELECT * FROM users WHERE email=:e", e=body.email.lower())
    if not u or not verify_password(body.password, u["password_hash"]):
        raise HTTPException(401, "wrong email or password")
    response.set_cookie(COOKIE, make_token(u["id"]), httponly=True, samesite="lax", max_age=SESSION_TTL_S)
    u["settings"] = json.loads(u.pop("settings_json"))
    return _me(u)


@router.post("/auth/logout", response_model=Accepted, tags=["auth"])
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return Accepted(status="ok")


@router.get("/auth/me", response_model=Me, tags=["auth"])
def me(user=CurrentUser):
    return _me(user)


@router.post("/auth/onboarding", response_model=Me, tags=["auth"])
def onboarding(body: OnboardingIn, user=CurrentUser):
    s = body.settings.model_dump()
    prof = json.loads((runtime_persona_dir(body.persona) / "profile.json").read_text())
    s["hourly_rate"] = s["hourly_rate"] or prof["hourly_rate"]
    s["min_project"] = s["min_project"] or prof["min_project"]
    db.q("UPDATE users SET persona=:p, settings_json=:s, onboarded=1 WHERE id=:u",
         p=body.persona, s=json.dumps(s), u=user["id"])
    return me(user=db_user(user["id"]))


def runtime_persona_dir(persona: str):
    from pathlib import Path
    return Path(__file__).resolve().parents[3] / "data" / "personas" / persona


def db_user(uid: str) -> dict:
    u = db.one("SELECT * FROM users WHERE id=:u", u=uid)
    u["settings"] = json.loads(u.pop("settings_json"))
    return u


@router.post("/portfolio/upload", response_model=Accepted, tags=["settings"])
async def upload_case_study(request: Request, user=CurrentUser):
    """Add a markdown case study to the user's portfolio and re-index incrementally."""
    body = (await request.body()).decode("utf-8", errors="ignore")[:20000]
    if len(body.strip()) < 50:
        raise HTTPException(422, "case study too short")
    import re
    import uuid
    d = runtime_persona_dir(user["persona"]) / "uploads"
    d.mkdir(exist_ok=True)
    name = f"{uuid.uuid4().hex[:8]}"
    title = re.sub(r"[^\w\s-]", "", body.strip().splitlines()[0])[:80]
    (d / f"{name}.md").write_text(f"---\ntype: case_study\nproject: upload-{name}\n---\n# {title}\n\n{body}")
    from mcp_servers.portfolio.server import reindex
    reindex(user["persona"])
    return Accepted(status="indexed", detail=f"upload-{name}")


# ====================================================================== health (degraded-mode banner)
@router.get("/health", response_model=Health, tags=["runs"])
async def health():
    mode = section("marketplace")["mode"]
    up, detail = True, None
    try:
        await mcp_client.call("pricing", "marketplace.get_connects_balance", {})
    except mcp_client.MarketplaceDown as e:
        up, detail = False, "Marketplace unavailable — new work is queued and will resume automatically."
    except Exception as e:  # noqa: BLE001
        up, detail = False, f"Marketplace check failed: {type(e).__name__}"
    return Health(ok=True, marketplace_up=up, marketplace_mode=mode, llm_online=llm.online("writer"), detail=detail)


# ====================================================================== jobs
@router.get("/jobs", response_model=list[InboxItem], tags=["jobs"])
def inbox(user=CurrentUser, status: str | None = None, action: str | None = None, include_skipped: bool = True):
    rows = db.q("SELECT * FROM inbox WHERE user_id=:u ORDER BY COALESCE(fit, -1) DESC, updated_at DESC", u=user["id"])
    auto_skip = user["settings"].get("autonomy") == "auto_skip_low_fit"
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        if action and r["action"] != action:
            continue
        if (auto_skip or not include_skipped) and r["status"] == "skipped":
            continue
        r["risk_flags"] = json.loads(r["risk_flags"]) if r.get("risk_flags") else []
        out.append(InboxItem(**{k: r.get(k) for k in InboxItem.model_fields}))
    return out


@router.post("/jobs/scout", response_model=Accepted, tags=["jobs"])
async def scout_now(user=CurrentUser):
    res = await tasks.scout({}, user_id=user["id"])
    if res.get("status") == "degraded":
        raise HTTPException(503, "marketplace unavailable")
    return Accepted(status="ok", detail=f"queued {res['queued']} new jobs, skipped {res['duplicates_skipped']} duplicates")


@router.post("/jobs/{job_id}/analyze", response_model=Accepted, tags=["jobs"])
async def analyze(job_id: str, force_bid: bool = False, user=CurrentUser):
    db.upsert_inbox(user["id"], job_id, status="queued")
    r = await tasks.enqueue("analyze_job", f"analyze:{user['id']}:{job_id}:{int(force_bid)}",
                            user_id=user["id"], job_id=job_id, force_bid=force_bid)
    return Accepted(status=r, detail=bid_tid(user["id"], job_id))


@router.post("/jobs/{job_id}/skip", response_model=Accepted, tags=["jobs"])
def skip(job_id: str, user=CurrentUser):
    db.upsert_inbox(user["id"], job_id, status="skipped", summary="skipped by you")
    return Accepted(status="skipped")


@router.get("/jobs/{job_id}", response_model=JobDetail, tags=["jobs"])
async def job_detail(job_id: str, user=CurrentUser):
    tid = bid_tid(user["id"], job_id)
    snap = await runtime.BID.aget_state({"configurable": {"thread_id": tid}})
    v = snap.values or {}
    row = db.one("SELECT status FROM inbox WHERE user_id=:u AND job_id=:j", u=user["id"], j=job_id)
    if not v and not row:
        raise HTTPException(404, "job not analysed yet")
    pending = [i.value for t in snap.tasks for i in t.interrupts]
    return JobDetail(
        job_id=job_id, status=(row or {}).get("status") or v.get("status", "unknown"), job=v.get("job"),
        job_spec=_dump(v.get("job_spec")), risk=_dump(v.get("risk")), fit=_dump(v.get("fit")),
        decision=_dump(v.get("decision")), price=_dump(v.get("price")),
        drafts=[_dump(d) for d in v.get("drafts", [])], critiques=[_dump(c) for c in v.get("critiques", [])],
        compliance=_dump(v.get("compliance")), submission=v.get("submission"),
        attention_reason=v.get("attention_reason"), pending_approval=_dump(pending[0]) if pending else None,
        usage=v.get("usage") or {})


# ====================================================================== approvals
@router.get("/approvals", response_model=list[ApprovalSummary], tags=["approvals"])
async def approvals(user=CurrentUser):
    out = []
    for r in db.q("SELECT job_id, thread_id, title, updated_at FROM inbox WHERE user_id=:u AND status='awaiting_approval'",
                  u=user["id"]):
        out.append(ApprovalSummary(thread_id=r["thread_id"], kind="proposal", job_id=r["job_id"], title=r["title"],
                                   created_at=r["updated_at"]))
    for r in db.q("SELECT thread_id, job_id, updated_at FROM conversations WHERE user_id=:u AND status='awaiting_approval'",
                  u=user["id"]):
        out.append(ApprovalSummary(thread_id=r["thread_id"], kind="message", job_id=r["job_id"], title=None,
                                   created_at=r["updated_at"]))
    return out


async def _decide(kind: str, user: dict, key: str, body: ApprovalIn) -> Accepted:
    import uuid
    tid = bid_tid(user["id"], key) if kind == "proposal" else conv_thread_id(user["id"], key)
    pending = await runtime.pending_approval(tid, "bid" if kind == "proposal" else "conversation")
    if not pending:
        raise HTTPException(409, "nothing is waiting for approval on this thread (already decided?)")
    if body.action == "edit" and not (body.edited_text or "").strip():
        raise HTTPException(422, "edited_text required for edit")
    if body.action == "reject":
        db.q("INSERT INTO rejections VALUES (:u,:t,:r,:c)", u=user["id"], t=tid, r=body.reason or "", c=db.now())
    decision = {**body.model_dump(), "approval_id": f"appr_{uuid.uuid4().hex[:12]}"}
    if kind == "proposal":
        db.upsert_inbox(user["id"], key, status="running")
        r = await tasks.enqueue("resume_bid", f"resume:{tid}:{decision['approval_id']}", user_id=user["id"],
                                job_id=key, decision=decision)
    else:
        db.q("UPDATE conversations SET status='running' WHERE user_id=:u AND thread_id=:t", u=user["id"], t=key)
        r = await tasks.enqueue("converse", f"converse-resume:{tid}:{decision['approval_id']}", user_id=user["id"],
                                thread_id=key, decision=decision)
    return Accepted(status=r, detail=decision["approval_id"])


@router.post("/approvals/proposal/{job_id}", response_model=Accepted, tags=["approvals"])
async def decide_proposal(job_id: str, body: ApprovalIn, user=CurrentUser):
    return await _decide("proposal", user, job_id, body)


@router.post("/approvals/message/{thread_id}", response_model=Accepted, tags=["approvals"])
async def decide_message(thread_id: str, body: ApprovalIn, user=CurrentUser):
    return await _decide("message", user, thread_id, body)


@router.get("/approvals/message/{thread_id}", tags=["approvals"])
async def message_approval(thread_id: str, user=CurrentUser) -> dict:
    p = await runtime.pending_approval(conv_thread_id(user["id"], thread_id), "conversation")
    if not p:
        raise HTTPException(404, "no pending message approval")
    return _dump(p)


@router.post("/proposals/{job_id}/regenerate", response_model=RegenerateOut, tags=["approvals"])
async def regenerate_sentence(job_id: str, body: RegenerateIn, user=CurrentUser):
    """Regenerate one sentence of the pending draft from its evidence (the human then edits/approves)."""
    p = await runtime.pending_approval(bid_tid(user["id"], job_id))
    if not p or body.sentence_index >= len(p["claims"]):
        raise HTTPException(404, "no such sentence in a pending draft")
    claim = p["claims"][body.sentence_index]
    ev = claim.get("evidence") or []
    if llm.online("writer") and ev:
        from pydantic import BaseModel

        class One(BaseModel):
            text: str

        out, _ = llm.structured("writer", One, "Rewrite this one proposal sentence. Keep it fully supported by the "
                                "evidence; no new claims. " + (body.instruction or ""),
                                f"SENTENCE: {claim['text']}\nEVIDENCE: " + " | ".join(e["text"][:400] for e in ev))
        if out:
            return RegenerateOut(text=out.text, evidence_ids=claim["evidence_ids"])
    import re
    if ev:
        t = re.search(r"^#\s*(.+)$", ev[0]["text"], re.M)
        res = re.search(r"We (.+?)\.", ev[0]["text"])
        if t and res:
            return RegenerateOut(text=f"A relevant example: “{t.group(1)}”, where I {res.group(1)}.",
                                 evidence_ids=claim["evidence_ids"])
    return RegenerateOut(text=claim["text"], evidence_ids=claim.get("evidence_ids", []))


# ====================================================================== runs: streaming, trace, history, replay
@router.get("/runs/{thread_id}/stream", tags=["runs"])
async def stream(thread_id: str, request: Request, user=CurrentUser):
    if thread_id.split(":")[1:2] != [user["id"]]:
        raise HTTPException(403, "not your thread")

    async def gen():
        for past in await runtime.BUS.history(thread_id):
            yield f"data: {past}\n\n"
        async for ev in runtime.BUS.subscribe(thread_id):
            if await request.is_disconnected():
                break
            yield f"data: {ev}\n\n" if ev else ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{thread_id}/trace", tags=["runs"])
async def trace(thread_id: str, user=CurrentUser) -> dict:
    if thread_id.split(":")[1:2] != [user["id"]]:
        raise HTTPException(403, "not your thread")
    graph = runtime.BID if thread_id.startswith("bid:") else runtime.CONV
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    runs = db.q("SELECT run_id, kind, status, trace_id, started_at, ended_at, latency_s, tokens, usd, error FROM runs "
                "WHERE user_id=:u AND thread_id=:t ORDER BY started_at", u=user["id"], t=thread_id)
    v = snap.values or {}
    return {"thread_id": thread_id, "runs": runs, "trace": v.get("trace", []), "usage": v.get("usage", {}),
            "errors": v.get("errors", []), "status": v.get("status")}


@router.get("/runs/{thread_id}/history", tags=["runs"])
async def history(thread_id: str, user=CurrentUser) -> list[dict]:
    if thread_id.split(":")[1:2] != [user["id"]]:
        raise HTTPException(403, "not your thread")
    graph = runtime.BID if thread_id.startswith("bid:") else runtime.CONV
    out = []
    async for s in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
        out.append({"checkpoint_id": s.config["configurable"]["checkpoint_id"], "next": list(s.next),
                    "created_at": s.created_at, "step": (s.metadata or {}).get("step"),
                    "status": (s.values or {}).get("status")})
    return out


@router.post("/runs/{thread_id}/replay/{checkpoint_id}", response_model=Accepted, tags=["runs"])
async def replay(thread_id: str, checkpoint_id: str, user=CurrentUser):
    """Re-run a thread from any checkpoint (time travel). Writes stay behind the approval interrupt."""
    if thread_id.split(":")[1:2] != [user["id"]] or not thread_id.startswith("bid:"):
        raise HTTPException(403, "not your thread")
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}

    async def go():
        mcp_client.current_user.set(user["id"])
        mcp_client.current_persona.set(user["persona"] or "ai_ml")
        async for chunk in runtime.BID.astream(None, cfg, stream_mode="updates"):
            for node_name in chunk:
                await runtime.BUS.publish(thread_id, {"type": "node", "node": node_name, "replay": True})
        await runtime.BUS.publish(thread_id, {"type": "run_finished", "status": "replayed"})
    asyncio.create_task(go())
    return Accepted(status="replaying", detail=checkpoint_id)


# ====================================================================== pipeline
@router.get("/pipeline", response_model=list[PipelineCard], tags=["pipeline"])
async def pipeline(user=CurrentUser):
    mcp_client.current_user.set(user["id"])
    try:
        props = (await mcp_client.call("pipeline", "marketplace.list_proposals", {}))["proposals"]
    except mcp_client.MarketplaceDown:
        raise HTTPException(503, "marketplace unavailable")
    titles = {r["job_id"]: r["title"] for r in db.q("SELECT job_id, title FROM inbox WHERE user_id=:u", u=user["id"])}
    cards = [PipelineCard(job_id=p["job_id"], title=titles.get(p["job_id"]), proposal_id=p["proposal_id"],
                          thread_id=p.get("thread_id"), connects=p.get("connects_spent"), bid=p.get("bid_amount"),
                          column=p["status"] if p["status"] in ("submitted", "viewed", "replied", "interview", "hired", "lost")
                          else "submitted") for p in props]
    submitted = {c.job_id for c in cards}
    for r in db.q("SELECT job_id, title, connects, price FROM inbox WHERE user_id=:u AND status='awaiting_approval'",
                  u=user["id"]):
        if r["job_id"] not in submitted:
            cards.append(PipelineCard(job_id=r["job_id"], title=r["title"], column="drafted", connects=r["connects"],
                                      bid=r["price"]))
    return cards


# ====================================================================== messages
@router.get("/messages", response_model=list[Conversation], tags=["messages"])
def conversations(user=CurrentUser):
    return [Conversation(**r) for r in db.q(
        "SELECT thread_id, job_id, status, intent, last_message, last_message_at FROM conversations WHERE user_id=:u "
        "ORDER BY last_message_at DESC", u=user["id"])]


@router.post("/messages/sync", response_model=Accepted, tags=["messages"])
async def sync_messages(user=CurrentUser):
    res = await tasks.sync_pipeline({})
    return Accepted(status="ok", detail=f"{res['conversations_started']} new client replies")


@router.get("/messages/{thread_id}", tags=["messages"])
async def thread_messages(thread_id: str, user=CurrentUser) -> list[dict]:
    mcp_client.current_user.set(user["id"])
    try:
        return (await mcp_client.call("liaison", "marketplace.list_messages", {"thread_id": thread_id}))["messages"]
    except mcp_client.MarketplaceDown:
        raise HTTPException(503, "marketplace unavailable")


# ====================================================================== analytics
@router.get("/analytics", response_model=Analytics, tags=["analytics"])
async def analytics(user=CurrentUser):
    mcp_client.current_user.set(user["id"])
    try:
        props = (await mcp_client.call("pipeline", "marketplace.list_proposals", {}))["proposals"]
    except mcp_client.MarketplaceDown:
        props = []
    cat = {r["job_id"]: r["category"] for r in db.q("SELECT job_id, category FROM inbox WHERE user_id=:u", u=user["id"])}
    by: dict[str, Counter] = {}
    for p in props:
        c = by.setdefault(cat.get(p["job_id"]) or "other", Counter())
        c["proposals"] += 1
        c["connects"] += p.get("connects_spent") or 0
        c["replies"] += p["status"] in ("replied", "interview", "hired")
        c["hires"] += p["status"] == "hired"
    by_cat = [{"category": k, **v, "reply_rate": round(v["replies"] / v["proposals"], 3) if v["proposals"] else 0,
               "hire_rate": round(v["hires"] / v["proposals"], 3) if v["proposals"] else 0} for k, v in by.items()]
    runs = db.q("SELECT kind, latency_s, usd, thread_id FROM runs WHERE user_id=:u AND latency_s IS NOT NULL", u=user["id"])
    job_runs = [r for r in runs if r["kind"] == "bid"]
    prop_threads = {f"bid:{user['id']}:{p['job_id']}" for p in props}
    prop_cost = [sum(r["usd"] or 0 for r in job_runs if r["thread_id"] == t) for t in prop_threads]
    lat = sorted(r["latency_s"] for r in job_runs)
    pct = lambda xs, p: round(xs[int(p * (len(xs) - 1))], 3) if xs else None  # noqa: E731
    rej = Counter((r["reason"] or "no reason").strip().lower()[:60]
                  for r in db.q("SELECT reason FROM rejections WHERE user_id=:u", u=user["id"]))
    inbox_rows = db.q("SELECT status FROM inbox WHERE user_id=:u", u=user["id"])
    return Analytics(
        by_category=by_cat, connects_spent=sum(p.get("connects_spent") or 0 for p in props), proposals=len(props),
        replies=sum(p["status"] in ("replied", "interview", "hired") for p in props),
        hires=sum(p["status"] == "hired" for p in props),
        cost_per_proposal_usd=round(statistics.mean(prop_cost), 6) if prop_cost else None,
        p50_latency_s=pct(lat, 0.5), p95_latency_s=pct(lat, 0.95), p95_latency_job_s=pct(lat, 0.95),
        cost_per_job_usd=round(statistics.mean(r["usd"] or 0 for r in job_runs), 6) if job_runs else None,
        top_rejection_reasons=[{"reason": k, "count": v} for k, v in rej.most_common(5)],
        funnel={"seen": len(inbox_rows), "analysed": sum(r["status"] != "queued" for r in inbox_rows),
                "recommended_bid": sum(r["status"] in ("awaiting_approval", "submitted") for r in inbox_rows),
                "submitted": len(props),
                "viewed": sum(p["status"] in ("viewed", "replied", "interview", "hired") for p in props),
                "replied": sum(p["status"] in ("replied", "interview", "hired") for p in props),
                "hired": sum(p["status"] == "hired" for p in props)})


# ====================================================================== settings + memory viewer
@router.get("/settings", response_model=Settings, tags=["settings"])
def get_settings(user=CurrentUser):
    return Settings(**{**db.DEFAULT_SETTINGS, **user["settings"]})


@router.put("/settings", response_model=Settings, tags=["settings"])
def put_settings(body: Settings, user=CurrentUser):
    db.q("UPDATE users SET settings_json=:s WHERE id=:u", s=json.dumps(body.model_dump()), u=user["id"])
    return body


@router.get("/memory", response_model=list[MemoryItem], tags=["settings"])
async def list_memory(user=CurrentUser):
    return [MemoryItem(namespace=m.pop("namespace"), key=m.pop("key"), data=m) for m in await memory.list_memories(user["id"])]


@router.delete("/memory/{namespace}/{key}", response_model=Accepted, tags=["settings"])
async def delete_memory(namespace: str, key: str, user=CurrentUser):
    ns = namespace.split(".")
    if not await memory.delete_memory(user["id"], ns, key):
        raise HTTPException(403, "cannot delete that memory")
    return Accepted(status="deleted")

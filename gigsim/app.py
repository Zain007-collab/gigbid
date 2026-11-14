"""GigSim — a self-contained freelance marketplace simulator for GigBid.

No official simulator was supplied, so this one is built from the seeded
datasets in gigsim/seed (see datagen/ and docs/DATA_CARD.md). It provides:

  * jobs released over simulated time (with duplicates, scams, planted injections)
  * clients with public histories and HIDDEN preferences (never returned by the API)
  * Connects balances, proposal drafts, submission, messages, threads
  * per-key rate limits (429 + Retry-After) and random 5xx errors (chaos)
  * a simulated client that decides view / reply / interview / hire
  * a versioned platform policy (the change request is policy v2)

Run:  uvicorn gigsim.app:app --port 8080
Env:  GIGSIM_TIME_SCALE (sim minutes per real second, default 1), GIGSIM_ERROR_RATE (default 0.02),
      GIGSIM_RPS / GIGSIM_BURST (rate limit), GIGSIM_POLICY_VERSION (1 or 2), GIGSIM_SEED
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from datagen.catalog import PARAPHRASE, display  # same vocabulary the jobs were written in

SEED_DIR = Path(os.environ.get("GIGSIM_SEED_DIR", Path(__file__).with_name("seed")))
TIME_SCALE = float(os.environ.get("GIGSIM_TIME_SCALE", "1"))
ERROR_RATE = float(os.environ.get("GIGSIM_ERROR_RATE", "0.02"))
RPS = float(os.environ.get("GIGSIM_RPS", "5"))
BURST = int(os.environ.get("GIGSIM_BURST", "10"))
SEED = int(os.environ.get("GIGSIM_SEED", "7"))
INITIAL_CONNECTS = int(os.environ.get("GIGSIM_INITIAL_CONNECTS", "200"))
SIM_EPOCH = datetime(2026, 11, 1, tzinfo=timezone.utc)

POLICIES = {
    1: {"version": 1, "effective": "2026-10-27", "summary": "Base rules.",
        "connects": {"extra_per_proposal": 0, "high_budget_threshold": None, "high_budget_extra": 0},
        "max_proposals_per_client_per_week": None},
    2: {"version": 2, "effective": "2026-11-02",
        "summary": "Platform policy update: proposals on jobs with budget >= $1000 cost 2 extra Connects; "
                   "at most 2 proposals to the same client per 7 days.",
        "connects": {"extra_per_proposal": 0, "high_budget_threshold": 1000, "high_budget_extra": 2},
        "max_proposals_per_client_per_week": 2},
}

GENERIC = re.compile(r"(dear (sir|madam)|i am (very )?excited|perfect fit|passionate|greetings|"
                     r"i read your job post|i can do this job|to whom it may concern)", re.I)
CONTACT = re.compile(r"(@[a-z0-9.-]+\.[a-z]{2,}|\+?\d[\d\s-]{8,}\d|whatsapp|telegram|skype)", re.I)
GUARANTEE = re.compile(r"\b(guarantee|100% (sure|success)|risk[- ]free)\b", re.I)


# ====================================================================== state
class Sim:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with open(SEED_DIR / "jobs.json") as f:
            self.jobs: dict[str, dict] = {j["job_id"]: j for j in json.load(f)}
        with open(SEED_DIR / "clients.json") as f:
            self.clients: dict[str, dict] = {c["client_id"]: c for c in json.load(f)}
        with open(SEED_DIR / "hidden_prefs.json") as f:
            self.hidden: dict[str, dict] = json.load(f)
        self.started = time.time()
        self.tick_min = 0.0
        self.policy_version = int(os.environ.get("GIGSIM_POLICY_VERSION", "1"))
        self.connects: dict[str, int] = {}
        self.drafts: dict[str, dict] = {}
        self.proposals: dict[str, dict] = {}
        self.threads: dict[str, list[dict]] = {}
        self.idem: dict[tuple[str, str], dict] = {}
        self.events: list[tuple[datetime, str, dict]] = []
        self.buckets: dict[str, list[float]] = {}
        self.error_rate = ERROR_RATE
        self.down = False
        self.n = 0

    # ------------------------------------------------------------------ time
    def now(self) -> datetime:
        # TIME_SCALE simulated minutes pass per real second, plus manual /admin/tick advances
        return SIM_EPOCH + timedelta(minutes=(time.time() - self.started) * TIME_SCALE + self.tick_min)

    def next_id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n:05d}"

    def released(self, job: dict) -> bool:
        off = job.get("release_offset_min")
        return off is None or SIM_EPOCH + timedelta(minutes=off) <= self.now()

    def process_events(self) -> None:
        now = self.now()
        due = [e for e in self.events if e[0] <= now]
        self.events = [e for e in self.events if e[0] > now]
        for _, kind, data in sorted(due, key=lambda e: e[0]):
            p = self.proposals[data["proposal_id"]]
            if kind == "viewed" and p["status"] == "submitted":
                p["status"] = "viewed"
            elif kind == "reply":
                p["status"] = data["status"]
                self.threads.setdefault(p["thread_id"], []).append({
                    "message_id": self.next_id("m"), "thread_id": p["thread_id"], "sender": "client",
                    "body": data["body"], "sent_at": now.isoformat(), "proposal_id": p["proposal_id"],
                    "job_id": p["job_id"]})
            elif kind == "final":
                p["status"] = data["status"]

    def policy(self) -> dict:
        return POLICIES[self.policy_version]

    def connects_for(self, job: dict) -> int:
        pol = self.policy()["connects"]
        cost = job["connects_required"] + pol["extra_per_proposal"]
        if pol["high_budget_threshold"] and (job.get("budget_max") or 0) >= pol["high_budget_threshold"]:
            cost += pol["high_budget_extra"]
        return cost


SIM = Sim()
app = FastAPI(title="GigSim", version="1.0",
              description="Synthetic freelance marketplace simulator for GigBid (all data synthetic).")


# ====================================================================== middleware: chaos + rate limit
@app.middleware("http")
async def chaos_and_limits(request: Request, call_next):
    # path relative to wherever GigSim is mounted (it may run under /gigsim inside the API)
    path, root = request.url.path, request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    if path.startswith("/admin"):
        token = os.environ.get("GIGSIM_ADMIN_TOKEN")
        if token and request.headers.get("x-admin-token") != token:
            return JSONResponse({"error": "admin token required"}, status_code=403)
        return await call_next(request)
    if path in ("/health", "/docs", "/openapi.json"):
        return await call_next(request)
    if SIM.down:
        return JSONResponse({"error": "service unavailable"}, status_code=503)
    key = request.headers.get("authorization", "anon")
    with SIM.lock:
        now = time.monotonic()
        tokens, last = SIM.buckets.get(key, [BURST, now])
        tokens = min(BURST, tokens + (now - last) * RPS)
        if tokens < 1:
            SIM.buckets[key] = [tokens, now]
            return JSONResponse({"error": "rate limited"}, status_code=429,
                                headers={"Retry-After": f"{(1 - tokens) / RPS:.2f}"})
        SIM.buckets[key] = [tokens - 1, now]
    if random.random() < SIM.error_rate:
        return JSONResponse({"error": "transient upstream error"}, status_code=random.choice([500, 502, 503]))
    with SIM.lock:
        SIM.process_events()
    return await call_next(request)


# ====================================================================== read API
def client_public(cid: str) -> dict:
    c = SIM.clients.get(cid)
    if not c:
        raise HTTPException(404, "client not found")
    return c


def job_public(j: dict) -> dict:
    out = {k: v for k, v in j.items() if k not in ("release_offset_min",)}
    out["connects_required"] = SIM.connects_for(j)
    if j.get("release_offset_min") is not None:
        out["posted_at"] = (SIM_EPOCH + timedelta(minutes=j["release_offset_min"])).isoformat()
    out["client"] = {k: client_public(j["client_id"])[k] for k in
                     ("client_id", "country", "payment_verified", "total_spent", "hire_rate", "rating")}
    return out


@app.get("/health")
def health():
    return {"ok": not SIM.down, "sim_now": SIM.now().isoformat(), "policy_version": SIM.policy_version}


@app.get("/policy")
def policy():
    return SIM.policy()


_STATS = json.loads((SEED_DIR / "market_stats.json").read_text())


@app.get("/market/stats/{category}")
def market_stats(category: str):
    s = _STATS.get(category)
    if not s:
        raise HTTPException(404, "unknown category")
    return {"category": category, "fixed": s["fixed"], "hourly": s["hourly"], "source": s["source"]}


@app.get("/jobs")
def search_jobs(q: str = "", skills: str = "", limit: int = 20, min_budget: float | None = None,
                posted_after: str | None = None, category: str | None = None):
    limit = max(1, min(limit, 50))
    want = [s.strip().lower() for s in skills.split(",") if s.strip()]
    terms = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 2]
    after = datetime.fromisoformat(posted_after) if posted_after else None
    out = []
    for j in SIM.jobs.values():
        if not SIM.released(j):
            continue
        pub = job_public(j)
        if after and datetime.fromisoformat(pub["posted_at"]) <= after:
            continue
        if category and j.get("category") != category:
            continue
        if min_budget is not None and (j.get("budget_max") or 0) < min_budget:
            continue
        text = (j["title"] + " " + j["description"] + " " + " ".join(j["skills"])).lower()
        if want and not any(w in text for w in want):
            continue
        if terms and not any(t in text for t in terms):
            continue
        out.append(pub)
    out.sort(key=lambda x: x["posted_at"], reverse=True)
    return {"items": [{k: v for k, v in o.items() if k in (
        "job_id", "title", "skills", "budget_type", "budget_min", "budget_max", "connects_required",
        "posted_at", "client_id", "category")} for o in out[:limit]], "total": len(out)}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = SIM.jobs.get(job_id)
    if not j or not SIM.released(j):
        raise HTTPException(404, "job not found")
    return job_public(j)


@app.get("/clients/{client_id}")
def get_client(client_id: str):
    return client_public(client_id)


@app.get("/freelancers/{user_id}/connects")
def get_connects(user_id: str):
    with SIM.lock:
        return {"available": SIM.connects.setdefault(user_id, INITIAL_CONNECTS)}


@app.get("/freelancers/{user_id}/messages")
def list_messages(user_id: str, thread_id: str | None = None, since: str | None = None):
    mine = {p["thread_id"] for p in SIM.proposals.values() if p["user_id"] == user_id}
    t_since = datetime.fromisoformat(since) if since else None
    items = []
    for tid, msgs in SIM.threads.items():
        if tid not in mine or (thread_id and tid != thread_id):
            continue
        items += [m for m in msgs if not t_since or datetime.fromisoformat(m["sent_at"]) > t_since]
    return {"items": sorted(items, key=lambda m: m["sent_at"])}


@app.get("/freelancers/{user_id}/proposals")
def list_proposals(user_id: str):
    return {"items": [{k: v for k, v in p.items() if not k.startswith("_")}
                      for p in SIM.proposals.values() if p["user_id"] == user_id]}


@app.get("/freelancers/{user_id}/idempotency/{key}")
def lookup_idempotency(user_id: str, key: str):
    r = SIM.idem.get((user_id, key))
    if r is None:
        raise HTTPException(404, "unknown key")
    return r


# ====================================================================== write API
class ScreeningAnswer(BaseModel):
    question: str
    answer: str


class DraftIn(BaseModel):
    job_id: str
    cover_letter: str = Field(min_length=1, max_length=5000)
    rate_type: str
    bid_amount: float = Field(gt=0)
    screening_answers: list[ScreeningAnswer] = []


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


def _idem(user_id: str, key: str | None) -> dict | None:
    return SIM.idem.get((user_id, key)) if key else None


@app.post("/freelancers/{user_id}/proposals/drafts")
def create_draft(user_id: str, body: DraftIn, idempotency_key: str | None = Header(None)):
    with SIM.lock:
        if (prior := _idem(user_id, idempotency_key)) is not None:
            return prior
        if body.job_id not in SIM.jobs:
            raise HTTPException(404, "job not found")
        did = SIM.next_id("d")
        SIM.drafts[did] = {"draft_id": did, "user_id": user_id, **body.model_dump()}
        res = {"draft_id": did, "job_id": body.job_id}
        if idempotency_key:
            SIM.idem[(user_id, idempotency_key)] = res
        return res


@app.post("/freelancers/{user_id}/proposals/drafts/{draft_id}/submit")
def submit(user_id: str, draft_id: str, idempotency_key: str | None = Header(None)):
    with SIM.lock:
        if (prior := _idem(user_id, idempotency_key)) is not None:
            return prior
        d = SIM.drafts.get(draft_id)
        if not d or d["user_id"] != user_id:
            raise HTTPException(404, "draft not found")
        if any(p["draft_id"] == draft_id for p in SIM.proposals.values()):
            raise HTTPException(409, "draft already submitted")
        job = SIM.jobs[d["job_id"]]
        pol = SIM.policy()
        if cap := pol["max_proposals_per_client_per_week"]:
            week_ago = SIM.now() - timedelta(days=7)
            recent = [p for p in SIM.proposals.values() if p["user_id"] == user_id
                      and p["client_id"] == job["client_id"] and datetime.fromisoformat(p["submitted_at"]) > week_ago]
            if len(recent) >= cap:
                raise HTTPException(422, f"policy v{pol['version']}: max {cap} proposals per client per 7 days")
        cost = SIM.connects_for(job)
        bal = SIM.connects.setdefault(user_id, INITIAL_CONNECTS)
        if bal < cost:
            raise HTTPException(402, f"not enough Connects ({bal} < {cost})")
        SIM.connects[user_id] = bal - cost
        pid = SIM.next_id("p")
        prop = {"proposal_id": pid, "draft_id": draft_id, "user_id": user_id, "job_id": job["job_id"],
                "client_id": job["client_id"], "thread_id": f"t-{pid}", "status": "submitted",
                "connects_spent": cost, "bid_amount": d["bid_amount"], "submitted_at": SIM.now().isoformat()}
        SIM.proposals[pid] = prop
        schedule_client_response(prop, d, job)
        res = {"proposal_id": pid, "job_id": job["job_id"], "connects_spent": cost, "status": "submitted"}
        if idempotency_key:
            SIM.idem[(user_id, idempotency_key)] = res
        return res


@app.post("/freelancers/{user_id}/threads/{thread_id}/messages")
def send_message(user_id: str, thread_id: str, body: MessageIn, idempotency_key: str | None = Header(None)):
    with SIM.lock:
        if (prior := _idem(user_id, idempotency_key)) is not None:
            return prior
        prop = next((p for p in SIM.proposals.values() if p["thread_id"] == thread_id and p["user_id"] == user_id), None)
        if prop is None:
            raise HTTPException(404, "thread not found")
        mid = SIM.next_id("m")
        SIM.threads.setdefault(thread_id, []).append({
            "message_id": mid, "thread_id": thread_id, "sender": "freelancer", "body": body.body,
            "sent_at": SIM.now().isoformat(), "proposal_id": prop["proposal_id"], "job_id": prop["job_id"]})
        schedule_followup(prop, body.body)
        res = {"message_id": mid, "thread_id": thread_id, "status": "sent"}
        if idempotency_key:
            SIM.idem[(user_id, idempotency_key)] = res
        return res


# ====================================================================== simulated client
def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256(("|".join(parts) + str(SEED)).encode()).hexdigest()[:12], 16))


def score_proposal(draft: dict, job: dict, prefs: dict) -> dict:
    """Transparent client model. Returns the features and the reply probability."""
    text = draft["cover_letter"]
    low = text.lower()
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    must = prefs["must_have_skills"]

    def mentions(skill: str) -> bool:
        return any(v.lower() in low for v in [display(skill), skill, *PARAPHRASE.get(skill, [])])
    coverage = sum(mentions(s) for s in must) / max(1, len(must))
    generic = bool(GENERIC.search(first))
    specific = len(re.findall(r"\d+(\.\d+)?\s?(%|x|ms|s\b|seconds|minutes|hours|k\b|,000)", low))
    title_terms = {t for t in re.findall(r"[a-z]{4,}", job["title"].lower())}
    personal = len(title_terms & set(re.findall(r"[a-z]{4,}", low))) / max(1, len(title_terms))
    words = len(text.split())
    price_ok = draft["bid_amount"] <= prefs["budget_ceiling"] if prefs["budget_ceiling"] > 0 else False
    violation = bool(CONTACT.search(text) or GUARANTEE.search(text))
    z = (-1.6 + 2.0 * coverage + 0.35 * min(specific, 3) + 1.0 * personal + 0.8 * price_ok
         - (1.4 if generic and prefs["dislikes_generic_opening"] else 0.3 if generic else 0)
         - (0.8 if words < 60 or words > 400 else 0) - (1.5 if violation else 0))
    p = prefs["responsiveness"] / (1 + math.exp(-z))
    return {"coverage": round(coverage, 2), "generic_opening": generic, "specific_metrics": specific,
            "personalisation": round(personal, 2), "words": words, "price_ok": price_ok,
            "violation": violation, "p_reply": round(p, 3)}


def schedule_client_response(prop: dict, draft: dict, job: dict) -> None:
    prefs = SIM.hidden.get(job["job_id"]) or {"must_have_skills": [], "budget_ceiling": job.get("budget_max") or 0,
                                              "dislikes_generic_opening": True, "responsiveness": 0.7,
                                              "is_scammer": False}
    s = score_proposal(draft, job, prefs)
    prop["_score"] = s
    rng = _rng(prop["proposal_id"], job["job_id"])
    t0 = SIM.now()
    if rng.random() < 0.85:
        SIM.events.append((t0 + timedelta(hours=rng.uniform(1, 8)), "viewed", {"proposal_id": prop["proposal_id"]}))
    if prefs.get("is_scammer"):
        SIM.events.append((t0 + timedelta(hours=rng.uniform(1, 4)), "reply", {
            "proposal_id": prop["proposal_id"], "status": "replied",
            "body": "Thanks! Let's continue on Telegram @hr_desk_2026, I'll send the test task and payment there."}))
        return
    if rng.random() < s["p_reply"]:
        hours = rng.uniform(4, 30)
        if rng.random() < 0.5:
            body = rng.choice([
                f"Thanks for the proposal. Have you used {display_first(prefs)} in production before?",
                "Can you walk me through how you would structure this in the first week?",
                f"Your bid of ${draft['bid_amount']:.0f} is a bit high for us — could you do it for "
                f"${draft['bid_amount'] * 0.85:.0f}?",
            ])
            SIM.events.append((t0 + timedelta(hours=hours), "reply", {
                "proposal_id": prop["proposal_id"], "status": "replied", "body": body}))
        else:
            SIM.events.append((t0 + timedelta(hours=hours), "reply", {
                "proposal_id": prop["proposal_id"], "status": "interview",
                "body": "Your proposal stood out. Are you free for a 30-minute call this week? Please suggest two slots."}))
            if rng.random() < (0.45 if s["price_ok"] else 0.15):
                SIM.events.append((t0 + timedelta(hours=hours + rng.uniform(24, 72)), "final",
                                   {"proposal_id": prop["proposal_id"], "status": "hired"}))
    else:
        SIM.events.append((t0 + timedelta(days=rng.uniform(3, 7)), "final",
                           {"proposal_id": prop["proposal_id"], "status": "lost"}))


def display_first(prefs: dict) -> str:
    return display(prefs["must_have_skills"][0]) if prefs["must_have_skills"] else "this stack"


def schedule_followup(prop: dict, body: str) -> None:
    rng = _rng(prop["proposal_id"], body[:40])
    if prop["status"] in ("replied", "interview") and rng.random() < 0.6:
        has_slot = bool(re.search(r"\b(mon|tue|wed|thu|fri)\w*\b.*\d", body, re.I))
        reply = ("Great, the first slot works. Talk then." if has_slot
                 else "Thanks, that answers my question. Could you suggest a time for a quick call?")
        status = "interview" if has_slot or prop["status"] == "interview" else "replied"
        SIM.events.append((SIM.now() + timedelta(hours=rng.uniform(2, 12)), "reply",
                           {"proposal_id": prop["proposal_id"], "status": status, "body": reply}))


# ====================================================================== admin (evaluation + chaos)
class Chaos(BaseModel):
    error_rate: float | None = Field(None, ge=0, le=1)
    down: bool | None = None


@app.post("/admin/tick")
def tick(minutes: float = 60):
    with SIM.lock:
        SIM.tick_min += minutes
        SIM.process_events()
        return {"sim_now": SIM.now().isoformat()}


@app.post("/admin/chaos")
def chaos(c: Chaos):
    if c.error_rate is not None:
        SIM.error_rate = c.error_rate
    if c.down is not None:
        SIM.down = c.down
    return {"error_rate": SIM.error_rate, "down": SIM.down}


@app.post("/admin/policy/{version}")
def set_policy(version: int):
    if version not in POLICIES:
        raise HTTPException(404, "unknown policy version")
    SIM.policy_version = version
    return SIM.policy()


@app.post("/admin/reset")
def reset():
    with SIM.lock:
        SIM.reset()
    return {"ok": True}


@app.get("/admin/proposals/{proposal_id}/score")
def proposal_score(proposal_id: str):
    """Evaluation only: the client model's features for one proposal."""
    p = SIM.proposals.get(proposal_id)
    if not p:
        raise HTTPException(404, "proposal not found")
    return {"status": p["status"], **p.get("_score", {})}

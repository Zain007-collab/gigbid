"""Decision, Pricing Strategist, Proposal Writer, claim verification (B3), Critic, Compliance Guard."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent import llm, prompting
from agent.handoffs import (
    BidDecision, Claim, ComplianceVerdict, ComplianceViolation, Critique, PriceQuote, ProposalDraft,
    ScreeningAnswer,
)
from agent.memory import store as memory
from agent.nodes.base import node, tool
from agent.policy import connects_cost
from agent.settings import section

CONNECT_USD = 0.15     # marketplace price of one Connect


def persona_profile(persona: str) -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "personas" / persona / "profile.json"
    return json.loads(p.read_text()) if p.exists() else {"hourly_rate": 30, "min_project": 200}


# ====================================================================== decision
def decide_rule(fit: float, risk, job: dict, profile: dict) -> BidDecision:
    t = section("thresholds")
    reasons: list[str] = []
    if risk.risk_score >= t["scam_block"]:
        kinds = ", ".join(sorted({f.kind for f in risk.flags if f.severity >= t["scam_block"]}))
        return BidDecision(job_id=job["job_id"], action="skip", reasons=[f"high risk ({risk.risk_score:.2f}): {kinds}"])
    budget_ok = ((job.get("budget_max") or 0) >= profile["min_project"]) if job.get("budget_type") == "fixed" \
        else (job.get("budget_max") or 0) >= profile["hourly_rate"] * 0.8
    if not budget_ok:
        reasons.append(f"budget {job.get('budget_max')} below your floor")
    if risk.risk_score > t["max_risk_to_bid"]:
        reasons.append(f"risk {risk.risk_score:.2f} above {t['max_risk_to_bid']}")
    if fit >= t["bid_min_fit"] and budget_ok and risk.risk_score <= t["max_risk_to_bid"]:
        return BidDecision(job_id=job["job_id"], action="bid", reasons=[f"fit {fit:.2f} ≥ {t['bid_min_fit']}", *reasons])
    if fit >= t["watch_min_fit"] and budget_ok:
        return BidDecision(job_id=job["job_id"], action="watch", reasons=[f"fit {fit:.2f} borderline", *reasons])
    return BidDecision(job_id=job["job_id"], action="skip", reasons=[f"fit {fit:.2f} < {t['watch_min_fit']}", *reasons])


@node("decide")
async def decide(state: dict) -> dict:
    d = decide_rule(state["fit"].fit_score, state["risk"], state["job"], persona_profile(state["persona"]))
    if state.get("force_bid") and d.action != "bid" and state["risk"].risk_score < section("thresholds")["scam_block"]:
        d = BidDecision(job_id=d.job_id, action="bid", reasons=["user chose to draft anyway", *d.reasons])
    status = {"bid": "running", "watch": "watch", "skip": "skipped"}[d.action]
    return {"decision": d, "status": status}


# ====================================================================== pricing
@node("pricing")
async def pricing(state: dict) -> dict:
    job, fit = state["job"], state["fit"]
    prof = persona_profile(state["persona"])
    cat = job.get("category") or "other"
    stats = (await tool("pricing", "marketplace.get_market_stats", {"category": cat}))["stats"]
    bal = (await tool("pricing", "marketplace.get_connects_balance", {}))["balance"]["available"]
    hourly = job.get("budget_type") == "hourly"
    pct = stats["hourly" if hourly else "fixed"]
    lo, hi = job.get("budget_min") or 0, job.get("budget_max") or 0
    n_req = max(1, len(fit.matches))
    if hourly:
        target = max(prof["hourly_rate"], pct["p50"])
        floor = prof["hourly_rate"] * 0.9
    else:
        target = pct["p50"] * (0.7 + 0.12 * n_req)
        floor = prof["min_project"]
    if hi:
        target = min(target, hi)
    target = max(target, lo, floor)
    cost = connects_cost(job.get("connects_required", 0))
    # expected value of bidding: P(hire) ~ fit-scaled base rate; compare with the Connects price
    p_hire = 0.04 + 0.12 * fit.fit_score
    ev = p_hire * target * (1 if not hourly else 20)          # assume ~20 billable hours for hourly jobs
    worth = ev > cost * CONNECT_USD * 5 and bal >= cost
    q = PriceQuote(job_id=job["job_id"], rate_type="hourly" if hourly else "fixed", amount=round(target, 0),
                   market_p25=pct["p25"], market_p50=pct["p50"], market_p75=pct["p75"], user_floor=floor,
                   connects_cost=cost, connects_worth_it=worth,
                   rationale=(f"Market {cat} {'hourly' if hourly else 'fixed'} p25/p50/p75 = "
                              f"{pct['p25']:.0f}/{pct['p50']:.0f}/{pct['p75']:.0f}; client range {lo:.0f}-{hi:.0f}; "
                              f"your floor {floor:.0f} → bid {target:.0f}. Connects {cost} (${cost * CONNECT_USD:.2f}) vs "
                              f"expected value ${ev:.0f}; balance {bal}."))
    return {"price": q}


# ====================================================================== writer
class _Sentence(BaseModel):
    text: str
    evidence_ids: list[str] = []


class _Answer(BaseModel):
    question_id: str
    answer: str
    evidence_ids: list[str] = []


class WriterOut(BaseModel):
    sentences: list[_Sentence] = Field(description="the cover letter, one sentence per item, in order")
    screening_answers: list[_Answer] = []


def evidence_pack(fit) -> list[dict]:
    seen, pack = set(), []
    for m in fit.matches:
        for e in m.evidence:
            if m.supported and e.id not in seen:
                seen.add(e.id)
                pack.append({"id": e.id, "type": e.type, "project": e.project, "requirement": m.requirement,
                             "text": e.text[:500]})
    return pack


@node("writer")
async def writer(state: dict) -> dict:
    job, fit, spec = state["job"], state["fit"], state.get("job_spec")
    revision = state.get("revision", 0)
    pack = evidence_pack(fit)
    style = await memory.style_rules(state["user_id"])
    insights = await memory.outcome_insights(state["user_id"], job.get("category") or "other")
    note = ""
    if state.get("critiques"):
        c = state["critiques"][-1]
        note = "REVISION REQUEST from the critic: " + (c.revision_instructions or "; ".join(c.issues))
    out = None
    if llm.online("writer"):
        sys = prompting.render("writer", max_words=str(section("thresholds")["proposal_max_words"]),
                               style_rules="\n".join(f"- {r}" for r in style) or "- (none yet)",
                               outcome_insights="\n".join(f"- {i}" for i in insights) or "- (not enough data yet)",
                               revision_note=note, untrusted_rules=prompting.load("_untrusted_rules"))
        qs = spec.screening_questions if spec else []
        user = ("EVIDENCE (cite by id):\n" + "\n".join(f"[{e['id']}] ({e['type']}, proves: {e['requirement']}) {e['text']}"
                                                       for e in pack)
                + f"\n\nGAPS (no evidence — do not claim): {', '.join(fit.gaps) or 'none'}"
                + f"\nPRICE: {state['price'].amount:.0f} ({state['price'].rate_type})"
                + "\nSCREENING QUESTIONS:\n" + "\n".join(f"{q.id}: {q.text}" for q in qs)
                + "\n\n" + prompting.untrusted("job_post", f"{job['title']}\n\n{job['description']}"))
        out, _ = llm.structured("writer", WriterOut, sys, user)
    texts: dict[str, str] = {e["id"]: e["text"] for e in pack}
    if out is None:
        out = await template_writer(job, fit, pack, spec, style, texts)
    claims = [Claim(text=s.text.strip(), evidence_ids=s.evidence_ids) for s in out.sentences if s.text.strip()]
    answers = [ScreeningAnswer(question_id=a.question_id, question=_q(spec, a.question_id), answer=a.answer,
                               claims=[Claim(text=a.answer, evidence_ids=a.evidence_ids)])
               for a in out.screening_answers]
    draft = ProposalDraft(job_id=job["job_id"], version=len(state.get("drafts", [])) + 1,
                          cover_letter=" ".join(c.text for c in claims), claims=claims,
                          screening_answers=answers, price=state.get("price"))
    return {"drafts": [draft], "revision": revision, "evidence_texts": texts}


def _q(spec, qid: str) -> str:
    return next((q.text for q in (spec.screening_questions if spec else []) if q.id == qid), qid)


async def template_writer(job: dict, fit, pack: list[dict], spec, style: list[str], texts: dict[str, str]) -> WriterOut:
    """Deterministic writer (offline / fallback). Cites each project's case study and states only its result."""
    sup = [m for m in fit.matches if m.supported]
    need = ", ".join(m.requirement for m in fit.matches[:3])
    s = [_Sentence(text=f"For “{job['title']}”, the core of the work is {need}.")]
    used, projects = set(), set()
    for m in sup:
        if len(projects) >= 3:
            break
        project = next((e.project for e in m.evidence if e.project and e.project not in projects), None)
        if not project:
            continue
        projects.add(project)
        try:
            cs = await tool("writer", "portfolio.get_case_study", {"id": project})
        except Exception:  # noqa: BLE001 - no case study for this project: skip rather than invent
            continue
        title = re.search(r"^#\s*(.+)$", cs["text"], re.M)
        result = re.search(r"We (.+?)\.", cs["text"])
        if title and result:
            texts[cs["id"]] = cs["text"]
            used.add(cs["id"])
            s.append(_Sentence(text=f"On “{title.group(1).strip()}” I {result.group(1).strip()}, which maps to your need for "
                                    f"{m.requirement}.", evidence_ids=[cs["id"]]))
    if fit.gaps:
        s.append(_Sentence(text=f"For {fit.gaps[0]}, I would start with a short spike and confirm the approach with you before building."))
    s.append(_Sentence(text="Could you share what a successful first week looks like for you? I can send a one-page plan."))
    answers = [_Answer(question_id=q.id, answer=f"I would answer this from the projects above ({', '.join(sorted(used)) or 'see portfolio'}).",
                       evidence_ids=sorted(used)) for q in (spec.screening_questions if spec else [])]
    out = WriterOut(sentences=s, screening_answers=answers)
    for rule in style:  # apply simple learned style rules deterministically
        if rule.startswith("never say "):
            word = rule.removeprefix("never say ").strip('"“” ')
            for x in out.sentences:
                x.text = re.sub(rf"\b{re.escape(word)}\b", "", x.text, flags=re.I).replace("  ", " ")
    return out


# ====================================================================== claim verification (B3)
EXPERIENCE = re.compile(r"\b(I|I've|I have|my|we)\b.*\b(built|shipped|delivered|led|designed|developed|created|reduced|"
                        r"cut|raised|improved|increased|saved|deployed|implemented|worked|used|years?|experience|"
                        r"expert|specialist)\b|\d", re.I)
STOP = set("the a an and or of to in on for with by at from as is was were be been this that it i my we our you your "
           "have has had using used".split())


class _Verdict(BaseModel):
    index: int
    status: str = Field(description="supported | partial | unsupported")
    rewrite: str | None = None


class VerifyOut(BaseModel):
    verdicts: list[_Verdict]


def _content(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%.+#-]+", t.lower()) if w not in STOP and len(w) > 2}


def lexical_check(claim: str, evidence_texts: list[str]) -> str:
    """Heuristic grounding: content-word coverage of the claim by its best evidence; numbers must appear."""
    claim = re.split(r",? which maps to your need for", claim)[0]
    cw = _content(claim)
    if not cw:
        return "supported"
    nums = set(re.findall(r"\d+(?:\.\d+)?", claim))
    best = 0.0
    for t in evidence_texts:
        et = _content(t)
        if nums and not nums <= set(re.findall(r"\d+(?:\.\d+)?", t)):
            continue
        best = max(best, len(cw & et) / len(cw))
    return "supported" if best >= 0.6 else "partial" if best >= 0.35 else "unsupported"


def is_claim(text: str) -> bool:
    return bool(EXPERIENCE.search(text)) and not text.rstrip().endswith("?")


async def check_claims(claims: list[Claim], fit, use_llm: bool, extra: dict[str, str] | None = None) -> list[Claim]:
    ev_by_id = {e.id: e.text for m in fit.matches for e in m.evidence} | (extra or {})
    out = []
    pending = []
    for c in claims:
        if not is_claim(c.text) and not c.evidence_ids:
            out.append(c.model_copy(update={"status": "supported", "note": "not a factual claim"}))
            continue
        texts = [ev_by_id[i] for i in c.evidence_ids if i in ev_by_id]
        if not texts:  # no citation: search the portfolio once
            res = await tool("verifier", "portfolio.search_evidence", {"requirement": c.text[:300], "k": 3})
            texts = [i["snippet"] for i in res.get("items", [])]
            c = c.model_copy(update={"evidence_ids": [i["id"] for i in res.get("items", [])][:2]})
            for i in res.get("items", []):
                ev_by_id[i["id"]] = i["snippet"]
        status = lexical_check(c.text, texts)
        out.append(c.model_copy(update={"status": status}))
        pending.append((len(out) - 1, texts))
    if use_llm and pending:
        sys = prompting.load("verifier")
        user = "\n\n".join(f"CLAIM {k}: {out[k].text}\nEVIDENCE: " + " | ".join(t[:400] for t in texts)
                           for k, texts in pending)
        res, _ = llm.structured("verifier", VerifyOut, sys, user)
        for v in (res.verdicts if res else []):
            if 0 <= v.index < len(out) and v.status in ("supported", "partial", "unsupported"):
                upd = {"status": v.status}
                if v.status == "partial" and v.rewrite:
                    upd.update(text=v.rewrite, note="rewritten to match evidence")
                out[v.index] = out[v.index].model_copy(update=upd)
    return out


@node("verifier")
async def verifier(state: dict) -> dict:
    draft: ProposalDraft = state["drafts"][-1]
    extra = state.get("evidence_texts") or {}
    checked = await check_claims(draft.claims, state["fit"], llm.online("verifier"), extra)
    before = sum(c.status == "unsupported" for c in checked)
    kept = [c for c in checked if c.status != "unsupported"]
    # re-check what remains (partials may have been rewritten) to measure "after"
    after = sum(c.status == "unsupported" for c in await check_claims(
        [c.model_copy(update={"status": "unchecked"}) for c in kept], state["fit"], False, extra))
    verified = draft.model_copy(update={
        "claims": kept, "cover_letter": " ".join(c.text for c in kept),
        "unsupported_before_verify": before, "unsupported_after_verify": after})
    return {"drafts": [verified]}


# ====================================================================== critic
GENERIC_OPEN = re.compile(r"(dear (sir|madam|hiring)|i am (very )?excited|perfect fit|passionate|greetings|"
                          r"i read your (job )?post|to whom it may concern)", re.I)


class CriticOut(BaseModel):
    approve: bool
    personalisation_ok: bool
    issues: list[str] = []
    revision_instructions: str | None = None


@node("critic")
async def critic(state: dict) -> dict:
    draft: ProposalDraft = state["drafts"][-1]
    job = state["job"]
    max_words = section("thresholds")["proposal_max_words"]
    words = len(draft.cover_letter.split())
    first = draft.claims[0].text if draft.claims else ""
    issues = []
    grounding_ok = draft.unsupported_after_verify == 0
    if not grounding_ok:
        issues.append(f"{draft.unsupported_after_verify} claim(s) still unsupported")
    length_ok = 40 <= words <= max_words
    if not length_ok:
        issues.append(f"length {words} words (want 40-{max_words})")
    title_terms = set(re.findall(r"[a-z]{4,}", job["title"].lower()))
    personal_ok = not GENERIC_OPEN.search(first) and bool(title_terms & set(re.findall(r"[a-z]{4,}", first.lower())))
    if not personal_ok:
        issues.append("first sentence is generic — open with the client's specific problem")
    if not any(c.evidence_ids for c in draft.claims):
        issues.append("no evidence cited")
        grounding_ok = False
    llm_out = None
    if llm.online("critic") and grounding_ok and length_ok:
        llm_out, _ = llm.structured("critic", CriticOut,
                                    prompting.render("critic", max_words=str(max_words),
                                                     untrusted_rules=prompting.load("_untrusted_rules")),
                                    f"PROPOSAL:\n{draft.cover_letter}\n\n"
                                    + prompting.untrusted("job_post", f"{job['title']}\n\n{job['description']}"))
        if llm_out is not None:
            personal_ok = personal_ok and llm_out.personalisation_ok
            issues += [i for i in llm_out.issues if i not in issues]
    approve = grounding_ok and length_ok and personal_ok and (llm_out is None or llm_out.approve)
    c = Critique(job_id=job["job_id"], draft_version=draft.version, approve=approve, grounding_ok=grounding_ok,
                 personalisation_ok=personal_ok, length_ok=length_ok, issues=issues,
                 revision_instructions=(llm_out.revision_instructions if llm_out else None) or "; ".join(issues) or None)
    return {"critiques": [c], "revision": state.get("revision", 0) + (0 if approve else 1)}


# ====================================================================== compliance guard
def policy_rules() -> list[tuple[str, re.Pattern, str]]:
    """(rule id, pattern, action). Mirrors data/policy_pack/marketplace_policy.md."""
    return [
        ("P1-off-platform-contact", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\+?\d[\d\s().-]{8,}\d|"
                                               r"\b(whatsapp|telegram|skype|discord|wa\.me|t\.me)\b", re.I), "removed"),
        ("P1-off-platform-payment", re.compile(r"\b(paypal|crypto|bitcoin|usdt|western union|bank transfer|pay (me )?"
                                               r"(directly|outside))\b", re.I), "removed"),
        ("P4-guarantee", re.compile(r"\b(guarantee[sd]?|100% (sure|success|satisfaction)|risk[- ]free|never fail)\b", re.I),
         "rewritten"),
        ("P2-free-work", re.compile(r"\b(free (sample|trial|test)|work for free|no charge)\b", re.I), "removed"),
        ("P5-injection-echo", re.compile(r"\b(banana|developer mode|system prompt|ignore (all )?previous)\b", re.I), "removed"),
    ]


def comply(text: str, claims: list[Claim] | None = None) -> tuple[str, list[ComplianceViolation]]:
    violations: list[ComplianceViolation] = []
    sentences = [c.text for c in claims] if claims else re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for sent in sentences:
        drop = False
        for rule, pat, action in policy_rules():
            if m := pat.search(sent):
                violations.append(ComplianceViolation(rule=rule, span=m.group(0), action=action))
                if action == "rewritten":
                    sent = pat.sub("aim to", sent)
                else:
                    drop = True
        if not drop:
            kept.append(sent)
    return " ".join(kept).strip(), violations


@node("compliance")
async def compliance(state: dict) -> dict:
    draft: ProposalDraft = state["drafts"][-1]
    risk = state.get("risk")
    cleaned, violations = comply(draft.cover_letter, draft.claims)
    if draft.unsupported_after_verify:
        violations.append(ComplianceViolation(rule="P3-no-fabrication", span=f"{draft.unsupported_after_verify} claims",
                                              action="blocked"))
    block = None
    if risk and any(f.kind in ("off_platform_payment", "scam") for f in risk.flags):
        block = "job asks for off-platform payment or matches a scam pattern"
    elif draft.unsupported_after_verify:
        block = "proposal still contains claims the portfolio does not support"
    elif len(cleaned.split()) < 25:
        block = "too little compliant text left after removing violations"
    verdict = ComplianceVerdict(job_id=draft.job_id, allowed=block is None, violations=violations,
                                cleaned_text=cleaned, block_reason=block)
    out = {"compliance": verdict}
    if block:
        out.update(status="blocked", attention_reason=f"Compliance Guard blocked: {block}")
    return out

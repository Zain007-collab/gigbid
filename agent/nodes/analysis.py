"""The three parallel analysis nodes: Job Analyst, Risk Assessor, Fit Matcher (R3)."""
from __future__ import annotations

import math
import re

from agent import llm, prompting
from agent.handoffs import (
    EvidenceItem, FitReport, JobSpec, RequirementMatch, RiskFlag, RiskReport, ScreeningQuestion,
)
from agent.nodes.base import node, tool
from agent.settings import section
from rag.pipeline import extract_requirements


# Past proposals are sales copy, not proof: they shape wording and outcome memory but never support a claim.
PROOF_TYPES = ["case_study", "readme", "code", "repo", "review", "cv"]


def job_text(job: dict) -> str:
    return f"{job['title']}\n\n{job['description']}"


def _rules() -> str:
    return prompting.load("_untrusted_rules")


# ====================================================================== Job Analyst
@node("job_analyst")
async def job_analyst(state: dict) -> dict:
    job = state["job"]
    spec = None
    if llm.online("job_analyst"):
        spec, _ = llm.structured(
            "job_analyst", JobSpec, prompting.render("job_analyst", untrusted_rules=_rules()),
            f"job_id: {job['job_id']}\nbudget: {job.get('budget_type')} {job.get('budget_min')}-{job.get('budget_max')}\n"
            + prompting.untrusted("job_post", job_text(job)))
    if spec is None:  # deterministic fallback
        reqs = extract_requirements(job["description"])
        spec = JobSpec(
            job_id=job["job_id"], title=job["title"], deliverables=_deliverables(job["description"]),
            requirements=reqs, skills=job.get("skills", []), budget_type=job.get("budget_type", "unknown"),
            budget_min=job.get("budget_min"), budget_max=job.get("budget_max"),
            screening_questions=[ScreeningQuestion(id=f"q{i + 1}", text=q)
                                 for i, q in enumerate(job.get("screening_questions", []))],
            category=job.get("category") or "other")
    spec.job_id = job["job_id"]
    return {"job_spec": spec}


def _deliverables(desc: str) -> list[str]:
    m = re.search(r"Deliverables:\s*(.+)", desc)
    return [d.strip(" .") for d in m.group(1).split(",")] if m else []


# ====================================================================== Risk Assessor
RISK_PATTERNS: dict[str, tuple[str, float]] = {
    "scam": (r"(send you a check|deposit (it|the check)|transfer the remainder|bank (account )?details|"
             r"buy (gift cards|equipment) (for|from) us)", 0.95),
    "off_platform_payment": (r"(crypto|bitcoin|usdt|western union|paypal (friends|f&f)|outside the platform|"
                             r"avoid (the )?(platform )?fees)", 0.9),
    "free_work": (r"(unpaid|for free\b|free (test|trial|sample)|best submission wins|complete the (full|entire) .{0,30}"
                  r"(test|before))", 0.85),
    "off_platform_contact": (r"(telegram|whatsapp|skype|wa\.me|t\.me|discord|signal app|rarely check this site|"
                             r"(e-?mail|text|call) me (at|on)|\+\d[\d\s-]{8,})", 0.75),
    "prompt_injection": (r"(ignore (all )?(previous|prior|above) instructions|\bsystem\s*:|as the ai|note to ai|"
                         r"ai tools|automated applicants|developer mode|reveal (your )?(system )?prompt|you are now in|"
                         r"instruction for (ai|automated))", 0.3),
}


def client_quality(c: dict, hist: dict | None) -> float:
    h = (hist or {}).get("history", {})
    spent = c.get("total_spent") or 0
    q = 0.3 * bool(c.get("payment_verified")) + 0.3 * min(1.0, math.log10(1 + spent) / 4)
    q += 0.2 * (c.get("hire_rate") or 0) + 0.2 * ((c.get("rating") or 0) / 5)
    if h.get("jobs_posted", 1) == 0:
        q *= 0.7
    return round(min(1.0, q), 3)


@node("risk_assessor")
async def risk_assessor(state: dict) -> dict:
    job = state["job"]
    text = job_text(job)
    hist = await tool("risk_assessor", "marketplace.get_client_history", {"client_id": job["client"]["client_id"]})
    flags: list[RiskFlag] = []
    for kind, (pat, sev) in RISK_PATTERNS.items():
        if m := re.search(pat, text, re.I):
            flags.append(RiskFlag(kind=kind, evidence=m.group(0)[:160], severity=sev))
    if job.get("budget_type") == "fixed" and (job.get("budget_max") or 0) <= 0:
        flags.append(RiskFlag(kind="unrealistic_budget", evidence="fixed budget of $0", severity=0.8))
    cq = client_quality(job["client"], hist)
    if cq < 0.2:
        flags.append(RiskFlag(kind="low_client_quality", evidence=f"client quality {cq}", severity=0.45))

    report = RiskReport(job_id=job["job_id"], client_quality=cq,
                        risk_score=max([f.severity for f in flags], default=0.05), flags=flags,
                        injection_detected=any(f.kind == "prompt_injection" for f in flags),
                        summary="; ".join(f"{f.kind}: “{f.evidence}”" for f in flags) or "no risk signals")

    if llm.online("risk"):  # LLM catches paraphrased scams the regexes miss; never lowers a rule-based flag
        llm_rep, _ = llm.structured(
            "risk", RiskReport, prompting.render("risk_assessor", untrusted_rules=_rules()),
            f"client_quality (computed): {cq}\nclient history: {hist}\n" + prompting.untrusted("job_post", text))
        if llm_rep is not None:
            known = {f.kind for f in report.flags}
            report.flags += [f for f in llm_rep.flags if f.kind not in known]
            report.risk_score = max(report.risk_score, llm_rep.risk_score)
            report.injection_detected = report.injection_detected or llm_rep.injection_detected
            report.summary = "; ".join(f"{f.kind}: “{f.evidence}”" for f in report.flags) or report.summary
    return {"risk": report}


# ====================================================================== Fit Matcher
@node("fit_matcher")
async def fit_matcher(state: dict) -> dict:
    job = state["job"]
    reqs = extract_requirements(job["description"]) or [job["title"]]
    import os
    backend = os.environ.get("RAG_EMBED_BACKEND", section("embeddings")["backend"])
    threshold = section("thresholds")["evidence_min_score"][backend]
    matches: list[RequirementMatch] = []
    for r in reqs[:8]:
        res = await tool("fit_matcher", "portfolio.search_evidence",
                         {"requirement": r, "k": 3, "filters": {"types": PROOF_TYPES}})
        items = [EvidenceItem(id=i["id"], type=_etype(i["type"]), source=i.get("source") or "", project=i.get("project"),
                              date=i.get("date"), outcome=i.get("outcome"), text=i["snippet"],
                              score=i.get("similarity", i["score"])) for i in res.get("items", [])]
        best = max((i.score for i in items), default=0.0)
        supported = bool(res.get("supported")) and best >= threshold
        matches.append(RequirementMatch(requirement=r, evidence=items if supported else items[:1], supported=supported,
                                        reason=f"best similarity {best:.2f} (threshold {threshold})"))
    n_sup = sum(m.supported for m in matches)
    fit = FitReport(job_id=job["job_id"], fit_score=round(n_sup / max(1, len(matches)), 3), matches=matches,
                    gaps=[m.requirement for m in matches if not m.supported],
                    reasons=[f"{n_sup}/{len(matches)} requirements have portfolio evidence"])
    return {"fit": fit}


def _etype(t: str) -> str:
    return {"readme": "repo", "repo": "repo", "code": "code"}.get(t, t if t in
            ("case_study", "cv", "review", "proposal") else "case_study")

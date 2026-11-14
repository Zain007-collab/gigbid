"""Explicit promotion rules (R7). The ONLY code path that writes long-term memory.

Style memory (from the user's edits to drafts):
  RULE style.repeat.v1 — a candidate pattern extracted from an approved edit is promoted
  when it has been observed in >= memory.style.min_occurrences edits across
  >= memory.style.min_distinct_jobs different jobs.

Outcome memory (from marketplace outcomes per job category):
  RULE outcome.lift.v1 — a proposal feature is promoted as an insight for a category when
  >= memory.outcome.min_samples_per_category proposals carry an outcome and the reply rate
  with the feature exceeds the rate without it by >= memory.outcome.min_reply_rate_lift.

Candidates are derived features (patterns, booleans), never raw tool output or message text.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from agent.settings import section

STOP = set("the a an and or of to in on for with by at from as is was were be been this that it i my we our you "
           "your have has had will can would could just very really also".split())


# ---------------------------------------------------------------- style
def edit_patterns(before: str, after: str) -> list[tuple[str, str]]:
    """(pattern_id, human-readable rule) observed in one edit."""
    pats: list[tuple[str, str]] = []
    sent = lambda t: [s.strip() for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s.strip()]  # noqa: E731
    b, a = sent(before), sent(after)
    if b and a and b[0] not in after and len(a) <= len(b):
        pats.append(("cut_first_sentence", "always cut the generic first sentence"))
    words = lambda t: set(re.findall(r"[a-z][a-z'-]{3,}", t.lower())) - STOP  # noqa: E731
    for w in sorted(words(before) - words(after)):
        if before.lower().count(w) >= 1 and w not in words(after):
            pats.append((f"avoid_word:{w}", f'never say "{w}"'))
    lb, la = len(before.split()), len(after.split())
    if lb and la < 0.75 * lb:
        pats.append(("shorter", f"keep proposals short (about {la} words)"))
    if a and b and a[-1] not in before:
        pats.append(("custom_closing", "end with a specific question about the client's project"))
    return pats[:12]


async def observe_edit(store, user_id: str, job_id: str, before: str, after: str) -> list[dict]:
    """Record candidates from one edit and promote any that satisfy style.repeat.v1."""
    cfg = section("memory")["style"]
    promoted = []
    for pid, rule in edit_patterns(before, after):
        ns = ("style_candidates", user_id)
        item = await store.aget(ns, pid)
        val = item.value if item else {"rule": rule, "jobs": [], "count": 0}
        val["count"] += 1
        if job_id not in val["jobs"]:
            val["jobs"].append(job_id)
        await store.aput(ns, pid, val)
        already = await store.aget(("style", user_id), pid)
        if not already and val["count"] >= cfg["min_occurrences"] and len(val["jobs"]) >= cfg["min_distinct_jobs"]:
            mem = {"rule": rule, "promoted_by": "style.repeat.v1", "evidence_jobs": val["jobs"][-10:],
                   "observations": val["count"], "promoted_at": datetime.now(timezone.utc).isoformat()}
            await store.aput(("style", user_id), pid, mem)
            promoted.append({"key": pid, **mem})
    return promoted


# ---------------------------------------------------------------- outcome
def proposal_features(cover_letter: str, n_citations: int, bid: float, market_p50: float | None) -> dict[str, bool]:
    first = re.split(r"(?<=[.!?])\s+", cover_letter.strip(), maxsplit=1)[0].lower()
    return {
        "specific_opening": not re.search(r"(dear|excited|passionate|perfect fit|greetings)", first),
        "cites_2plus_projects": n_citations >= 2,
        "has_metric": bool(re.search(r"\d+\s?(%|x|seconds|minutes|hours)", cover_letter.lower())),
        "bid_at_or_below_median": market_p50 is not None and bid <= market_p50,
        "under_150_words": len(cover_letter.split()) < 150,
    }


INSIGHT_TEXT = {
    "specific_opening": "open with the client's specific problem",
    "cites_2plus_projects": "cite two or more concrete projects",
    "has_metric": "include a measured result (a number)",
    "bid_at_or_below_median": "bid at or below the market median",
    "under_150_words": "keep it under 150 words",
}


async def observe_outcome(store, user_id: str, category: str, proposal_id: str, features: dict[str, bool],
                          replied: bool) -> list[dict]:
    cfg = section("memory")["outcome"]
    ns = ("outcome_samples", user_id, category)
    await store.aput(ns, proposal_id, {"features": features, "replied": replied})
    samples = [i.value for i in await store.asearch(ns, limit=1000)]
    promoted = []
    if len(samples) < cfg["min_samples_per_category"]:
        return promoted
    for f in features:
        with_f = [s for s in samples if s["features"].get(f)]
        without = [s for s in samples if not s["features"].get(f)]
        if len(with_f) < 2 or len(without) < 2:
            continue
        r1 = sum(s["replied"] for s in with_f) / len(with_f)
        r0 = sum(s["replied"] for s in without) / len(without)
        if r1 - r0 >= cfg["min_reply_rate_lift"]:
            mem = {"insight": f"{INSIGHT_TEXT[f]} (reply rate {r1:.0%} vs {r0:.0%}, n={len(samples)})",
                   "promoted_by": "outcome.lift.v1", "feature": f, "reply_with": round(r1, 3),
                   "reply_without": round(r0, 3), "n": len(samples),
                   "promoted_at": datetime.now(timezone.utc).isoformat()}
            await store.aput(("outcome", user_id, category), f, mem)
            promoted.append(mem)
    return promoted

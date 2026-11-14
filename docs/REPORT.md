# GigBid — R&D report (engineering design review)

Every number below is read from a file in `evals/results/` (named in brackets) or a commit. Sections marked
**PENDING** need a run with a hosted open model (Groq) and are not yet filled in; nothing has been estimated.

## 1. Problem and constraints
A freelancer's loop is *discover → judge → price → write → follow up*. GigBid automates discovery, judgement and
drafting; it **never** automates sending, submitting, spending Connects, moving a client off-platform, or claiming
experience the portfolio does not show. Every write passes a LangGraph `interrupt()` and, in live Upwork mode, a
second confirmation on Upwork.

Declared budgets (config `budgets`): p95 ≤ 20 s per job analysed, ≤ 60 s per proposal; ≤ $0.002 per job and ≤ $0.01
per proposal on the open profile; 40 steps and 60k tokens per thread.

## 2. Architecture
Two hand-written graphs (rendered from code in `docs/ARCHITECTURE.md`): the bid graph runs Job Analyst, Risk Assessor
and Fit Matcher in parallel via `Send`, then decide → pricing → writer → claim verification → critic (≤2 revisions) →
compliance → approval interrupt → idempotent submit; the conversation graph handles client replies with the same
approval gate. A single marketplace port (GigSim REST / Upwork official MCP) is served by our Marketplace MCP server;
Portfolio and Workspace are our other two servers; GitHub's MCP server is the second third-party server. State,
execution trace and long-term memory are separate (§ARCHITECTURE). Every LLM node has a deterministic fallback
(ADR 0004), which is what all *offline* numbers below measure.

## 3. R&D question A — tool granularity — **PENDING (needs LLM)**
*Hypothesis:* a small model selects tools more accurately from a small set of well-described tools than from either
many fine-grained tools or two catch-all tools, at fewer tokens per task.
*Setup:* 40 tasks with known correct first calls (`evals/runners/rq_a_tools.py`), three tool sets (21 fine, 2 coarse,
9 ours with example-bearing descriptions), same model (config node `scout`), `tool_choice=required`.
*Results:* run `python -m evals.runners.rq_a_tools` → `rq_a_tools.json`.

## 4. R&D question B — retrieval, verification, ablation
*Hypothesis (stated before running):* requirement decomposition + hybrid retrieval raises evidence Recall@5 by
≥ 15 points over V0 on jobs with four or more requirements.

**Variant sweep** [`rag_dev_fastembed.json`; bge-small-en-v1.5, 3 personas × ≤100 dev jobs]

| Variant | Recall@5 | R@5 (4+ reqs) | nDCG@10 | MRR |
|---|---|---|---|---|
| V0 naive | 0.685 | 0.765 | 0.440 | 0.826 |
| V1 decomposition | 0.724 | 0.778 | 0.493 | 0.908 |
| V2 hybrid (+V1) | 0.795 | 0.839 | 0.552 | 0.909 |
| V3 + cross-encoder | 0.750 | 0.816 | 0.533 | 0.900 |
| V4 + outcome boost (on V3) | 0.756 | 0.819 | 0.546 | 0.861 |
| V5 + multi-vector (on V3) | 0.775 | 0.822 | 0.568 | 0.875 |
| V6 + contextual (on V3) | 0.750 | 0.812 | 0.523 | 0.897 |
| V8 corrective RAG (on V5) | 0.706 | 0.789 | 0.495 | 0.867 |

**Ablation** [`ablation_dev_round1.json`, `ablation_dev_round2.json`]. From decompose+hybrid+multivector+outcome:
removing outcome boosting *raised* Recall@5 from 0.774 to 0.842. From the resulting base (decompose + hybrid +
multivector = **shipped**, R@5 0.842, nDCG 0.646, MRR 0.917), removing decomposition costs 10.5 points, removing
hybrid 4.6, removing multi-vector 4.8; adding the cross-encoder costs 6.7, outcome boosting 6.8, contextual lines
0.3 (nDCG +1.0, a wash).

*Hypothesis check:* on 4+-requirement jobs V2 (decompose+hybrid) reaches 0.839 vs 0.765 for V0 (+7.4 points): the
direction holds, the 15-point size does not. Across all jobs the shipped pipeline adds +15.7 points of Recall@5.

*Why the cross-encoder hurt:* ms-marco-MiniLM is trained on web-search relevance, and our queries are short
requirement phrases against project write-ups. It preferred chunks that repeat the query's words (reviews, past
proposals) over the case study or code that proves the skill. bge-reranker-base was too slow to use on the dev CPU
(~6 pairs/s, [`reranker_speed.json`]).

*Why outcome boosting hurt retrieval:* it promotes past *proposals*, which mention many skills in sales language,
above the artefacts that prove the skill. Outcome knowledge is used where it belongs instead — in the writer, via the
outcome memory (§C2).

**Claim verification (B3)** [`decision_dev_shipped_offline.json`]: unsupported claims per proposal **0.245 before →
0 after** verification on 355 drafted proposals; holdout 0.19 → 0 (94 proposals). The deterministic writer only
paraphrases evidence, so the "before" rate is low by construction; the LLM writer's rate is **PENDING**.

**Decisions** (shipped retrieval, deterministic nodes):

| Split | Bid/skip F1 | Precision | Recall | Scam P / R | Price in fair band | Injection attack success |
|---|---|---|---|---|---|---|
| dev, threshold 0.55 (initial guess) [`decision_dev_shipped_t055.json`] | 0.842 | 0.727 | 1.000 | 1.0 / 1.0 | 97.1% | 0 / 51 |
| dev, threshold 0.65 (swept on dev) [`decision_dev_shipped_offline.json`] | 0.943 | 0.933 | 0.954 | 1.0 / 1.0 | 97.3% | 0 / 51 |
| **holdout** (never tuned on) [`decision_holdout_shipped_offline.json`] | **0.941** | 0.926 | 0.956 | 1.0 / 1.0 | 100% | 0 / 16 |
| dev, **V0 retrieval** [`decision_dev_v0_offline.json`] | 0.947 | 0.921 | 0.974 | 1.0 / 1.0 | 97.4% | 0 / 51 |

**Where better retrieval did not translate:** V0 makes bid/skip decisions as well as the shipped pipeline (F1 0.947
vs 0.943) despite 15.7 points less Recall@5. The Fit Matcher only needs the single most similar item per requirement
to clear a threshold, and V0's small fixed chunks often produce *higher* top-1 similarity than whole-document
chunks. Recall matters for what the writer can cite, not for the decision. Reply-rate impact: **[`gigsim_outcomes_*`
— see §README table once the run completes]**.

**By persona and job type:** F1 by category on dev: ai_ml 0.959, data 0.964, web 0.957, devops 0.435 (13 false
positives); off-domain categories (design, mobile, writing, other) contain no gold bids, so their F1 is 0 by
definition; there the relevant number is that they were skipped.

**Judge calibration — PENDING (needs your labels):** `python -m evals.runners.judge_calibration sample` writes 30
proposals to `evals/datasets/judge_labels.csv`; the author labels them, then `calibrate` reports Cohen's κ.

**Shipped:** decompose + hybrid BM25/dense (RRF) + multi-vector grouping, evidence threshold 0.65 (ADR 0003).

## 5. R&D question C — single agent vs supervisor vs graph — **PENDING (needs LLM)**
Runner: `evals/runners/rq_c_agents.py` (same model, same MCP tools, same grader, same GigSim clients; harness approves
like a human). Metrics: bid accuracy, scams bid on, unsupported claims, violations in output vs caught, reply rate,
tokens, p95 latency, per-category accuracy.

## 6. The change request
GigSim simulates the 2 Nov platform update as **policy v2**: +2 Connects on jobs with budget ≥ $1,000 and at most two
proposals to the same client per seven days. *What changed in GigBid:* nothing for Connects — Pricing reads the cost
the marketplace reports and the approval screen's `dry_run` preview shows it. The per-client limit needed a config
hook that did not exist yet: `agent/policy.py::check_bidding_rules` (≈30 lines, written once, generic over three rule
types) and **one line of config** (`policy.bidding_rules: [{rule: max_proposals_per_client_per_week, value: 2}]`).
Verified by `tests/integration/test_change_request.py` and `tests/unit/test_policy_rules.py`. Lesson: costs already
flowed through the port; rules about *the user's history* needed a generic, configurable checkpoint before Connects
are spent.

## 7. Signature extension — X2 proposal experiments
Two writer strategies (`evidence_first`: up to three case studies with measured results; `concise_question`: one
strongest case study plus a scoping question), jobs randomly assigned (seed 11), submitted to GigSim clients, compared
with a two-proportion z-test and Fisher's exact test [`ab_test_evidence_first_vs_concise_question.json`]. See
`docs/EXTENSION.md`.

## 8. Failure analysis (dev, shipped pipeline) [`decision_dev_shipped_offline.json`]
40 of 600 dev decisions were wrong (24 false bids, 16 missed bids).

| Group (taxonomy) | Count | Example | Cause | Fix tried | Worked? |
|---|---|---|---|---|---|
| **Wrong bid — adjacent-skill conflation** | 24 | `dev-ai_ml-070` (Docker, AWS, Kubernetes; gold coverage 0.33) | "container orchestration cluster" matches Docker evidence at cosine 0.65–0.75 | raised evidence threshold 0.55 → 0.65 | Partly: false bids 131 → 24, holdout confirms; the rest needs a per-requirement LLM check (not yet tried) |
| **Missed evidence — paraphrase-only skills** | 16 | `dev-ai_ml-143` (Pinecone, Airflow, Celery, SQL; gold coverage 1.0, fit 0.5) | Pinecone appears only as "managed vector database" and as an import in code; best similarity 0.62 | multi-vector indexing of code chunks | Partly (Recall@5 +4.8 from multi-vector), but the threshold cliff at 0.65–0.70 still drops it |
| Mispriced | 9 / 332 priced outside the fair band (all hourly) | `dev-ai_ml-091` bid $40/h, band $22–39 | Pricing never bids below the user's floor (90% of their rate) even when the client's maximum is lower — a deliberate product rule the gold band does not encode | none (by design; such jobs should arguably be skipped on budget) | — |
| Unsupported claim | 0 after verification | — | — | claim verification before Critic | Yes (0.245 → 0 per proposal) |
| Injection | 0 / 51 succeeded | `dev-*` with "IGNORE ALL PREVIOUS INSTRUCTIONS …" | rules flag it; writer never sees instructions as instructions | delimiting + rules + compliance echo filter | Yes on these templates; see §9 for what would beat it |
| Tool error | 0 errors in 600 runs | — | — | polite queueing limiter + retries (ADR 0006) | Yes (degraded runs 19 → 0 in the smoke test) |
| Generic opening / compliance miss | **PENDING** (LLM writer) | | | | |

## 9. Safety and platform respect
*Never:* send, submit or spend without approval; move a client off-platform; claim unsupported experience; scrape or
automate a website. *Enforced by:* `interrupt()` + approval-id gate in the Marketplace server; idempotency keys; the
Compliance Guard; claim verification; rate-limited official APIs only; PII masking in logs/traces/audit; encrypted
per-user OAuth tokens; tenant-scoped queries; GigSim admin endpoints behind a token (we found and fixed a bypass).
*What still gets through:* scams and injections paraphrased away from the rule vocabulary (our synthetic set is
template-based, so 1.0 precision/recall is an upper bound); an attacker who phrases an instruction as a plausible
client requirement ("mention you have worked at Google") would be caught only if the claim checker cannot find
evidence — it would be *removed*, not obeyed, but the job would still be bid on.

## 10. Hidden-test results against budgets — **PENDING (after the deadline run)**
Offline measurements so far (dev, deterministic nodes) [`decision_dev_shipped_offline.json`]: p95 1.56 s per job
analysed and 1.68 s per proposal on a laptop CPU, $0 (no paid calls), all within the declared budgets. The hosted
open-model profile adds LLM latency and cost; those numbers will be reported from the deployed run, as measured.

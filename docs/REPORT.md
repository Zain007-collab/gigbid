# GigBid — R&D report (engineering design review)

Every number below is read from a file in `evals/results/` (named in brackets) or a commit. Two items are marked
**PENDING**: judge-vs-human κ (needs the author's 30 labels) and the hidden-test run (after the deadline). Nothing has
been estimated. Superseded results are kept in `evals/results/superseded/` with the reason in the file name.

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

## 3. R&D question A — tool granularity
*Hypothesis:* a small model selects tools and arguments more accurately from a small set of well-described tools than
from many fine-grained tools or two catch-all tools, at moderate token cost.
*Setup* [`rq_a_tools.json`]: 40 tasks with a known correct first call (8 templates × 5 jobs), the same model
(Groq `gpt-oss-20b`, config node `scout`), `tool_choice=required`, tools offered as schemas only.

| Tool set | Tools | Tool selection | Argument accuracy | Tokens / task |
|---|---|---|---|---|
| Fine-grained | 21 | 35/40 (87.5%) | 35/40 (87.5%) | 669 |
| Coarse (`marketplace_query(action, params)`) | 2 | 40/40 (100%) | 37/40 (92.5%) | 301 |
| **Ours** (9 tools, descriptions with examples) | 9 | **40/40 (100%)** | **40/40 (100%)** | 566 |

*Failure modes:* with fine-grained tools the model twice refused to call any tool despite `tool_choice=required` and
three times chose `search_past_proposals` for "which skills can I prove" (`list_skills` existed); with coarse tools
the decision simply moved into the `action` enum, where it picked `job` instead of `client` three times.
*Conclusion:* granularity changes behaviour; the middle ground with example-bearing descriptions was the only set with
zero errors, at 1.9× the tokens of the coarse set and 15% fewer than the fine set. The two refusals are counted as
failures (the runner's per-set accuracy over completed calls reads 92.1% for the fine set).

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

**Claim verification (B3).** Deterministic writer [`decision_dev_shipped_offline.json`]: unsupported claims per
proposal **0.19 → 0** (336 proposals); holdout 0.14 → 0 (84). The template only paraphrases evidence, so "before" is
low by construction. With the **LLM writer** (`gpt-oss-120b`) [`decision_dev_shipped_llm.json`, 15 jobs, 9 proposals]
it is **1.78 → 0**: the LLM invents about seven times more unsupported claims, and verification removes them all
before the Critic sees the draft. *Caveat:* free-tier rate limits pushed 35 node executions in that run onto the
deterministic path (counted per job in the file); LLM-judge mean 4.0 / 5 on its 9 proposals [`judge_shipped_llm.json`].

**Decisions — and how three bugs changed the story.** The first numbers looked excellent and were partly wrong:

| Configuration (dev unless noted) | Bid/skip F1 | Precision | Recall | File |
|---|---|---|---|---|
| threshold 0.55 (initial guess) | 0.842 | 0.727 | 1.000 | `decision_dev_shipped_t055.json` |
| threshold 0.65, **past proposals counted as proof** | 0.943 | 0.933 | 0.954 | `superseded/…before-proof-types.json` |
| proposals excluded, filter applied *after* ranking | 0.673 | 0.973 | 0.514 | `superseded/…proof-types_t065.json` |
| proposals excluded, filter applied *before* ranking, threshold re-swept to **0.62** | **0.879** | 0.893 | 0.865 | `decision_dev_shipped_offline.json` |
| **holdout** (never used for tuning), same configuration | **0.880** | 0.917 | 0.846 | `decision_holdout_shipped_offline.json` |
| V0 retrieval, same configuration | 0.866 | 0.888 | 0.845 | `decision_dev_v0_offline.json` |

The 0.943 rested on **circular evidence**: past proposals name skills in sales language ("needs Pinecone and
LangChain; I have shipped exactly that"), so they matched requirements strongly and "proved" skills the portfolio
never demonstrates. Excluding them exposed a second bug — the type filter ran after top-k, and for common skills the
top-k was all proposals, leaving 14% of requirements (308 / 2,197) with no evidence at all. Fixing both and
re-sweeping gives F1 0.879, confirmed on the untouched holdout (0.880). The threshold sits two points below a cliff
(at 0.64 recall falls to 0.67): `bge-small`'s similarities are compressed into a narrow band, a real fragility.

Other decision metrics (shipped, dev): scams 79/79 caught, 0 false alarms; price inside the fair band 97.7% (301
priced); injection attack success 0 / 51; 0 errors in 600 runs.

**Where better retrieval did and did not translate.** With honest evidence the shipped retriever now beats V0 on
decisions (F1 0.879 vs 0.866) — earlier, circular evidence had masked that difference. **Reply rate did not move**
[`gigsim_outcomes_shipped_offline.json`, `gigsim_outcomes_v0_offline.json`; 100 gold-bid jobs per persona, harness
approves, 10 simulated days]: shipped 68.7% realised / 67.8% expected (n = 259) vs V0 67.1% / 67.8% (n = 249). GigSim's
clients reward must-have coverage, measured results and a specific opening; the writer cites the top projects under
either retriever, so must-have coverage is the same (0.875 vs 0.880). Better recall pays off only when the writer
*uses* the extra evidence — the deterministic writer, by design, cites at most three projects.

**By persona and job type** (dev F1): ai_ml 0.891, fullstack 0.868, data_analyst 0.876; by category ai_ml 0.914,
web 0.899, data 0.852, devops 0.526 (adjacent-skill false bids). Off-domain categories contain no gold bids, so F1 is 0
by definition; there the relevant result is that they were skipped.

**Judge calibration.** 30 proposals sampled into `evals/datasets/judge_labels.csv` for the author to label (1–4
rubric). κ is reported once the author's labels are in [`judge_calibration.json`].

**Shipped:** decompose + hybrid BM25/dense (RRF) + multi-vector, proof types only, evidence threshold 0.62 (ADR 0003).

## 5. R&D question C — single agent vs supervisor vs graph
*Setup* [`rq_c_agents.json`]: 9 dev jobs (3 per persona); the same open model for **every** system
(Groq `qwen/qwen3.8-27b` via the experiment-only `GIGBID_MODEL_OVERRIDE`), the same MCP tools, the same claim grader
(LLM verifier + number rule) and compliance rules, the same GigSim clients; up to 3 attempts per job for every
system; 25 s pause between jobs to respect the free tier's tokens-per-minute cap.

| System | Finished | Invalid tool call | Rate-limited after retries | Correct bid/skip (finished) | Unsupported claims | Tokens / finished job |
|---|---|---|---|---|---|---|
| (a) single ReAct agent, all tools | 2 / 9 | 4 | 3 | 2 / 2 | 2 (one proposal) | 9,583 |
| (b) `langgraph-supervisor`, 3 workers | **0 / 9** | 6 | 3 | — | — | — |
| (c) hand-built graph | **9 / 9** | 0 | 0 | 7 / 9 | **0** (six proposals) | 3,503 |

*Failure modes:* the ReAct agent invented a tool name (`search_e evidence`); the supervisor invented a handoff tool
(`specialist`) three times and produced unparseable function calls twice; long agent transcripts also exhausted the
per-minute token budget. The graph never asks the model to choose tools: each node makes one structured call, and a
failed call falls back to a deterministic path, so it always finishes.
*Does (a) win anywhere?* Not measurably: it completed too few jobs to compare by job type (its two completions were
both correct). *Would we ship it?* No — on this budget a single agent is unreliable and ~2.7× more expensive per
finished job. *Caveats:* n = 9 is small and rate limits affected (a) and (b) more because their transcripts are
longer — which is itself part of the cost of that design. An earlier attempt on `gpt-oss-20b` hit the model's daily
token cap and is kept in `evals/results/superseded/`; the graph run here predates the "proposals are not proof" fix.

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
strongest case study plus a scoping question), jobs randomly assigned (seed 11), submitted to GigSim clients
[`ab_test_evidence_first_vs_concise_question.json`]: evidence-first **65.7%** (94/143) vs concise-question **57.0%**
(65/114); z = 1.43, p = 0.15 (Fisher p = 0.16) — a 8.7-point difference that is **not significant** at this sample
size; the default stays evidence-first. Earlier runs (before the evidence fixes) pointed the same way (p = 0.10–0.70),
which is a reason to collect more samples, not to claim a winner. A first run reported 0 replies in both arms because
the runner read statuses before GigSim applied due events; caught by the implausible zero. See `docs/EXTENSION.md`.

## 8. Failure analysis (dev, shipped pipeline) [`decision_dev_shipped_offline.json`]
83 of 600 dev decisions are wrong (36 false bids, 47 missed bids); the rows below also record bugs found along the
way and how each changed the numbers.

| Group (taxonomy) | Count | Example | Cause | Fix tried | Worked? |
|---|---|---|---|---|---|
| **Wrong bid — adjacent-skill conflation** | 36 | `dev-ai_ml-036` (Docker, Kubernetes, AWS; gold coverage 0.33, fit 1.0) | "container orchestration cluster" / AWS phrases match Docker evidence at cosine 0.62–0.73 | threshold sweep (0.55 → 0.62) | Partly: false bids 123 → 36; the rest needs a per-requirement LLM check (not yet tried) |
| **Missed evidence — paraphrase-only skills** | 47 | `dev-ai_ml-016` (Airflow, SQL, pgvector, Hugging Face, Kubernetes; gold coverage 0.8, fit 0.4) | SQL and Hugging Face evidence lands at 0.50–0.58, below 0.62 | multi-vector indexing; type filter before ranking | Partly: requirements with no evidence 308 → 0 and missed bids 169 → 47; the threshold cliff limits the rest |
| Mispriced | 7 / 301 priced outside the fair band (all hourly) | `dev-ai_ml-091` bid $40/h, band $22–39 | Pricing never bids below the user's floor (90% of their rate) even when the client's maximum is lower — a deliberate product rule the gold band does not encode | none (by design; such jobs should arguably be skipped on budget) | — |
| Unsupported claim | relevance clauses ("which maps to your need for X") were exempt from verification and often false | sampled judge item 1 (`dev-ai_ml-170`): a semantic-search project "mapping to" a model registry | writer appended the clause unconditionally; verifier stripped it before grading | clause only when the case study covers the requirement; verifier grades whole sentences; all affected results regenerated (old ones in `evals/results/superseded/`) | Yes (see regenerated `decision_*` files) |
| Unsupported claim — circular evidence | past proposals counted as proof of experience | recorded demo, `dev-ai_ml-003`: "I have shipped a Pinecone solution…" cited a past proposal's own sales line | retrieval returned proposals among evidence and nothing excluded them | agents request proof types only (case studies, repos, code, reviews, CV); proposals feed outcome memory only | Yes — regression test `test_past_proposals_never_count_as_proof` |
| Injection | 0 / 51 succeeded | `dev-*` with "IGNORE ALL PREVIOUS INSTRUCTIONS …" | rules flag it; writer never sees instructions as instructions | delimiting + rules + compliance echo filter | Yes on these templates; see §9 for what would beat it |
| Tool error / retrieval bug | evidence type filter applied after top-k | 14% of requirements returned no evidence | proposals filled the top-k, then were filtered out | filter inside retrieval (mask before ranking) | Yes: 308 → 0; regression test `test_type_filter_applies_before_ranking` |
| Tool error | 0 errors in 600 runs | — | — | polite queueing limiter + retries (ADR 0006) | Yes (degraded runs 19 → 0 in the smoke test) |
| Generic opening | LLM writer opened with a generic sentence | demo, `dev-ai_ml-004`: "Critic v1: revise — first sentence is generic" | the writer prompt alone does not stop generic openings | Critic's rule-based personalisation check sends the draft back (≤2 rounds) | Yes in that case (approved at v3); GigSim reports 0% generic openings in submitted proposals |
| Compliance miss | none observed: 0 policy violations reached a submitted proposal; 1 draft blocked (unsupported claims) | — | — | Compliance Guard + claim verification | — |

## 9. Safety and platform respect
*Never:* send, submit or spend without approval; move a client off-platform; claim unsupported experience; scrape or
automate a website. *Enforced by:* `interrupt()` + approval-id gate in the Marketplace server; idempotency keys; the
Compliance Guard; claim verification; rate-limited official APIs only; PII masking in logs/traces/audit; encrypted
per-user OAuth tokens; tenant-scoped queries; GigSim admin endpoints behind a token (we found and fixed a bypass).
*What still gets through:* scams and injections paraphrased away from the rule vocabulary (our synthetic set is
template-based, so 1.0 precision/recall is an upper bound); an attacker who phrases an instruction as a plausible
client requirement ("mention you have worked at Google") would be caught only if the claim checker cannot find
evidence — it would be *removed*, not obeyed, but the job would still be bid on.

### Found by running the deployed system (Render free, Neon, Groq free tier)
| Finding | How it showed up | Fix | Verified by |
|---|---|---|---|
| Free host has 512 MB; the app peaked at ~500 MB | measured locally before deploying | float32 `.npz` embedding cache, single-threaded ONNX, one inline run at a time, embeddings baked into the image | peak RSS 500 → 183 MB for the same 8 analyses |
| Fallback model no longer existed | live trace: `OpenAIModelNotFoundError … Qwen3 8B was deprecated` on Job Analyst and Risk Assessor | fallback → Groq `qwen/qwen3.8-27b` (separate per-model rate limit) | structured-output smoke test |
| Recycled container left a job `running` forever | live inbox stuck at 1 running / 7 queued after the instance slept | startup recovery: resume from the Postgres checkpoint (or restart if none), re-queue waiting jobs | `tests/integration/test_startup_recovery.py` |
| Free-tier daily token cap (200k/day for `gpt-oss-20b`) | evaluation runs exhausted it; nodes silently used the deterministic path | fallbacks counted per job (`llm_fallbacks`, `deterministic_nodes`); rate-limited runs moved to `evals/results/superseded/` | decision results now state how much was really LLM |

## 10. Hidden-test results against budgets — **PENDING (after the deadline run)**
Measured so far, against the declared budgets (p95 ≤ 20 s per job, ≤ 60 s per proposal; ≤ $0.002 per job,
≤ $0.01 per proposal):

| Run | p95 per job | p95 per proposal | USD per job | USD per proposal | Within budget? |
|---|---|---|---|---|---|
| Deterministic nodes, laptop [`decision_dev_shipped_offline.json`] | 1.46 s | 1.54 s | $0 | $0 | yes |
| Hosted open models, Groq free tier [`decision_dev_shipped_llm.json`] | 86.3 s | 86.3 s | $0.0019 | $0.0030 | **cost yes, latency no** |

The latency miss is rate-limit waiting, not compute: the median job took 18.6 s, and the p95 is dominated by
`Retry-After` sleeps on the free tier's tokens-per-minute cap. The optimisation that helped most was routing the
verifier and critic to the 20B model and trimming the writer's evidence pack (one live job went from 57 s with
retries to 13 s). Hidden-test results will be reported here as measured, without excuses.

# GigBid — Saturday & Sunday Checklist

> Source: *AI-4016 R&D Assignment II — GigBid* (100 marks × viva multiplier 0–1.0)
> Rubric weights: **A MCP 20 · B RAG 20 · C Graphs 25 · D Web UI 15 · E Prod 10 · Portfolio/Report 10**

---

## ⚠️ Before anything else (15 min)

- [ ] **Confirm the deadline.** The PDF says **12 Nov 2026, 11:59 PM PKT**, and commits made after it are ignored. Today is Sat 14 Nov. If you have an extension, get it in writing (Classroom comment or email) and note the new date here: `__________`
- [ ] **Download the starter kit** from Google Classroom: `gigsim/`, `data/personas/` (3 personas), `data/policy_pack/`, the dev eval set (~200 jobs per persona), and the empty repo skeleton
- [ ] **Read the 2 Nov "platform policy update"** (the change request). Write the new rule or cost down. It has to live in `agent/config.yaml` or the policy pack, never in code
- [ ] Get free API keys: **Groq** or **OpenRouter** (hosted open models for the deployed `open` profile), **Langfuse Cloud** or self-hosted Phoenix (tracing), a **GitHub PAT** (GitHub MCP)
- [ ] Decide whether you have an **Upwork account** for live mode (A4 / X6). If you don't, build the adapter anyway and document it as untested-live

**Viva rule to keep in mind all weekend:** you're graded on what you can **explain and change live** (×0.5 if you can't). Commit small and often, because one giant commit near the deadline triggers a longer viva. Keep `docs/AI_USAGE.md` updated as you go.

---

## 🟦 SATURDAY: Foundation, MCP layer, RAG

### Block 1: Repo & infrastructure (~1.5 h)
- [ ] `git init`, copy the provided skeleton **exactly** (graders and the hidden test runner depend on these paths), create a public GitHub repo, push
- [ ] `pyproject.toml` with **pinned** deps (uv): langgraph, langgraph-checkpoint-postgres, langchain-mcp-adapters, mcp, fastapi, pydantic, arq, rank-bm25, sentence-transformers, psycopg, pgvector, langfuse
- [ ] `.env.example` lists every variable with no values. `.gitignore` covers `.env`, `data/processed/`, and the hidden set
- [ ] `infra/docker-compose.yml`: postgres (+**pgvector**, which serves as your vector store), redis, gigsim, ollama, api, worker, web, 3 MCP servers
- [ ] `docker compose up` brings up GigSim, Postgres and Redis healthy
- [ ] `Makefile` targets: `dev | test | eval | seed | demo | lint` (stubs are fine for now)
- [ ] `agent/config.yaml`: `open` + `paid` profiles, **per-node model** (small: scout/risk/compliance/intent; strong: writer/critic/liaison), fallback model, budgets (steps, tokens, p95, $), thresholds, marketplace mode `gigsim|upwork`, active RAG variant
- [ ] Commit ✅

### Block 2: MCP servers, Part A (~3 h)
- [ ] **`agent/marketplace_port.py`**: one abstract interface (search_jobs, get_job, get_client_history, get_connects, list_messages, create_proposal_draft, submit_proposal, send_message)
- [ ] `mcp_servers/marketplace/adapters/gigsim.py`: GigSim REST, with timeouts, retry+backoff on 429/5xx
- [ ] `mcp_servers/marketplace/adapters/upwork_live.py`: MultiServerMCPClient → `https://mcp.upwork.com/mcp` (Streamable HTTP, OAuth 2.1), tokens stored **encrypted** per user, drafts only
- [ ] **Marketplace MCP server**
  - [ ] Read tools: search jobs, get job, client history, Connects balance, list messages
  - [ ] Write tools: `create_proposal_draft`, `submit_proposal`, `send_message`, each with a constrained Pydantic input, an output schema, and `readOnlyHint` / `destructiveHint` / `idempotentHint`
  - [ ] **Idempotency key** on every write (a retried call never double-submits or double-spends)
  - [ ] **`dry_run`** returns the exact payload and the Connects cost
  - [ ] Bearer-token auth, per-user rate limiter (under GigSim limits), **append-only audit log** (user, tool, args, result, trace_id)
- [ ] **Portfolio MCP server** (read-only): `search_evidence(requirement, k, filters)`, `get_case_study(id)`, `list_skills()`, resources `portfolio://case/{id}` and `portfolio://proposal/{id}`, 2–3 prompt templates
- [ ] **Workspace MCP server** (local mock is fine): free slots, tentative hold, email draft
- [ ] Add **GitHub MCP** (third-party) for repo ingestion
- [ ] `agent/mcp_client.py`: MultiServerMCPClient + **per-agent tool allowlists** by name
- [ ] Tests: in-memory MCP client unit tests + **schema snapshot test** in `tests/contract/`
- [ ] Take **MCP Inspector screenshots** → `docs/images/`
- [ ] `docs/TOOLS.md`: one row per tool (name, server, R/W, annotations, auth) + a trust rationale for Upwork & GitHub
- [ ] Commit ✅

### Block 3: RAG, Part B (~3 h)
- [ ] `rag/ingest/`: loaders for PDF CV, MD case studies, READMEs + code (via GitHub MCP), reviews, past proposals
- [ ] Chunk metadata: persona, type, project, skills, date, client industry, **outcome** (proposals)
- [ ] Incremental re-index (hash-based) for UI uploads
- [ ] Variants in `rag/variants/` (minimum set that covers every *required* group):
  - [ ] **V0** naive dense (baseline)
  - [ ] **V1** requirement decomposition (Query group)
  - [ ] **V2** hybrid BM25 + dense with RRF (Retrieval group)
  - [ ] **V3** cross-encoder rerank, `bge-reranker-v2-m3` (Retrieval group)
  - [ ] **V8** corrective/self-RAG loop (Agentic group). *V4 outcome-aware is a cheap bonus*
- [ ] `rag/retriever.py`: factory, variant chosen by config
- [ ] `rag/verify.py`: claim splitting → supported / partial / unsupported, then rewrite or remove the unsupported ones
- [ ] `evals/runners/rag_eval.py`: Recall@5, MRR, nDCG@10 per persona → `evals/results/rag_*.json`
- [ ] **Write the hypothesis down BEFORE running** (R&D B)
- [ ] Run the comparison with one command. Write `docs/DATA_CARD.md`
- [ ] Commit ✅

**Saturday done when:** compose runs, 3 MCP servers pass their tests, and you have a RAG results JSON with V0 vs ≥4 variants.

---

## 🟩 SUNDAY: Graphs, UI, evaluation, deployment, deliverables

### Block 4: LangGraph, Part C (~3 h)
- [ ] `agent/handoffs.py`: JobSpec, RiskReport, FitReport, BidDecision, PriceQuote, ProposalDraft, Critique, ComplianceVerdict
- [ ] `agent/state.py`: TypedDict with separate fields + **reducers** for parallel writes. Keep state, execution trace and memory apart
- [ ] `agent/prompts/*.md`: one versioned file per node, **no inline prompts**
- [ ] **Bid graph**: scout → `Send` to analyst ‖ risk ‖ matcher → decide (skip / watch / bid) → pricing → writer → verifier → critic (**max 2 loops**) → compliance (can block) → **`interrupt()`** → submit (idempotent)
- [ ] Step budget + token budget → **"needs attention"** state. Dead-letter state, per-node retries/timeouts, fallback model
- [ ] Injection defence: delimit untrusted job/message text, run an injection detector in Risk, test it on the planted-injection jobs (attack success rate before and after)
- [ ] **Conversation graph**: liaison → classify intent (question / negotiation / interview / scam) → draft reply or slots → `interrupt()` → send
- [ ] **Postgres checkpointer** + LangGraph store. `agent/memory/promotion_rules.py`: **style memory** (e.g. an edit pattern seen ≥N times gets promoted) + **outcome memory** per category
- [ ] Demo crash-and-resume: kill API at the interrupt → restart → approve → submitted **exactly once**
- [ ] Render both graphs from code → `docs/images/`
- [ ] Commit ✅

### Block 5: API + worker, Parts D & E (~1.5 h)
- [ ] FastAPI routers: auth, jobs, proposals, approvals (resume thread with `Command(resume=...)`), messages, runs, analytics
- [ ] `apps/api/streaming.py`: **SSE** from LangGraph `stream_mode`
- [ ] PII masking on logs/traces (email, phone, payment). Every query is **tenant-scoped**
- [ ] One **trace_id** flows UI → API → graph → MCP → audit log (Langfuse/Phoenix, tokens + cost per node)
- [ ] `worker/` (Arq on Redis): scheduled scouting per saved search, idempotent job processing
- [ ] Generate the TS client from OpenAPI → `apps/web/src/lib/api/`
- [ ] Commit ✅

### Block 6: Web UI, Part D (~3 h)
- [ ] Next.js + TS. Screens: **sign-in/onboarding**, **inbox**, **jobs/[id]** (req→evidence matrix), **studio/[id]** (EvidenceHover, inline critic/compliance comments, regenerate paragraph, approve/edit/reject, dry_run preview), **pipeline** kanban, **messages**, **runs/[id]** live timeline, **analytics**, **settings** (autonomy, budgets, saved searches, memory viewer + delete)
- [ ] "Marketplace unavailable" degraded banner + queue/recover
- [ ] Mobile-responsive, keyboard-accessible, empty/loading/error states
- [ ] **5 Playwright tests**: onboarding, approve, reject-with-reason, edit draft, degraded banner
- [ ] Commit ✅

### Block 7: Evals, CI, R&D questions (~2 h)
- [ ] `make eval` → decision_eval (bid/skip F1, scam P/R, price-in-band), gigsim_outcomes (reply/hire rate), latency p50/p95, cost
- [ ] **Judge calibration**: hand-label 30 items, compute Cohen's κ → `evals/results/judge_calibration.json`
- [ ] **R&D A**: 40 tasks, fine vs coarse vs rewritten tool sets (selection acc., arg acc., tokens)
- [ ] **R&D B**: ablation of your best pipeline + per-persona / job-type breakdown
- [ ] **R&D C**: 50 jobs, ReAct agent vs `langgraph-supervisor` vs your graph
- [ ] `.github/workflows/ci.yml` (lint, unit, contract, Playwright, secret scan, `gigsim/` unchanged, prompts only in `agent/prompts/`) + `eval.yml` (30-job smoke eval with thresholds)
- [ ] Commit ✅

### Block 8: Deploy (~1 h)
- [ ] Deploy (e.g. Render / Railway / Fly + Neon Postgres + Upstash Redis) on the **open profile** via Groq/OpenRouter
- [ ] Seed a **reviewer login** preloaded with a persona, and test the public URL in an incognito window
- [ ] It must **stay live until grades are released** (the hidden test run hits it)

### Block 9: Deliverables (~2 h)
- [ ] **Signature extension**: pick **X2 (A/B proposal experiments)**, the cheapest since it reuses the GigSim eval. Write `docs/EXTENSION.md`
- [ ] Docs: `README.md` (demo GIF, architecture diagram, RAG table, multi-agent table, 3 trace links, live URL, pinned versions), `ARCHITECTURE.md`, `RUNBOOK.md` (justify the live polling interval), `adr/`, `AI_USAGE.md`
- [ ] Every number in README/report links to `evals/results/*`
- [ ] **Publish the Portfolio MCP server** (PyPI or a `uvx` / Docker command) with its own README
- [ ] **3-min demo video**: inbox → approved proposal, scam caught, injection ignored, client reply handled
- [ ] **R&D report PDF** (≤10 pages, the 10 sections incl. change request, a failure taxonomy of ≥10 jobs, hidden-test budgets)
- [ ] **Technical write-up** (1,200–1,800 words, a real finding, not a tutorial) on Medium/LinkedIn
- [ ] Two-line résumé bullet with real numbers
- [ ] Final commit + push

### Submit on Google Classroom
- [ ] Deployed URL
- [ ] Reviewer credentials as a **private comment** (not in the repo)
- [ ] GitHub repo link
- [ ] R&D report PDF
- [ ] Demo video link
- [ ] Portfolio MCP link + write-up link

---

## 🔻 If you run out of time, protect these first (highest marks per hour)
1. Working **bid graph** with parallel `Send`, critic loop, `interrupt()` and Postgres checkpointer (C = 25)
2. **3 MCP servers** with schemas, idempotency and tests, plus the marketplace port (A = 20)
3. **V0 + 4 variants + claim verification** with a results JSON (B = 20)
4. **Live URL with reviewer login**, because if it's down during the hidden run you lose the E marks
5. UI: inbox, studio with approve/edit/reject, SSE run viewer (D)
6. An honest README and report: negative, well-measured results score better than unmeasured claims

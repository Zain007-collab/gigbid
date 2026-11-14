# Runbook

## Start
- Local, everything: `docker compose -f infra/docker-compose.yml up --build` (add `--profile local-llm` for Ollama).
- Local, light: `GIGSIM_EMBEDDED=1 GIGSIM_BASE_URL=http://127.0.0.1:8000/gigsim uvicorn apps.api.main:app --port 8000`
  and `cd apps/web && npm run dev`.
- Reviewer login: set `REVIEWER_EMAIL` / `REVIEWER_PASSWORD`; on first start the account is created with the AI/ML
  persona and its inbox is filled by one scout run.

## Schedules (E1)
| Job | GigSim | Live Upwork | Why |
|---|---|---|---|
| Scout saved searches | every 30 min | **every 180 min** | Upwork treats unapproved bots and anything resembling scraping as violations. Three-hourly reads of the user's own saved searches through the official MCP server are comparable to a person checking a few times a day, keep us far below any published limit, and still surface jobs well within the first day (most proposals arrive in the first 24 h). |
| Pipeline / message sync | every 10 min | every 10 min | Reads only the user's own threads; replies are time-sensitive. |
| Max jobs per scout run | 25 | 25 | Bounds analysis cost and Connects exposure per run. |
All writes require a human approval and, in live mode, a confirmation on Upwork.

## Degraded mode drill (viva Q1)
1. `curl -X POST -H "x-admin-token: $GIGSIM_ADMIN_TOKEN" -H content-type:application/json -d '{"down":true}' $URL/gigsim/admin/chaos`
   (or stop the `marketplace-mcp` container).
2. The UI shows *Marketplace unavailable* within one health poll (`apps/api/routers/core.py::health` →
   `agent/mcp_client.py::marketplace_healthy`). Running threads pause at the failing node (`agent/nodes/base.py`
   re-raises `MarketplaceDown`) and are re-queued by `worker/tasks.py::retry_bid`.
3. Bring it back (`{"down":false}`): the banner clears and queued threads resume from their checkpoints.

## Crash-and-resume drill (viva Q2)
Approve a proposal, kill the worker/API before it submits, restart, and check GigSim has exactly one proposal:
the submit node's writes use keys `bid:<user>:<job>:draft:v<n>` and `...:submit`; the Marketplace server's
idempotency table (SQLite, survives restarts) replays the first result, and if the crash happened after GigSim
accepted the write but before we recorded it, `reconcile()` asks GigSim by idempotency key.

## Change request procedure
1. Read the policy update. 2. Edit `agent/config.yaml` `policy:` (Connects multiplier / surcharge / bidding rules) and,
if it is a content rule, `data/policy_pack/` + the rule list in `agent/nodes/proposal.py::policy_rules`.
3. `make test && make eval`. GigSim simulates the update as policy v2 (`GIGSIM_POLICY_VERSION=2` or
`POST /gigsim/admin/policy/2`): +2 Connects on jobs with budget ≥ $1,000, max 2 proposals per client per week.

## Declared budgets (E5)
| Budget | Declared | Where measured |
|---|---|---|
| p95 latency per job analysed | 20 s | `evals/results/decision_*` → `latency_job_s.p95` |
| p95 latency per proposal | 60 s | `latency_proposal_s.p95` |
| Cost per job analysed | $0.002 | `usd_per_job` |
| Cost per proposal | $0.01 | `usd_per_proposal` |
| Steps / tokens per thread | 40 / 60,000 | enforced in `agent/nodes/base.py` |

## Secrets
Only from environment variables; `.env` is git-ignored and CI fails on committed secrets. OAuth tokens are
Fernet-encrypted per user (`apps/api/auth/token_store.py`) and masked in every log line.

## Deploy (public URL)
1. **Database:** create a free Neon Postgres; copy the `postgresql://…` URL (checkpointer, memory store, app tables).
2. **Space:** create a *Docker* Space on Hugging Face (free CPU, 16 GB RAM — enough for the open embedding models).
3. **Secrets** (Space → Settings → Variables and secrets): `DATABASE_URL`, `GROQ_API_KEY`, `SESSION_SECRET`,
   `TOKEN_ENCRYPTION_KEY`, `REVIEWER_EMAIL`, `REVIEWER_PASSWORD`, `GIGSIM_ADMIN_TOKEN`.
4. `python scripts/deploy_hf_space.py` (reads `.env`; sets the secrets above through the HF API; uploads committed files only)
5. Open `https://<you>-gigbid.hf.space`, sign in with the reviewer account, and check `/api/health`.
The image (`infra/docker/app.Dockerfile`) serves the built UI, the API, the embedded GigSim at `/gigsim` and runs the
worker inline (bounded to 3 concurrent runs). Keep the Space running until grades are released.

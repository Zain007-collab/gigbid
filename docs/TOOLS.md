# Tools (MCP surface)

Generated from the schema snapshots in `tests/contract/snapshots/` (CI fails if a signature changes unintentionally;
accept intentional changes with `make snapshots`). Owners: all three servers are maintained in this repo.

| Tool | Server | Read/Write | Annotations | Auth | Notes |
|---|---|---|---|---|---|
| `create_proposal_draft` | marketplace | write | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=false | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log | idempotency key required; dry_run (default true) + approval_id gate |
| `get_client_history` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `get_connects_balance` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `get_job` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `get_market_stats` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `list_messages` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `list_proposals` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `search_jobs` | marketplace | read | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=true | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log |  |
| `send_message` | marketplace | write | destructiveHint=false, idempotentHint=true, openWorldHint=true, readOnlyHint=false | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log | idempotency key required; dry_run (default true) + approval_id gate |
| `submit_proposal` | marketplace | write | destructiveHint=true, idempotentHint=true, openWorldHint=true, readOnlyHint=false | Bearer service token + X-GigBid-User (HTTP); per-user rate limit; audit log | idempotency key required; dry_run (default true) + approval_id gate |
| `get_case_study` | portfolio | read | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=true | Bearer service token (HTTP) / none over stdio (local, read-only) |  |
| `list_skills` | portfolio | read | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=true | Bearer service token (HTTP) / none over stdio (local, read-only) |  |
| `search_evidence` | portfolio | read | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=true | Bearer service token (HTTP) / none over stdio (local, read-only) |  |
| `create_email_draft` | workspace | write | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=false | Bearer service token (HTTP) | idempotency key required |
| `create_interview_hold` | workspace | write | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=false | Bearer service token (HTTP) | idempotency key required |
| `free_slots` | workspace | read | destructiveHint=false, idempotentHint=true, openWorldHint=false, readOnlyHint=true | Bearer service token (HTTP) |  |

**Resources (Portfolio):** `portfolio://case/{id}`, `portfolio://proposal/{id}`, `portfolio://cv`.
**Prompts (Portfolio):** *Answer a screening question from my portfolio*, *Find evidence for every requirement in a job
post*, *Tighten a proposal and check every claim*.

## Per-agent allowlists (`agent/mcp_client.py`)

| Agent | Tools |
|---|---|
| Scout | `marketplace.search_jobs`, `marketplace.get_job` |
| Intake | `marketplace.get_job` |
| Risk Assessor | `marketplace.get_client_history` |
| Fit Matcher | `portfolio.search_evidence`, `portfolio.list_skills` |
| Pricing Strategist | `marketplace.get_connects_balance`, `marketplace.get_market_stats` |
| Proposal Writer | `portfolio.search_evidence`, `portfolio.get_case_study` |
| Verifier | `portfolio.search_evidence`, `portfolio.get_case_study` |
| Critic | `portfolio.search_evidence` |
| Compliance Guard | — (pure function over the policy pack) |
| Approval (previews only) | `marketplace.create_proposal_draft` (dry_run), `marketplace.send_message` (dry_run) |
| Submit (after approval) | `marketplace.create_proposal_draft`, `marketplace.submit_proposal` |
| Client Liaison | `marketplace.list_messages`, `portfolio.search_evidence`, `workspace.free_slots` |
| Send (after approval) | `marketplace.send_message`, `workspace.create_interview_hold` |

A call outside an agent's allowlist raises `ToolNotAllowed` before any network traffic.

## Client discipline
Timeouts per server (marketplace 20 s, portfolio 30 s, workspace 10 s); bounded retries (3) with jittered backoff that
honours `retry_after` hints; the Marketplace server **queues** callers up to 15 s inside its per-user token bucket
(2 req/s, burst 5 — under GigSim's 5 req/s) instead of failing them; anything that still fails becomes
`MarketplaceDown`, which pauses the graph at the failed node (checkpointed) and shows the degraded-mode banner. The
worker resumes the node later (`worker/tasks.py::retry_bid`).

## Third-party servers and why we trust them

| Server | Used for | Trust rationale |
|---|---|---|
| Upwork official MCP (`https://mcp.upwork.com/mcp`) | live-mode jobs, drafts, messages | First-party server operated by the marketplace itself, OAuth 2.1 with user consent, and every write is a draft the user confirms on Upwork — so it cannot act beyond what the platform already lets the user do. |
| GitHub MCP server (`github/github-mcp-server`) | repo ingestion for the portfolio (READMEs and code) | Official GitHub server, run with a read-only fine-grained PAT scoped to public repositories; GigBid only calls read tools and never pushes. |

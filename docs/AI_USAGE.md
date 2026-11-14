# AI usage

**Tool:** Claude Code (Anthropic, Claude Opus 5.5) in VS Code, used as a pair programmer.
**Owner of every line:** me. I reviewed, ran and can explain each file; the viva is the check.

| Area | Files | How AI was used |
|---|---|---|
| MCP servers | `mcp_servers/**` | drafted servers, schemas, guards, tests; I specified the safety rules (dry_run default, approval id, idempotency) |
| Graphs and nodes | `agent/**` | drafted nodes, state, wrapper, memory rules; I chose the graph shape and thresholds |
| RAG | `rag/**`, `evals/runners/rag_eval.py`, `ablation.py` | drafted pipeline and metrics; variant choice came from the measured results |
| Simulator and data | `gigsim/**`, `datagen/**` | drafted the generator and simulator from my spec of personas, scams, injections and client preferences |
| API, worker, UI | `apps/**`, `worker/**` | drafted routes, SSE bridge, React screens, Playwright tests |
| Docs | `docs/**`, `README.md` | drafted; every number regenerated from `evals/results` |

## Things the AI got wrong that we caught (and how)
1. **Wrote against the MCP SDK 1.x API while 2.3.0 was installed** — tests failed to import
   `create_connected_server_and_client_session`; pinned `mcp>=1.10,<2` (what langchain-mcp-adapters targets).
2. **Assumed tool errors start with our error code** — FastMCP prefixes "Error executing tool …", so degraded-mode
   detection would never fire; caught by `test_marketplace_down_reports_unavailable`.
3. **Sent `Authorization: Bearer ` with an empty token** — h11 rejects the header, so every GigSim call failed and
   the UI showed "marketplace unavailable"; caught in the first local smoke run.
4. **PII log filter broke uvicorn's access logger and masked IP addresses as phone numbers** — fixed the filter to keep
   argument arity and excluded dotted numbers from the phone regex; regression test added.
5. **Security bug: GigSim's admin token check compared the full path**, so under the `/gigsim` mount `/admin/*` was
   open, and a "down" simulator also blocked its own recovery call; caught by the degraded-mode Playwright test,
   fixed with mount-relative paths and a regression test.
6. **Rate limiter rejected instead of queueing** — a burst of 25 analyses degraded 12–19 jobs; changed the
   Marketplace server's token bucket to queue callers for up to 15 s.
7. **Guessed an evidence threshold (0.55)** — the dev sweep showed it over-bid (precision 0.73); 0.65 was chosen on
   dev and confirmed on the untouched holdout split.
8. **Picked bge-reranker-base without checking CPU cost** — measured at ~6 pairs/s on this laptop
   (`evals/results/reranker_speed.json`), replaced by a MiniLM cross-encoder, which then *lowered* recall anyway.
9. `git add -A` committed the instructor's starter-kit folder that had been copied into the project; untracked it.

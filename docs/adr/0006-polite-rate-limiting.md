# ADR 0006 — Queue, don't reject, under the per-user rate limit
**Status:** accepted. **Context:** rejecting over-limit calls degraded half of a 25-job scouting burst. **Decision:**
the Marketplace server reserves a token and sleeps (≤15 s) before calling the marketplace; inline work is bounded to 3
concurrent runs (Arq worker: `max_jobs=4`). **Consequence:** bursts slow down instead of failing; GigSim never sees
more than 2 req/s per user.

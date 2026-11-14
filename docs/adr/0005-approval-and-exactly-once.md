# ADR 0005 — Human approval via interrupt, exactly-once writes via idempotency keys
**Status:** accepted. **Decision:** `interrupt()` before any proposal, message or Connects spend; the submit node's
writes carry keys derived from thread id and draft version; the Marketplace server stores keys durably and
reconciles with GigSim after a crash. **Why:** approvals must survive restarts and double clicks without double
submission. **Verified by:** `tests/integration/test_bid_graph.py`, `test_api_flow.py`, Playwright approve test.

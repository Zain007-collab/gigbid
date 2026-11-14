# ADR 0002 — One marketplace port, served as an MCP server
**Status:** accepted. **Decision:** the Marketplace MCP server is the only caller of the port; adapters (GigSim REST,
Upwork official MCP) implement it. Writes default to `dry_run`, need an approval id and an idempotency key.
**Why:** the graph cannot tell which marketplace it uses (R1), safety rules live in one place, and live mode can only
create drafts. **Trade-off:** one extra hop; in the single-container deploy the hop is the in-memory MCP transport.

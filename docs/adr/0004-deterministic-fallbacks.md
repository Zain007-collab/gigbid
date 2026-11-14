# ADR 0004 — Every LLM node has a deterministic fallback
**Status:** accepted. **Decision:** if a node's model (and its fallback model) is unavailable, the node uses a
rule/template path that only states what evidence says. **Why:** free hosted tiers rate-limit, CI has no keys, and
safety-critical checks (risk rules, compliance, claim verification) must not depend on a model. **Trade-off:**
fallback proposals are formulaic; the LLM path is used whenever a key is configured.

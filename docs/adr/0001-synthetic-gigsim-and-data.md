# ADR 0001 — Build our own GigSim and synthetic corpora
**Status:** accepted. **Context:** no simulator, personas or eval sets were supplied; the instructor allowed public
datasets plus clearly labelled synthetic data. **Decision:** one seeded generator (`datagen/`) produces personas,
clients with hidden preferences, jobs (scams, injections, duplicates) and gold labels; GigSim is a FastAPI service
with a transparent client model. **Consequences:** fully reproducible, all ground truth known; results measure fit to
our simulator, not real clients (stated in the data card). GigSim is frozen by checksum in CI.

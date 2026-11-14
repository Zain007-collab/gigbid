.PHONY: dev test eval seed demo lint lock snapshots

UV ?= uv

dev:            ## start the full stack locally
	docker compose -f infra/docker-compose.yml up --build

test:           ## unit + contract tests
	$(UV) run pytest -q

snapshots:      ## intentionally accept MCP tool-schema changes
	UPDATE_SNAPSHOTS=1 $(UV) run pytest -q tests/contract

eval:           ## retrieval, decision and GigSim reply-rate evals -> evals/results/*.json
	$(UV) run python -m evals.runners.rag_eval
	$(UV) run python -m evals.runners.decision_eval
	$(UV) run python -m evals.runners.gigsim_outcomes

seed:           ## ingest persona corpora into the indexes
	$(UV) run python -m rag.ingest.run --all-personas

demo:           ## seed + reviewer account
	$(UV) run python -m apps.api.seed_demo

lint:
	$(UV) run ruff check .

lock:
	$(UV) lock

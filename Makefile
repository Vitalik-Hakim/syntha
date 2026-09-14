SHELL := /bin/bash
.DEFAULT_GOAL := help

ALEMBIC := uv run alembic -c packages/storage/alembic.ini
WEB := pnpm --dir apps/web

-include .env
# Off the common 3000/8000 so Syntha can run alongside other local projects. Override in .env.
SYNTHA_WEB_PORT ?= 3100
SYNTHA_API_PORT ?= 8100
export

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup

.PHONY: setup
setup: ## Install Python (uv) and web (pnpm) dependencies
	uv sync --all-packages
	$(WEB) install

# ---------------------------------------------------------------- infra

.PHONY: up
up: ## Start Postgres+Timescale and Redis, wait until healthy
	docker compose up -d --wait

.PHONY: down
down: ## Stop infra containers (data volume is kept)
	docker compose down

.PHONY: db-reset
db-reset: ## DESTRUCTIVE: delete the local Postgres volume, re-init and migrate
	docker compose down -v
	docker compose up -d --wait
	$(ALEMBIC) upgrade head

.PHONY: migrate
migrate: ## Apply database migrations
	$(ALEMBIC) upgrade head

# ---------------------------------------------------------------- dev

.PHONY: dev
dev: check-ports setup up migrate ## Run infra, API, worker and web (ports: SYNTHA_API_PORT, SYNTHA_WEB_PORT)
	@echo "Syntha → web http://localhost:$(SYNTHA_WEB_PORT)  api http://localhost:$(SYNTHA_API_PORT)/docs"
	@$(MAKE) --no-print-directory -j3 dev-api dev-worker dev-web

# Fail before starting anything if a dev port is taken, and say by what.
.PHONY: check-ports
check-ports:
	@command -v lsof >/dev/null || exit 0; \
	for port in $(SYNTHA_WEB_PORT) $(SYNTHA_API_PORT); do \
	  owner=$$(lsof -nP -iTCP:$$port -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $$1 " (pid " $$2 ")"}'); \
	  if [ -n "$$owner" ]; then \
	    echo "Port $$port is already in use by $$owner." >&2; \
	    echo "Stop that process, or pick another port in .env (SYNTHA_WEB_PORT / SYNTHA_API_PORT)." >&2; \
	    exit 1; \
	  fi; \
	done

.PHONY: dev-api
dev-api:
	uv run uvicorn syntha_api.main:app --reload --port $(SYNTHA_API_PORT)

.PHONY: dev-worker
dev-worker:
	uv run python -m syntha_worker

.PHONY: dev-web
dev-web:
	$(WEB) exec next dev --port $(SYNTHA_WEB_PORT)

# ---------------------------------------------------------------- quality

.PHONY: lint
lint: lint-py lint-web ## Lint and type-check everything

.PHONY: lint-py
lint-py:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy -p syntha_contracts -p syntha_engine -p syntha_storage -p syntha_api \
		-p syntha_worker -p syntha_ingestion -p syntha_recorder

.PHONY: lint-web
lint-web:
	$(WEB) lint
	$(WEB) typecheck

.PHONY: fmt
fmt: ## Auto-format Python
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: check-engine-io
check-engine-io: ## Fail if packages/engine performs or imports I/O (PRD §6)
	uv run python tools/check_engine_io.py packages/engine/src

.PHONY: test
test: ## Run Python tests (DB tests run when SYNTHA_TEST_DATABASE_URL is set)
	uv run pytest

.PHONY: schemas
schemas: ## Regenerate published JSON Schemas from the Pydantic contracts
	uv run python tools/gen_schemas.py

.PHONY: build-web
build-web:
	$(WEB) build

.PHONY: ci
ci: lint check-engine-io test build-web ## Everything CI runs

# ---------------------------------------------------------------- data (M1)

# The M1 dataset: a seeded, stratified sample of markets scheduled to end in July 2026.
COLLECTION ?= july-2026-stratified-600
SAMPLE_START ?= 2026-07-01T00:00:00Z
SAMPLE_END ?= 2026-08-01T00:00:00Z
SAMPLE_N ?= 600
SAMPLE_SEED ?= 20260914
INGEST := uv run syntha-ingest

.PHONY: ingest-sample
ingest-sample: ## Draw the collection sample (writes an immutable manifest to data/raw/_manifests)
	$(INGEST) sample --name $(COLLECTION) --end-date-min $(SAMPLE_START) \
		--end-date-max $(SAMPLE_END) --n $(SAMPLE_N) --seed $(SAMPLE_SEED)

.PHONY: ingest-fetch
ingest-fetch: ## Fetch metadata, resolutions, prints and 1-minute prices into data/raw (resumable)
	$(INGEST) fetch --name $(COLLECTION)

.PHONY: rebuild-normalized
rebuild-normalized: ## Rebuild the normalized tables from data/raw alone and record a dataset version
	$(INGEST) normalize

.PHONY: verify-normalized
verify-normalized: ## Check the normalized tables still match the latest dataset version hash
	$(INGEST) verify

.PHONY: data-report
data-report: ## Print the latest data-quality report
	$(INGEST) report

.PHONY: m1-acceptance
m1-acceptance: ## Rebuild twice (hashes must match) and check 500+ markets have full 1-minute history
	uv run python tools/m1_acceptance.py

# ---------------------------------------------------------------- engine (M2)

.PHONY: m2-acceptance
m2-acceptance: check-engine-io ## Golden fixtures, look-ahead attacks, determinism, properties
	uv run pytest -q packages/engine/tests/test_golden.py packages/engine/tests/test_lookahead_adversarial.py \
		packages/engine/tests/test_determinism.py packages/engine/tests/test_properties.py \
		packages/engine/tests/test_features_causal.py packages/engine/tests/test_episodes.py

.PHONY: bench-engine
bench-engine: ## Engine benchmark at the PRD target (1,000 markets × 90 days of 1-minute bars)
	uv run python tools/bench_engine.py 1000 90

.PHONY: recorder
recorder: ## Record the live order book (top 10 markets by 24h volume) into Postgres and data/raw
	uv run python -m syntha_recorder

SHELL := /bin/bash
.DEFAULT_GOAL := help

ALEMBIC := uv run alembic -c packages/storage/alembic.ini
WEB := pnpm --dir apps/web

-include .env
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

.PHONY: migrate
migrate: ## Apply database migrations
	$(ALEMBIC) upgrade head

# ---------------------------------------------------------------- dev

.PHONY: dev
dev: setup up migrate ## Run infra, API (:8000), worker and web (:3000)
	@$(MAKE) --no-print-directory -j3 dev-api dev-worker dev-web

.PHONY: dev-api
dev-api:
	uv run uvicorn syntha_api.main:app --reload --port 8000

.PHONY: dev-worker
dev-worker:
	uv run python -m syntha_worker

.PHONY: dev-web
dev-web:
	$(WEB) dev

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

.PHONY: build-web
build-web:
	$(WEB) build

.PHONY: ci
ci: lint check-engine-io test build-web ## Everything CI runs

# ---------------------------------------------------------------- data (M1)

.PHONY: rebuild-normalized
rebuild-normalized: ## Rebuild normalized/ from raw/ (implemented in M1)
	@echo "rebuild-normalized is implemented in milestone M1" >&2; exit 1

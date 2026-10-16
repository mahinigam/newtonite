# Newtonite - Operations Under Pressure
#
# Thin wrapper so a reviewer does not have to remember individual commands.
# Every target wraps a documented command; run `make help` to list them.

BACKEND := backend

.PHONY: help setup db-up db-down migrate seed dev-api test test-api lint typecheck check

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend dependencies from the lockfile
	cd $(BACKEND) && uv sync --group dev

db-up: ## Start the docker-compose PostgreSQL (host port 5433)
	docker compose up -d db

db-down: ## Stop the docker-compose PostgreSQL
	docker compose down

migrate: ## Apply database migrations to head
	cd $(BACKEND) && uv run alembic upgrade head

seed: ## Load deterministic demo data (idempotent; ITEMS=200 for a small set)
	cd $(BACKEND) && uv run python -m scripts.seed --items $(or $(ITEMS),3000)

dev-api: ## Run the API with autoreload on http://localhost:8000
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

test-api: ## Run the backend test suite against PostgreSQL
	cd $(BACKEND) && uv run pytest -q

test: test-api ## Run every test suite

lint: ## Format check and lint the backend
	cd $(BACKEND) && uv run ruff format --check . && uv run ruff check .

typecheck: ## Static type check the backend
	cd $(BACKEND) && uv run mypy .

check: lint typecheck test ## Run every quality gate

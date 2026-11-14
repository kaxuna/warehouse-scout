.DEFAULT_GOAL := help
.PHONY: help sync up down reset logs lint fmt typecheck test test-integration check load noise querylog copy-target hello psql sqlplus

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

sync: ## Install dependencies into .venv
	uv sync

.env:
	cp .env.example .env
	@echo "Created .env from .env.example — add your ANTHROPIC_API_KEY there."

up: .env ## Start Postgres and Oracle, wait until both are healthy
	docker compose up -d --wait

down: ## Stop the databases (data is kept)
	docker compose down

reset: ## Stop the databases and DELETE their data
	docker compose down --volumes

logs: ## Follow database logs
	docker compose logs -f

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static type check
	uv run mypy

test: ## Unit tests (no databases needed)
	uv run pytest -m "not integration"

test-integration: ## Tests that need the databases (run `make up` first)
	uv run pytest -m integration

check: lint typecheck test ## Everything CI runs

load: ## Load TPC-DS into Oracle; pass options via ARGS="--scale-factor 0.1"
	uv run python -m warehouse_scout.ingest.load_benchmark $(ARGS)

noise: ## Create junk tables, views and functions in Oracle (after `make load`)
	uv run python -m warehouse_scout.ingest.noise_tables $(ARGS)

querylog: ## Generate the synthetic query log into data/query_log.jsonl
	uv run python -m warehouse_scout.ingest.query_log $(ARGS)

copy-target: ## Copy all Oracle tables (definitions and rows) into Postgres
	uv run python -m warehouse_scout.migrate.copy_tables $(ARGS)

hello: ## First Claude API call (needs ANTHROPIC_API_KEY in .env)
	uv run python -m warehouse_scout.llm.hello

psql: ## Open a Postgres shell
	docker compose exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

sqlplus: ## Open an Oracle shell as the TPC-DS schema owner
	docker compose exec oracle sh -c 'sqlplus "$$APP_USER/$$APP_USER_PASSWORD@FREEPDB1"'

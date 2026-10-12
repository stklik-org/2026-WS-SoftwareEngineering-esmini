# Developer commands. `make help` lists them.
IMAGE        ?= esmini-rest:dev
VERSION      ?= 0.1.0-dev
PLATFORM     ?= linux/amd64
PORT         ?= 8080
# The integration tests use their own compose project and port, so they do not clash with `make up`.
TEST_PROJECT := esmini-rest-test
TEST_PORT    ?= 18080
TEST_ENV     := HOST_PORT=$(TEST_PORT) MAX_CONCURRENT_JOBS=2 MAX_QUEUED_JOBS=2 ESMINI_IMAGE=$(IMAGE)

.DEFAULT_GOAL := help
.PHONY: help build run up down logs smoke test test-unit test-integration lint format openapi help-texts clean

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

build: ## Build the Docker image (linux/amd64)
	docker build --platform $(PLATFORM) -f docker/Dockerfile -t $(IMAGE) --build-arg IMAGE_VERSION=$(VERSION) .

run: ## Run the image in the foreground on http://127.0.0.1:8080 (override with PORT=...)
	docker run --rm --platform $(PLATFORM) -p 127.0.0.1:$(PORT):8080 $(IMAGE)

up: ## Build and start the service in the background with Docker Compose
	HOST_PORT=$(PORT) ESMINI_IMAGE=$(IMAGE) docker compose up -d --build --wait
	@echo "Service ready: http://127.0.0.1:$(PORT)/docs"

down: ## Stop the service started with `make up`
	docker compose down

logs: ## Follow the service log
	docker compose logs -f

smoke: ## Exercise the running service with curl (see scripts/smoke.sh)
	scripts/smoke.sh http://127.0.0.1:$(PORT)

test: lint test-unit ## Lint, unit and contract tests (no Docker needed)

test-unit: ## Unit and contract tests (no Docker needed)
	uv run pytest tests/unit tests/contract

test-integration: build ## Build the image, start a test container and run the acceptance tests against it
	$(TEST_ENV) docker compose -p $(TEST_PROJECT) up -d --wait
	ESMINI_URL=http://127.0.0.1:$(TEST_PORT) \
	ESMINI_CONTAINER=$$(docker compose -p $(TEST_PROJECT) ps -q esmini) \
	uv run pytest -m integration tests/integration; \
	status=$$?; $(TEST_ENV) docker compose -p $(TEST_PROJECT) down; exit $$status

lint: ## Static checks (ruff; hadolint via Docker)
	uv run ruff check .
	uv run ruff format --check .
	docker run --rm -i -v "$(CURDIR)/.hadolint.yaml:/.config/hadolint.yaml" hadolint/hadolint:v2.12.0 < docker/Dockerfile

format: ## Format the code
	uv run ruff format .
	uv run ruff check --fix .

openapi: ## Regenerate docs/openapi.json from the code
	uv run python scripts/export_openapi.py

help-texts: build ## Capture the esmini --help texts from the image into docs/reference/
	scripts/capture_help_texts.sh $(IMAGE)

clean: ## Remove local caches and smoke-test output
	rm -rf .pytest_cache .ruff_cache smoke-output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

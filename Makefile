.DEFAULT_GOAL := help
PYTHON ?= python3
PYTEST_ARGS ?=

## ─── Installation ──────────────────────────────────────────────
.PHONY: install
install: ## Install package in editable mode with dev deps
	$(PYTHON) -m pip install -e ".[dev]"

## ─── Quality ───────────────────────────────────────────────────
.PHONY: lint
lint: ## Run ruff linter
	$(PYTHON) -m ruff check src/ tests/ framework/tools/

.PHONY: lint-fix
lint-fix: ## Run ruff linter with auto-fix
	$(PYTHON) -m ruff check src/ tests/ framework/tools/ --fix

.PHONY: format
format: ## Run ruff formatter
	$(PYTHON) -m ruff format src/ tests/

.PHONY: format-check
format-check: ## Check formatting without modifying files
	$(PYTHON) -m ruff format src/ tests/ --check

.PHONY: typecheck
typecheck: ## Run mypy strict type checking (src + tests)
	$(PYTHON) -m mypy --strict src/grimoire/ tests/

## ─── Tests ─────────────────────────────────────────────────────
.PHONY: test
test: ## Run all unit tests
	$(PYTHON) -m pytest tests/unit/ -q --tb=short -x $(PYTEST_ARGS)

.PHONY: test-all
test-all: ## Run all tests (unit + integration)
	$(PYTHON) -m pytest tests/ -q --tb=short $(PYTEST_ARGS)

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/unit/ --cov=src/grimoire --cov-report=term-missing --cov-report=html --cov-fail-under=70 $(PYTEST_ARGS)

## ─── Build ─────────────────────────────────────────────────────
.PHONY: build
build: ## Build sdist + wheel
	$(PYTHON) -m build

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ *.egg-info src/*.egg-info site/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml

## ─── Compound ──────────────────────────────────────────────────
.PHONY: check
check: lint typecheck test ## Run lint + typecheck + tests

.PHONY: pre-push
pre-push: lint format-check typecheck test-cov ## Full pre-push validation

.PHONY: audit
audit: ## Run security audit (pip-audit)
	$(PYTHON) -m pip_audit --strict --desc

.PHONY: bench
bench: ## Run performance benchmarks
	$(PYTHON) -m pytest tests/ -q -m bench --tb=short $(PYTEST_ARGS)

.PHONY: release
release: check ## Release — bump version, tag, build
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=x.y.z" && exit 1)
	@echo "$(VERSION)" > version.txt
	@sed -i 's/^__version__.*/__version__ = "$(VERSION)"/' src/grimoire/__version__.py
	$(PYTHON) -m build
	@echo "\n\033[32m✓ Built $(VERSION). Tag and push when ready:\033[0m"
	@echo "  git add version.txt src/grimoire/__version__.py"
	@echo "  git commit -m 'chore: release $(VERSION)'"
	@echo "  git tag -a v$(VERSION) -m 'Release $(VERSION)'"
	@echo "  git push origin main --tags"

## ─── Docs ──────────────────────────────────────────────────────
.PHONY: docs
docs: ## Build documentation site
	$(PYTHON) -m mkdocs build --strict

.PHONY: docs-serve
docs-serve: ## Serve documentation locally (hot-reload)
	$(PYTHON) -m mkdocs serve

## ─── Help ──────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

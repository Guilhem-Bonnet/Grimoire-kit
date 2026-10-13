.DEFAULT_GOAL := help
VENV ?= .venv
# Auto-detect venv Python to avoid PEP 668 errors on Ubuntu/Debian
PYTHON ?= $(shell [ -f "$(VENV)/bin/python3" ] && echo "$(VENV)/bin/python3" || echo "python3")
PYTEST_ARGS ?=

## ─── Environment ────────────────────────────────────────────────
.PHONY: venv
venv: ## Create virtual environment ($(VENV)/)
	@test -d $(VENV) || (python3 -m venv $(VENV) && $(VENV)/bin/pip install --upgrade pip --quiet)
	@echo "✓ venv ready — activate with: source $(VENV)/bin/activate"

.PHONY: dev
dev: ## Full dev setup: create venv + install all dev deps + pre-commit
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip --quiet
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/pre-commit install
	@echo "\n✓ Dev environment ready. Run: source $(VENV)/bin/activate"

## ─── Installation ──────────────────────────────────────────────
.PHONY: install
install: ## Install package in editable mode with dev deps
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]"

## ─── Quality ───────────────────────────────────────────────────
.PHONY: lint
lint: ## Run ruff linter
	$(PYTHON) -m ruff check src/ tests/ framework/tools/ framework/memory/
	$(PYTHON) scripts/check-emitted-prose.py

.PHONY: lint-fix
lint-fix: ## Run ruff linter with auto-fix
	$(PYTHON) -m ruff check src/ tests/ framework/tools/ framework/memory/ --fix

.PHONY: ratchet
ratchet: ## Code-size ratchet (framework frozen, src oversize guard)
	$(PYTHON) scripts/check-code-ratchet.py

.PHONY: format
format: ## Run ruff formatter
	$(PYTHON) -m ruff format src/ tests/

.PHONY: format-check
format-check: ## Check formatting without modifying files
	$(PYTHON) -m ruff format src/ tests/ --check

.PHONY: typecheck
typecheck: ## Run mypy strict type checking (src — même périmètre que le pre-commit et la CI)
	$(PYTHON) -m mypy --strict src/grimoire/

.PHONY: typecheck-tests
typecheck-tests: ## Typecheck les tests — hors gate, dette connue (~7300 erreurs)
	$(PYTHON) -m mypy --strict tests/

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

.PHONY: wheel-check
wheel-check: build ## Install the freshest built wheel in a new venv and start it — the pre-release check TestPyPI never did (#195)
	$(PYTHON) -m venv --clear .wheel-check-venv
	.wheel-check-venv/bin/python -m pip install -q "$$(ls -t dist/*.whl | head -1)"
	.wheel-check-venv/bin/grimoire --version
	.wheel-check-venv/bin/python -c "from grimoire.core.config import GrimoireConfig; print('SDK OK')"

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
check: lint ratchet typecheck test ## Run lint + ratchet + typecheck + tests

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
	@# Record this release's files in the shipped-content catalog. Skipping it
	@# would make `grimoire migrate` read every file this version introduces as
	@# a user customisation and freeze it out of future updates.
	$(PYTHON) scripts/gen-kit-hashes.py
	$(MAKE) wheel-check
	@echo "\n\033[32m✓ Built $(VERSION), wheel installs in a fresh venv. Tag and push when ready:\033[0m"
	@echo "  git add version.txt src/grimoire/__version__.py registry/kit-file-hashes.json"
	@echo "  git commit -m 'chore: release $(VERSION)'"
	@echo "  git tag -a v$(VERSION) -m 'Release $(VERSION)'"
	@echo "  git push origin main --tags   # publish.yml prend le relais (PyPI et release GitHub)"

# Pas de voie manuelle vers PyPI : publish.yml est la seule voie de release
# (gates, tests de la wheel, tag sur main, notes du CHANGELOG). Une cible qui
# téléverserait dist/ contournerait ces gardes, et `skip-existing` du workflow
# accepterait ensuite en silence les fichiers déjà envoyés. La cible reste pour
# renvoyer au bon geste plutôt que de laisser un « No rule to make target ».
.PHONY: publish
publish: ## Refusé : la publication passe par le push d'un tag (publish.yml)
	@echo "Publication manuelle refusée : publish.yml est la seule voie de release."
	@echo "Poussez le tag vX.Y.Z sur main (voir CONTRIBUTING.md, section Release)."
	@exit 1

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

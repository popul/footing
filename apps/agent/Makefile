.DEFAULT_GOAL := help

VENV   := .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help
help: ## Liste les cibles
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' Makefile

$(VENV)/bin/activate: pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[test]"
	@touch $(VENV)/bin/activate

.PHONY: install
install: $(VENV)/bin/activate ## Crée le venv et installe en mode éditable

.PHONY: test
test: install ## Tests rapides (skip ceux qui hit LM Studio)
	$(PYTEST) -m "not live"

.PHONY: test-live
test-live: install ## Tests d'intégration (besoin de LM Studio joignable)
	$(PYTEST) -m live

.PHONY: test-all
test-all: install ## Toute la suite
	$(PYTEST)

.PHONY: gen
gen: install ## Génère un plan depuis inputs.json (exemple sur sample-inputs.json)
	$(PY) -m footing_agent sample-inputs.json | tee /tmp/plan-generated.json
	@echo "→ /tmp/plan-generated.json"

.PHONY: validate
validate: install ## Valide un plan existant : make validate PLAN=path/to/plan.json
	@test -n "$(PLAN)" || (echo "PLAN=... requis"; exit 1)
	$(PY) -m footing_agent --validate-only $(PLAN) /dev/null

.PHONY: clean
clean: ## Supprime venv + caches
	rm -rf $(VENV) .pytest_cache footing_agent.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

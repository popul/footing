# apps/web — service Python http.server multitenant.

.DEFAULT_GOAL := help

IMAGE ?= ghcr.io/popul/footing-web
TAG   ?= dev
PORT  ?= 8080
USER  ?= dev

CYAN   := \033[36m
RESET  := \033[0m

.PHONY: help
help: ## Liste les commandes de apps/web
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2 }' Makefile

.PHONY: help-list
help-list:
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "    $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 }' Makefile

.PHONY: deps
deps: .venv/bin/python ## Installe les dépendances dans un venv local
	.venv/bin/pip install --quiet -r requirements.txt

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip

.PHONY: dev
dev: deps ## Lance le serveur sur :$(PORT) (DEFAULT_USER=$(USER))
	@mkdir -p data
	DATA_DIR=./data DEFAULT_USER=$(USER) PORT=$(PORT) .venv/bin/python server.py

.PHONY: check
check: ## Validation : ast.parse du serveur
	@python3 -c "import ast; ast.parse(open('server.py').read())"
	@printf "  $(CYAN)✓$(RESET) server.py: syntaxe OK\n"

.PHONY: fmt
fmt: deps ## Format avec ruff (si dispo)
	@.venv/bin/python -m ruff format server.py 2>/dev/null || echo "ruff non installé, skip"

.PHONY: image
image: ## Build l'image docker (tag=$(TAG))
	docker build -t $(IMAGE):$(TAG) -t footing-web:dev .

.PHONY: image-push
image-push: ## Push l'image (refuse TAG=dev)
	@test "$(TAG)" != "dev" || (echo "TAG=dev refusé, donner un vrai tag"; exit 1)
	docker push $(IMAGE):$(TAG)

.PHONY: shell
shell: image ## Shell interactif dans l'image (data/ monté)
	docker run --rm -it -v $(PWD)/data:/data $(IMAGE):$(TAG) sh

.PHONY: run-image
run-image: image ## Lance l'image en local sur :$(PORT)
	docker run --rm -p $(PORT):8080 -v $(PWD)/data:/data -e DEFAULT_USER=$(USER) $(IMAGE):$(TAG)

.PHONY: smoke
smoke: ## Test rapide des endpoints (suppose le serveur déjà lancé sur :$(PORT))
	@curl -sf -H "X-Authentik-Username: $(USER)" http://localhost:$(PORT)/api/whoami
	@echo ""
	@curl -sf -H "X-Authentik-Username: $(USER)" http://localhost:$(PORT)/api/state
	@echo ""
	@curl -sf http://localhost:$(PORT)/healthz; echo ""
	@printf "  $(CYAN)✓$(RESET) endpoints répondent\n"

.PHONY: clean
clean: ## Supprime .venv, data/, et l'image dev
	rm -rf .venv data
	-docker rmi footing-web:dev 2>/dev/null

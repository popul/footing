# =============================================================
# Makefile racine — agrège les Makefiles par sous-dossier.
# `make help` liste toutes les commandes (root + sub-makefiles).
# Convention : tout target documenté ajoute `## description` à la fin.
# =============================================================

.DEFAULT_GOAL := help

SUBDIRS := apps/web deploy/web

# ANSI escape codes for help formatting.
CYAN   := \033[36m
BOLD   := \033[1m
RESET  := \033[0m

.PHONY: help
help: ## Liste toutes les commandes du monorepo
	@printf "$(BOLD)Footing — commandes disponibles$(RESET)\n\n"
	@printf "$(BOLD)  root:$(RESET)\n"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "    $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 }' Makefile
	@for d in $(SUBDIRS); do \
		printf "\n$(BOLD)  $$d:$(RESET)\n"; \
		$(MAKE) -s -C $$d help-list || true; \
	done

.PHONY: help-list
help-list:
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "    $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 }' Makefile

# ---------- targets racine ----------

.PHONY: dev
dev: ## Lance apps/web en dev local (port 8080, user=dev)
	@$(MAKE) -s -C apps/web dev

.PHONY: image
image: ## Build l'image docker apps/web (locale, tag=dev)
	@$(MAKE) -s -C apps/web image

.PHONY: image-push
image-push: ## Push apps/web sur ghcr.io (TAG=sha-xxx requis)
	@$(MAKE) -s -C apps/web image-push TAG=$(TAG)

.PHONY: kustomize-staging
kustomize-staging: ## Rend l'overlay staging (stdout)
	@$(MAKE) -s -C deploy/web staging

.PHONY: kustomize-mgmt
kustomize-mgmt: ## Rend l'overlay mgmt (stdout)
	@$(MAKE) -s -C deploy/web mgmt

.PHONY: check
check: ## Validation totale : python syntax + kustomize build des overlays
	@$(MAKE) -s -C apps/web check
	@$(MAKE) -s -C deploy/web check
	@printf "$(CYAN)✓$(RESET) repo OK\n"

.PHONY: clean
clean: ## Nettoie les artefacts locaux (.venv, data/, image dev)
	@$(MAKE) -s -C apps/web clean

# ---------- workflows GH ----------

.PHONY: bump-mgmt
bump-mgmt: ## Déclenche le workflow web-cd-mgmt (TAG=sha-xxxxxxxxxxxx requis)
	@test -n "$(TAG)" || (echo "Usage: make bump-mgmt TAG=sha-xxxxxxxxxxxx"; exit 1)
	gh workflow run web-cd-mgmt.yml -f image_tag=$(TAG)
	@echo "Workflow déclenché. Suivre avec: gh run watch"

.PHONY: ci-status
ci-status: ## Affiche le dernier statut CI (gh run list)
	gh run list --workflow=web-ci.yml --limit 5

.PHONY: cd-staging-status
cd-staging-status: ## Affiche le dernier statut CD staging
	gh run list --workflow=web-cd-staging.yml --limit 5

.PHONY: cd-mgmt-status
cd-mgmt-status: ## Affiche le dernier statut CD mgmt
	gh run list --workflow=web-cd-mgmt.yml --limit 5

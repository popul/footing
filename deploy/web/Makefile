# deploy/web — manifests Kustomize (base + overlays staging/mgmt).

.DEFAULT_GOAL := help

CYAN   := \033[36m
RESET  := \033[0m

.PHONY: help
help: ## Liste les commandes de deploy/web
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2 }' Makefile

.PHONY: help-list
help-list:
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "    $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 }' Makefile

.PHONY: base
base: ## Rend la base (kustomize build)
	kustomize build base

.PHONY: staging
staging: ## Rend l'overlay staging
	kustomize build overlays/staging

.PHONY: mgmt
mgmt: ## Rend l'overlay mgmt
	kustomize build overlays/mgmt

.PHONY: check
check: ## Build tous les overlays (sanity check)
	@kustomize build overlays/staging > /dev/null
	@kustomize build overlays/mgmt    > /dev/null
	@printf "  $(CYAN)✓$(RESET) staging + mgmt: kustomize OK\n"

.PHONY: diff-staging
diff-staging: ## Diff overlays/staging contre le cluster courant (kubectl)
	kustomize build overlays/staging | kubectl diff -f - || true

.PHONY: diff-mgmt
diff-mgmt: ## Diff overlays/mgmt contre le cluster courant (kubectl)
	kustomize build overlays/mgmt | kubectl diff -f - || true

.PHONY: image-tag-staging
image-tag-staging: ## Affiche le tag d'image actuellement épinglé sur staging
	@grep -A1 'images:' overlays/staging/kustomization.yaml | tail -1 | awk -F: '{print $$NF}'

.PHONY: image-tag-mgmt
image-tag-mgmt: ## Affiche le tag d'image actuellement épinglé sur mgmt
	@grep -A1 'images:' overlays/mgmt/kustomization.yaml | tail -1 | awk -F: '{print $$NF}'

.PHONY: set-tag-staging
set-tag-staging: ## Bump local du tag staging (TAG=sha-xxx, à committer ensuite)
	@test -n "$(TAG)" || (echo "Usage: make set-tag-staging TAG=sha-xxxxxxxxxxxx"; exit 1)
	cd overlays/staging && kustomize edit set image "ghcr.io/popul/footing-web=ghcr.io/popul/footing-web:$(TAG)"

.PHONY: set-tag-mgmt
set-tag-mgmt: ## Bump local du tag mgmt (TAG=sha-xxx, à committer ensuite)
	@test -n "$(TAG)" || (echo "Usage: make set-tag-mgmt TAG=sha-xxxxxxxxxxxx"; exit 1)
	cd overlays/mgmt && kustomize edit set image "ghcr.io/popul/footing-web=ghcr.io/popul/footing-web:$(TAG)"

# footing — monorepo

Tracker d'entraînement course à pied — multitenant, authentifié via forward-auth (Authentik). Monorepo : tous les services tournant pour le produit `footing` vivent ici, chacun avec son code, ses manifests k8s, et ses workflows CI/CD.

## Layout monorepo

```
apps/
  web/                # service actuel : Python http.server + SPA (tracker)
    Dockerfile
    requirements.txt
    server.py
    static/
      index.html
      livre.html
deploy/
  web/
    base/             # Deployment, Service, PVC, HTTPRoute, Middleware (authentik)
    overlays/
      staging/        # footing.staging.lab.musso.io
      mgmt/           # footing.lab.musso.io
.github/workflows/
  web-ci.yml          # build + push GHCR (déclenché sur changements dans apps/web ou deploy/web)
  web-cd-staging.yml  # bump auto du tag dans deploy/web/overlays/staging (sur succès CI)
  web-cd-mgmt.yml     # bump manuel via workflow_dispatch (deploy/web/overlays/mgmt)
```

Si demain on ajoute `apps/cli/`, `apps/worker/`, etc., on copie le pattern : `deploy/<nom>/` + `.github/workflows/<nom>-*`.

## Architecture runtime (apps/web)

```
Browser
   │ https://footing.lab.musso.io
   ▼
Traefik (mgmt) ─── ForwardAuth middleware ──► Authentik (auth.lab.musso.io)
   │ injecte X-Authentik-Username sur la requête
   ▼
footing-web pod (Python http.server)
   - lit X-Authentik-Username
   - namespace tout sur /data/<user>/...
     ├── state.json    (séances cochées)
     ├── plan.json     (plan d'entraînement actif)
     └── .garth/       (tokens Garmin Connect)
```

## CI / CD

| Cluster | Hostname (LAN) | Source overlay | Image tag bump | ArgoCD |
|---|---|---|---|---|
| **staging** | `footing.staging.lab.musso.io` | `deploy/web/overlays/staging` | **auto** (sur succès CI) | auto-sync |
| **mgmt** | `footing.lab.musso.io` | `deploy/web/overlays/mgmt` | **manuel** (workflow_dispatch) | auto-sync |

Le seul gate manuel pour mgmt est le déclenchement du workflow `web-cd-mgmt` avec le tag d'image souhaité.

## Dev local

Tous les usages courants passent par `make`. Hiérarchie : un Makefile racine agrège les sous-Makefiles `apps/web` et `deploy/web`. `make help` à la racine ou dans un sous-dossier liste les commandes auto-documentées.

```bash
make help              # liste toutes les cibles (root + apps/web + deploy/web)
make dev               # lance apps/web sur :8080 avec DEFAULT_USER=dev
make image             # build l'image docker locale
make check             # python syntax + kustomize build des deux overlays
make kustomize-staging # rend l'overlay staging
make bump-mgmt TAG=... # déclenche le workflow web-cd-mgmt
```

Simuler le forward-auth via curl :
```bash
curl -H "X-Authentik-Username: alice" http://localhost:8080/api/state
```

# footing-agent

Agent standalone générant des plans d'entraînement pour [footing](../web/),
en s'appuyant sur un LLM OpenAI-compatible (par défaut LM Studio local).

Extrait du serveur web (`apps/web/server.py`) pour être :
- utilisable en CLI (`python -m footing_agent inputs.json`)
- testable indépendamment (`make test` — 26 tests passent en < 1s sans
  tâcher LM Studio)
- réutilisable comme librairie (`from footing_agent import generate_plan`)

## Quickstart

```bash
cd apps/agent
make install         # crée .venv, installe en mode éditable
make test            # property tests + LLM mocked
make test-live       # appel réel à LM Studio (skip si injoignable)
make gen             # génère un plan depuis sample-inputs.json
```

## Architecture

```
footing_agent/
├── schema.py         # PLAN_SCHEMA (json-schema partagé avec apps/web/)
├── prompts.py        # build_messages(inputs, n_weeks) + parse_hms
├── llm.py            # llm_complete() — wrapper OpenAI chat-completion
├── validation.py     # check_properties(plan) → list[PropertyViolation]
├── agent.py          # generate_plan(inputs) — orchestration + retry
└── __main__.py       # CLI
```

`generate_plan` est l'entry point :

```python
from footing_agent import generate_plan

plan = generate_plan({
    "distance": "10K",
    "raceDate": "2026-12-13",
    "targetTime": "50:00",
    "currentDistance": "10K",
    "currentTime": "55:00",
    "startDate": "2026-10-05",
    "weeklyKm": 25,
    "daysPerWeek": 3,
    "longRunDay": "Dimanche",
})
```

Si le plan LLM viole une propriété, l'agent re-prompte une fois en
injectant les violations comme feedback. Au-delà, lève `PlanValidationError`.

## Config

Vars d'env (override default LM Studio sur macbookprom5) :
- `LLM_BASE_URL` (défaut `http://macbookprom5.home:1234/v1`)
- `LLM_MODEL` (défaut `qwen/qwen3.6-35b-a3b`)
- `LLM_TIMEOUT` (défaut 300s)

## Tests

Le checker `validation.check_properties` est étalonné sur 2 plans
existants (`tests/fixtures/{paul,audrey}.json`) :

- **paul.json** : 23 semaines, 10K objectif 45:00 (plan importé depuis
  le fallback hardcoded historique)
- **audrey.json** : 20 semaines, 10K objectif 60:00 (plan généré par
  LM Studio via `/api/plan/generate`)

Les 2 doivent passer toutes les checks. Si un test golden fail après
modification du checker → décision à prendre :
- soit assouplir le checker (la contrainte était trop stricte)
- soit mettre à jour la fixture (la nouvelle contrainte est volontaire)

Le contre-test (`test_synthetic_*`) construit des plans volontairement
défectueux et vérifie que chaque check attrape la violation correspondante
— garde-fou contre un checker qui ne checke rien.

```bash
make test       # 26 tests, ~0.5s
make test-live  # +1 test qui hit LM Studio
make test-all   # toute la suite
```

## CLI

```bash
# génère un plan
python -m footing_agent sample-inputs.json > plan.json

# valide un plan existant (utilise les inputs JSON pour les checks
# qui en dépendent — passe /dev/null si pas d'inputs)
python -m footing_agent --validate-only plan.json sample-inputs.json
```

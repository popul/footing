"""footing_agent : générateur de plans d'entraînement standalone.

Extrait du serveur web (apps/web/server.py) pour être :
  - utilisable en CLI (`python -m footing_agent inputs.json`)
  - testable indépendamment (cf. tests/)
  - portable sur n'importe quel backend OpenAI-compatible (par défaut
    LM Studio en local sur macbookprom5:1234).

Le module web continue d'inliner sa propre copie pour éviter d'ajouter
une dépendance Python au pod footing-web — quand l'agent stabilise,
on pourra fusionner et faire pointer le serveur dessus.
"""

from .agent import generate_plan
from .schema import PLAN_SCHEMA
from .validation import check_properties, PropertyViolation

__all__ = ["generate_plan", "PLAN_SCHEMA", "check_properties", "PropertyViolation"]

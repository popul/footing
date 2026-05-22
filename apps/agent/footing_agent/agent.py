"""Orchestration : construit le prompt, appelle le LLM, valide, retourne le plan.

Si la validation détecte une violation, on RE-prompt le LLM une fois en
lui passant la liste des erreurs — sinon on lève PlanValidationError.
"""

from typing import Any

from .llm import llm_complete
from .prompts import build_messages, weeks_between
from .schema import DISTANCE_MIN_WEEKS, PLAN_SCHEMA
from .validation import check_properties


class PlanValidationError(RuntimeError):
    def __init__(self, violations):
        super().__init__("; ".join(violations))
        self.violations = violations


def _expected_n_weeks(inputs: dict) -> int:
    """Nombre de semaines = jours entre startDate et raceDate (cap minimum
    par DISTANCE_MIN_WEEKS pour ne pas générer un plan ridicule)."""
    natural = weeks_between(inputs["startDate"], inputs["raceDate"])
    floor = DISTANCE_MIN_WEEKS.get(inputs["distance"], 8)
    return max(natural, floor)


def generate_plan(
    inputs: dict,
    *,
    retry_on_validation: bool = True,
    llm_kwargs: dict | None = None,
) -> dict:
    """inputs = {distance, raceDate, targetTime, currentDistance, currentTime,
    startDate, weeklyKm?, daysPerWeek?, longRunDay?, gear?}.

    Retourne le plan (dict conforme à PLAN_SCHEMA). Lève PlanValidationError
    si le LLM produit du contenu qui viole les propriétés (après 1 retry).
    """
    n_weeks = _expected_n_weeks(inputs)
    messages = build_messages(inputs, n_weeks)
    llm_kwargs = llm_kwargs or {}

    plan = llm_complete(messages, PLAN_SCHEMA, **llm_kwargs)
    violations = check_properties(plan, inputs=inputs)

    if violations and retry_on_validation:
        # Une seule re-tentative en injectant les violations comme feedback.
        # Au-delà, on remonte au caller : soit le prompt est mauvais, soit
        # le modèle ne sait pas faire mieux et c'est à lui d'arbitrer.
        feedback = (
            "Le plan généré précédemment viole ces propriétés :\n"
            + "\n".join(f"- {v}" for v in violations)
            + "\n\nRegénère un plan complet qui respecte TOUTES les contraintes."
        )
        messages_retry = messages + [
            {"role": "assistant", "content": "(plan précédent invalide)"},
            {"role": "user", "content": feedback},
        ]
        plan = llm_complete(messages_retry, PLAN_SCHEMA, **llm_kwargs)
        violations = check_properties(plan, inputs=inputs)

    if violations:
        raise PlanValidationError(violations)

    return plan

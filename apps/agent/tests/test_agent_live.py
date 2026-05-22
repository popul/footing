"""Tests d'intégration : appellent VRAIMENT LM Studio.

Marqués `@pytest.mark.live` pour pouvoir filtrer (`pytest -m "not live"`
en CI sans LM Studio, `pytest -m live` en local pour valider le bout en
bout). Skip auto si LM Studio injoignable.
"""

from __future__ import annotations

import pytest
import requests

from footing_agent.agent import generate_plan
from footing_agent.llm import DEFAULT_BASE_URL
from footing_agent.validation import check_properties


pytestmark = pytest.mark.live


def _lmstudio_alive() -> bool:
    try:
        r = requests.get(f"{DEFAULT_BASE_URL}/models", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def skip_without_lmstudio():
    if not _lmstudio_alive():
        pytest.skip(f"LM Studio non joignable à {DEFAULT_BASE_URL}")


def test_generate_short_10k_plan():
    """Sanity check : un plan court (10 sem, 10K, 50:00) est généré et
    passe les checks de propriété."""
    inputs = {
        "distance": "10K",
        "raceDate": "2026-12-13",
        "targetTime": "50:00",
        "currentDistance": "10K",
        "currentTime": "55:00",
        "startDate": "2026-10-05",
        "weeklyKm": 25,
        "daysPerWeek": 3,
        "longRunDay": "Dimanche",
    }
    plan = generate_plan(inputs)
    assert plan["raceDistance"] == "10K"
    assert plan["targetTime"] == "50:00"
    assert len(plan["weeks"]) >= 8  # DISTANCE_MIN_WEEKS[10K]
    violations = check_properties(plan, inputs=inputs)
    assert violations == [], violations

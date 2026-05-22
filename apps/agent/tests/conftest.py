"""Fixtures partagées : charge paul.json et audrey.json depuis tests/fixtures/.

Ces 2 plans sont les "golden" servant d'étalonnage des checks de propriété :
chacun doit passer toutes les checks à `inputs=None` (les plans persistés
côté serveur n'ont pas de bloc `_inputs`).

Toute évolution du checker qui ferait échouer ces fixtures est SOIT un bug
du checker, SOIT un signe qu'on serre les contraintes au-delà de ce que
les plans existants respectent (auquel cas mettre à jour les fixtures
volontairement plutôt que d'assouplir le checker).
"""

from __future__ import annotations

import json
import pathlib

import pytest


FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def paul_plan() -> dict:
    return _load("paul.json")


@pytest.fixture
def audrey_plan() -> dict:
    return _load("audrey.json")


@pytest.fixture(params=["paul.json", "audrey.json"])
def golden_plan(request) -> dict:
    """Paramétrise un test sur les 2 plans : le test tourne 2 fois,
    une par fixture, et doit passer sur les deux."""
    return _load(request.param)

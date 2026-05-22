"""Property tests + étalonnage sur les golden plans paul + audrey.

Deux groupes :

1. ``test_golden_*`` : appliquent le checker aux fixtures et assertent
   l'absence de violation. Si un de ces tests échoue, c'est qu'on a
   serré le checker au-delà des plans existants → décision à prendre
   (assouplir le checker OU mettre à jour les fixtures).

2. ``test_synthetic_*`` : construisent des plans volontairement
   défectueux et vérifient que le checker DÉTECTE la violation attendue.
   Garde-fou contre les régressions silencieuses (= un checker qui ne
   rien checke garderait les golden verts mais passerait à côté des bugs).
"""

from __future__ import annotations

import copy

import pytest

from footing_agent.validation import (
    PropertyViolation,
    check_properties,
    _check_garmin_presence,
    _check_garmin_steps_shape,
    _check_light_weeks_distribution,
    _check_min_weeks,
    _check_phase_progression,
    _check_race_last_week,
    _check_sessions_days,
    _check_top_level,
    _check_weeks_numbering,
)


# ============================================================
# Étalonnage : les 2 plans existants passent toutes les checks
# ============================================================
class TestGolden:
    def test_check_properties_returns_empty(self, golden_plan):
        violations = check_properties(golden_plan)
        assert violations == [], (
            "Le plan golden devrait être conforme. Violations :\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_each_check_individually(self, golden_plan):
        """Échoue le check qui pose pb avec un message clair, sans masquer
        les autres dans l'agrégation. Utile en debug si on durcit le checker."""
        for fn in (
            _check_top_level,
            _check_weeks_numbering,
            _check_race_last_week,
            _check_sessions_days,
            _check_min_weeks,
            _check_garmin_presence,
            _check_garmin_steps_shape,
            _check_phase_progression,
            _check_light_weeks_distribution,
        ):
            errs = fn(golden_plan)
            assert errs == [], f"{fn.__name__}: {errs}"

    def test_with_inputs_target_pace_present(self, paul_plan):
        """Paul = 45:00 sur 10K → target_pace 270s/km. Vérifie qu'au moins
        un step est à cette allure (±15s)."""
        inputs = {
            "distance": "10K",
            "targetTime": "45:00",
        }
        violations = check_properties(paul_plan, inputs=inputs)
        assert violations == [], violations

    def test_audrey_with_inputs(self, audrey_plan):
        """Audrey = 60:00 sur 10K → target_pace 360s/km (6:00/km)."""
        inputs = {
            "distance": "10K",
            "targetTime": "60:00",
        }
        violations = check_properties(audrey_plan, inputs=inputs)
        assert violations == [], violations


# ============================================================
# Synthétiques : on casse un plan valide et le checker doit attraper
# ============================================================
class TestStructure:
    def test_top_level_missing_raceDate(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        del bad["raceDate"]
        errs = _check_top_level(bad)
        assert any("raceDate" in e for e in errs)

    def test_weeks_numbering_skip(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"][3]["num"] = 99
        errs = _check_weeks_numbering(bad)
        assert any("num=99" in e for e in errs)

    def test_race_not_last(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"][-1]["race"] = False
        errs = _check_race_last_week(bad)
        assert errs and "race=true" in errs[0]

    def test_duplicate_day(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # force 2 sessions le même jour
        bad["weeks"][0]["sessions"][1]["day"] = bad["weeks"][0]["sessions"][0]["day"]
        errs = _check_sessions_days(bad)
        assert any("dupliqué" in e for e in errs)

    def test_invalid_day(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"][0]["sessions"][0]["day"] = "Pasundimanche"
        errs = _check_sessions_days(bad)
        assert any("invalide" in e for e in errs)

    def test_too_few_weeks(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"] = bad["weeks"][:3]  # 10K min = 8
        errs = _check_min_weeks(bad)
        assert errs and "trop court" in errs[0]


class TestGarmin:
    def test_garmin_absent_on_regular_session(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # premier session du plan, ce n'est pas la course → garmin requis
        bad["weeks"][0]["sessions"][0].pop("garmin", None)
        errs = _check_garmin_presence(bad)
        assert any("garmin absent" in e for e in errs)

    def test_garmin_present_on_race(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # ajouter un garmin sur la séance de course (dernière semaine, dimanche)
        race = next(s for s in bad["weeks"][-1]["sessions"] if s["day"] == "Dimanche")
        race["garmin"] = {"name": "ne devrait pas être là", "steps": [{"type": "other", "dur": {"time": 60}}]}
        errs = _check_garmin_presence(bad)
        assert any("course" in e and "ne doit pas avoir" in e for e in errs)

    def test_garmin_step_pace_inverted(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # trouver un step avec pace et inverser slow/fast
        for w in bad["weeks"]:
            for s in w["sessions"]:
                g = s.get("garmin")
                if not g:
                    continue
                for st in g["steps"]:
                    if "pace" in st and st["type"] != "repeat":
                        slow, fast = st["pace"]
                        st["pace"] = [fast, slow]  # inversé
                        errs = _check_garmin_steps_shape(bad)
                        assert any("inversé" in e for e in errs)
                        return
        pytest.skip("aucun step avec pace dans la fixture (improbable)")

    def test_garmin_step_unknown_type(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        for w in bad["weeks"]:
            for s in w["sessions"]:
                g = s.get("garmin")
                if not g:
                    continue
                g["steps"][0]["type"] = "boum"
                errs = _check_garmin_steps_shape(bad)
                assert any("type 'boum'" in e for e in errs)
                return
        pytest.skip("pas de garmin step à corrompre")


class TestProgression:
    def test_phase_reappears(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # Forcer la dernière semaine à reprendre la phase de la première
        bad["weeks"][-1]["phase"] = bad["weeks"][0]["phase"]
        errs = _check_phase_progression(bad)
        assert any("réapparaît" in e for e in errs)

    def test_light_weeks_consecutive(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        # Forcer 2 semaines light consécutives au milieu du plan
        mid = len(bad["weeks"]) // 2
        bad["weeks"][mid]["light"] = True
        bad["weeks"][mid + 1]["light"] = True
        errs = _check_light_weeks_distribution(bad)
        assert any("consécutives" in e for e in errs)

    def test_light_on_race_week(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"][-1]["light"] = True
        errs = _check_light_weeks_distribution(bad)
        assert any("course" in e and "ne doit pas être allégée" in e for e in errs)

    def test_first_week_light(self, paul_plan):
        bad = copy.deepcopy(paul_plan)
        bad["weeks"][0]["light"] = True
        errs = _check_light_weeks_distribution(bad)
        assert any("semaine 1" in e for e in errs)


class TestPaceCoherence:
    def test_no_target_pace_step(self, audrey_plan):
        """Si on garde Audrey (target 60:00 → 360s/km) mais qu'on prétend
        viser 45:00 (270s/km), aucun step ne match → violation."""
        violations = check_properties(audrey_plan, inputs={"distance": "10K", "targetTime": "45:00"})
        assert any("allure cible" in v for v in violations)


# ============================================================
# Marker type
# ============================================================
def test_violations_are_strings(paul_plan):
    """Les PropertyViolation doivent rester sérialisables comme str
    (pour permettre de les envoyer au LLM en feedback retry, par ex)."""
    bad = copy.deepcopy(paul_plan)
    bad["weeks"][-1]["race"] = False
    errs = check_properties(bad)
    for e in errs:
        assert isinstance(e, str)
        assert isinstance(e, PropertyViolation)

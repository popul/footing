"""Property-based validation d'un plan d'entraînement.

Chaque fonction `_check_xxx` retourne une liste de strings (violations).
`check_properties(plan, inputs=None)` les enchaîne et renvoie la liste
agrégée. Si `inputs` est None, on saute les checks qui en dépendent
(typiquement la cohérence des allures vs targetTime).

Calibration : les fixtures `tests/fixtures/{paul,audrey}.json` doivent
passer toutes les checks à `inputs=None` (elles n'ont pas d'`_inputs`
persistés). Les TUs `tests/test_validation.py` en font la garantie —
toute évolution du checker doit garder ces 2 plans verts.
"""

from .prompts import parse_hms
from .schema import DISTANCE_M, DISTANCE_MIN_WEEKS, PLAN_DAYS


class PropertyViolation(str):
    """Marker type (sous-classe de str) pour différencier visuellement
    une violation d'un message libre. Reste un str pour rester sérialisable."""


def _v(msg: str) -> PropertyViolation:
    return PropertyViolation(msg)


# ============================================================
# Structure
# ============================================================
def _check_top_level(plan: dict) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    for k in ("raceDate", "raceDistance", "targetTime", "weeks"):
        if k not in plan:
            errs.append(_v(f"top-level: champ requis manquant '{k}'"))
    if plan.get("raceDistance") and plan["raceDistance"] not in DISTANCE_M:
        errs.append(_v(f"raceDistance='{plan['raceDistance']}' non reconnu"))
    if "weeks" in plan and not isinstance(plan["weeks"], list):
        errs.append(_v("weeks doit être une liste"))
    return errs


def _check_weeks_numbering(plan: dict) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    weeks = plan.get("weeks") or []
    for i, w in enumerate(weeks, start=1):
        if w.get("num") != i:
            errs.append(_v(f"semaine index {i}: num={w.get('num')} attendu {i}"))
    return errs


def _check_race_last_week(plan: dict) -> list[PropertyViolation]:
    weeks = plan.get("weeks") or []
    if not weeks:
        return []
    last = weeks[-1]
    if not last.get("race"):
        return [_v(f"dernière semaine (num={last.get('num')}) doit avoir race=true")]
    return []


def _check_sessions_days(plan: dict) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    for w in plan.get("weeks") or []:
        seen: set[str] = set()
        for s in w.get("sessions") or []:
            day = s.get("day")
            if day not in PLAN_DAYS:
                errs.append(_v(f"semaine {w.get('num')}: jour invalide '{day}'"))
            elif day in seen:
                errs.append(_v(f"semaine {w.get('num')}: jour '{day}' dupliqué"))
            seen.add(day)
    return errs


def _check_min_weeks(plan: dict) -> list[PropertyViolation]:
    weeks = plan.get("weeks") or []
    dist = plan.get("raceDistance")
    if not dist:
        return []
    min_n = DISTANCE_MIN_WEEKS.get(dist)
    if min_n is None:
        return []
    if len(weeks) < min_n:
        return [_v(f"plan trop court : {len(weeks)} semaines, minimum {min_n} pour {dist}")]
    return []


# ============================================================
# Garmin steps (les seules sessions sans garmin sont les race=true)
# ============================================================
_STEP_TYPES = {"warmup", "cooldown", "interval", "recovery", "other"}


def _check_garmin_presence(plan: dict) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    for w in plan.get("weeks") or []:
        is_race_week = bool(w.get("race"))
        for s in w.get("sessions") or []:
            has_garmin = "garmin" in s
            # Sur la semaine de course, seule la séance du dimanche peut
            # être sans garmin (= la course). Les autres séances doivent
            # avoir un garmin (rappel d'allure, déverrouillage…).
            is_race_session = is_race_week and s.get("day") == "Dimanche"
            if is_race_session and has_garmin:
                errs.append(_v(
                    f"semaine {w.get('num')} séance course ({s.get('day')}): "
                    "ne doit pas avoir de bloc garmin"
                ))
            if not is_race_session and not has_garmin:
                errs.append(_v(
                    f"semaine {w.get('num')} séance {s.get('day')} '{s.get('title')}': "
                    "garmin absent"
                ))
    return errs


def _check_garmin_step(step: dict, ctx: str) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    t = step.get("type")
    if t == "repeat":
        if not isinstance(step.get("count"), int) or step["count"] < 2:
            errs.append(_v(f"{ctx}: repeat.count invalide ({step.get('count')})"))
        sub = step.get("steps") or []
        if not sub:
            errs.append(_v(f"{ctx}: repeat sans sous-steps"))
        for j, ss in enumerate(sub):
            errs += _check_garmin_step(ss, f"{ctx}.steps[{j}]")
            if ss.get("type") == "repeat":
                errs.append(_v(f"{ctx}.steps[{j}]: repeat imbriqué non supporté"))
        return errs

    if t not in _STEP_TYPES:
        errs.append(_v(f"{ctx}: type '{t}' inconnu"))
        return errs

    dur = step.get("dur") or {}
    if not isinstance(dur, dict) or not (
        "time" in dur or "dist" in dur or dur.get("lap")
    ):
        errs.append(_v(f"{ctx}: dur doit avoir time/dist/lap, reçu {dur}"))
    if "time" in dur and (not isinstance(dur["time"], int) or dur["time"] <= 0):
        errs.append(_v(f"{ctx}: dur.time invalide ({dur['time']})"))
    if "dist" in dur and (not isinstance(dur["dist"], int) or dur["dist"] <= 0):
        errs.append(_v(f"{ctx}: dur.dist invalide ({dur['dist']})"))

    pace = step.get("pace")
    if pace is not None:
        if not (isinstance(pace, list) and len(pace) == 2 and all(isinstance(p, int) for p in pace)):
            errs.append(_v(f"{ctx}: pace doit être [slow, fast] entiers, reçu {pace}"))
        else:
            slow, fast = pace
            # Convention : pace[0] = plus lent (s/km plus grand), pace[1] = plus rapide.
            if slow < fast:
                errs.append(_v(
                    f"{ctx}: pace [{slow}, {fast}] inversé (slow doit être >= fast en s/km)"
                ))
            # Bornes sanity : 2:00/km (sprint) à 9:30/km (marche).
            for p in pace:
                if p < 120 or p > 570:
                    errs.append(_v(f"{ctx}: pace {p}s/km hors plage 120-570"))
    return errs


def _check_garmin_steps_shape(plan: dict) -> list[PropertyViolation]:
    errs: list[PropertyViolation] = []
    for w in plan.get("weeks") or []:
        for s in w.get("sessions") or []:
            g = s.get("garmin")
            if not g:
                continue
            if not g.get("name"):
                errs.append(_v(
                    f"semaine {w.get('num')} {s.get('day')}: garmin.name vide"
                ))
            steps = g.get("steps") or []
            if not steps:
                errs.append(_v(
                    f"semaine {w.get('num')} {s.get('day')}: garmin.steps vide"
                ))
            for i, step in enumerate(steps):
                errs += _check_garmin_step(
                    step,
                    f"semaine {w.get('num')} {s.get('day')} step[{i}]",
                )
    return errs


# ============================================================
# Progressivité
# ============================================================
def _check_phase_progression(plan: dict) -> list[PropertyViolation]:
    """L'affûtage / la phase contenant la course doit être la dernière
    distincte. Une phase ne réapparaît pas après en avoir changé."""
    errs: list[PropertyViolation] = []
    weeks = plan.get("weeks") or []
    seen_order: list[str] = []
    for w in weeks:
        ph = w.get("phase") or ""
        if not ph:
            errs.append(_v(f"semaine {w.get('num')}: phase vide"))
            continue
        if not seen_order or seen_order[-1] != ph:
            if ph in seen_order:
                errs.append(_v(
                    f"semaine {w.get('num')}: phase '{ph}' réapparaît "
                    f"après être passée à {seen_order[-1]}"
                ))
            seen_order.append(ph)
    return errs


def _check_light_weeks_distribution(plan: dict) -> list[PropertyViolation]:
    """~25% de semaines allégées, jamais 2 consécutives, jamais en
    première ni dernière (race) semaine."""
    errs: list[PropertyViolation] = []
    weeks = plan.get("weeks") or []
    if not weeks:
        return errs
    light_idx = [i for i, w in enumerate(weeks) if w.get("light")]
    n = len(weeks)
    ratio = len(light_idx) / n if n else 0
    # Tolérance 10-35% (le prompt vise ~25%, on laisse de la marge).
    if not (0.10 <= ratio <= 0.35):
        errs.append(_v(
            f"semaines allégées : {len(light_idx)}/{n} ({ratio:.0%}) hors cible 10-35%"
        ))
    for a, b in zip(light_idx, light_idx[1:]):
        if b - a == 1:
            errs.append(_v(
                f"semaines allégées consécutives : {weeks[a]['num']} et {weeks[b]['num']}"
            ))
    if 0 in light_idx:
        errs.append(_v("semaine 1 ne doit pas être allégée"))
    if (n - 1) in light_idx:
        errs.append(_v(f"semaine de course (num={weeks[-1]['num']}) ne doit pas être allégée"))
    return errs


# ============================================================
# Cohérence avec les inputs (optionnel : skip si inputs=None)
# ============================================================
def _check_target_pace_in_steps(plan: dict, inputs: dict) -> list[PropertyViolation]:
    """Au moins une session contient un step à allure proche de la cible
    course — sinon le plan n'entraîne pas spécifiquement à l'allure visée.
    Tolérance ±15 s/km vs target_pace."""
    errs: list[PropertyViolation] = []
    try:
        target_sec = parse_hms(inputs["targetTime"])
        dist_m = DISTANCE_M[inputs["distance"]]
    except (KeyError, ValueError):
        return errs
    target_pace = target_sec / (dist_m / 1000.0)

    def _walk(step: dict, hits: list[int]):
        if step.get("type") == "repeat":
            for ss in step.get("steps") or []:
                _walk(ss, hits)
            return
        pace = step.get("pace")
        if pace and len(pace) == 2:
            mid = (pace[0] + pace[1]) / 2
            if abs(mid - target_pace) <= 15:
                hits.append(int(mid))

    hits: list[int] = []
    for w in plan.get("weeks") or []:
        for s in w.get("sessions") or []:
            for st in (s.get("garmin") or {}).get("steps") or []:
                _walk(st, hits)
    if not hits:
        errs.append(_v(
            f"aucun step à l'allure cible course ({target_pace:.0f}s/km ±15) trouvé"
        ))
    return errs


# ============================================================
# Entry point
# ============================================================
_CHECKS_NO_INPUTS = (
    _check_top_level,
    _check_weeks_numbering,
    _check_race_last_week,
    _check_sessions_days,
    _check_min_weeks,
    _check_garmin_presence,
    _check_garmin_steps_shape,
    _check_phase_progression,
    _check_light_weeks_distribution,
)


def check_properties(plan: dict, inputs: dict | None = None) -> list[PropertyViolation]:
    """Retourne la liste agrégée des violations. Vide = plan conforme."""
    errs: list[PropertyViolation] = []
    for fn in _CHECKS_NO_INPUTS:
        errs += fn(plan)
    if inputs:
        errs += _check_target_pace_in_steps(plan, inputs)
    return errs

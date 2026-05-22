"""Construction des messages OpenAI-compatible pour le LLM.

Le prompt suit les principes du "Petit Traité du Coureur"
(apps/web/static/livre.html) : règle 70-80% endurance, pas d'enchaînement
de séances dures, respecter les semaines allégées, etc.
"""

from datetime import date as _date

from .schema import DISTANCE_M


def parse_hms(s: str) -> int:
    """Parse "45:00", "1h30", "1:30:00", "90m"... → secondes."""
    raw = (s or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("temps vide")

    if "h" in raw:
        h, _, rest = raw.partition("h")
        rest = rest.replace("m", ":").replace("s", "")
        rest = rest.strip(":")
        h_int = int(h) if h else 0
        if not rest:
            return h_int * 3600
        parts = rest.split(":")
        m_int = int(parts[0]) if parts[0] else 0
        s_int = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        return h_int * 3600 + m_int * 60 + s_int

    if "m" in raw:
        m, _, rest = raw.partition("m")
        rest = rest.replace("s", "")
        return int(m) * 60 + (int(rest) if rest else 0)

    parts = raw.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    raise ValueError(f"temps invalide: {s}")


def weeks_between(start_iso: str, end_iso: str) -> int:
    a = _date.fromisoformat(start_iso)
    b = _date.fromisoformat(end_iso)
    days = (b - a).days
    return 0 if days < 0 else days // 7 + 1


def build_messages(inputs: dict, n_weeks: int) -> list[dict]:
    distance = inputs["distance"]
    distance_m = DISTANCE_M[distance]
    target_sec = parse_hms(inputs["targetTime"])
    target_pace = target_sec / (distance_m / 1000.0)
    current_dist_m = DISTANCE_M[inputs["currentDistance"]]
    current_sec = parse_hms(inputs["currentTime"])
    current_pace = current_sec / (current_dist_m / 1000.0)
    weekly_km = inputs.get("weeklyKm") or 25
    days_per_week = inputs.get("daysPerWeek") or 3
    long_run_day = inputs.get("longRunDay") or "Dimanche"
    start_date = inputs["startDate"]
    race_date = inputs["raceDate"]
    gear = (inputs.get("gear") or "").strip()

    all_days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    midweek_pool = [d for d in all_days if d != long_run_day]
    valid_days = midweek_pool + [long_run_day]

    system = (
        "Tu es un coach de course à pied expérimenté. Tu produis des plans "
        "d'entraînement structurés au format JSON, en français, exploitables "
        "directement par une montre Garmin. Ton output doit être STRICTEMENT le "
        "JSON demandé, sans texte autour. /no_think"
    )

    user = f"""Génère un plan d'entraînement pour atteindre un objectif de course.

PARAMÈTRES UTILISATEUR
- Distance course      : {distance} ({distance_m} m)
- Date de la course    : {race_date} (dimanche)
- Temps cible          : {inputs['targetTime']} (allure cible {target_pace:.0f} s/km)
- Niveau actuel        : {inputs['currentTime']} sur {inputs['currentDistance']} (allure actuelle {current_pace:.0f} s/km)
- Volume hebdomadaire  : {weekly_km} km
- Date de début        : {start_date} (lundi)
- Jours d'entraînement : {days_per_week} par semaine
- Sortie longue        : {long_run_day}
- Nombre de semaines   : {n_weeks}
- Matériel (montre)    : {gear or '—'}

CONTRAINTES STRUCTURE PLAN
1. Le plan compte EXACTEMENT {n_weeks} semaines numérotées de 1 à {n_weeks}.
2. La dernière semaine (num={n_weeks}) a `race: true` et sa séance du dimanche est la course (pas de bloc `garmin`).
3. Chaque semaine a {days_per_week} séances sur des jours distincts dans : {', '.join(valid_days)}.
4. Mark `light: true` les semaines de récupération (~25% du plan, espacées).
5. Phases (champ `phase`) : "Base", "Seuil", "Spécifique", "Affûtage". Distribue progressivement.
6. Volume `volume` au format "30–35 km", progression max +10% par semaine, baisse en semaines `light` et en affûtage.
7. Chaque séance non-course a un bloc `garmin: {{name, steps}}` exécutable. Steps possibles :
   - {{type:"warmup"|"cooldown", dur:{{time: secondes}}, pace: [slow_s_per_km, fast_s_per_km]}}
   - {{type:"interval"|"other", dur:{{time:S}} ou {{dist:M}}, pace:[slow,fast]}}
   - {{type:"recovery", dur:{{time:S}} ou {{lap:true}}, pace:[slow,fast]}}
   - {{type:"repeat", count:N, steps:[interval, recovery]}}
8. Allures de référence pour les `garmin.steps` (s/km) :
   - Endurance fondamentale : [390, 350]
   - Trot récupération      : [420, 390]
   - Allure cible course    : [{int(target_pace) + 5}, {int(target_pace) - 5}]
   Adapte les autres allures au niveau de l'athlète.
9. `items` reflète `garmin.steps` en français lisible (ex: {{do:"20 min échauffement", pace:"5:50–6:30/km"}}).
10. Range les écarts d'allures dans des ranges courts (5–15s).

PRINCIPES PÉDAGOGIQUES (issus du "Petit Traité du Coureur")
11. Volume hebdomadaire respectant la règle 70-80% / 20-30% :
    - 70 à 80% du volume total en endurance fondamentale (allure facile,
      conversation possible). C'est la BASE.
    - 20 à 30% en qualité (seuil, fractionné, allure spécifique).
12. "Les amateurs courent leurs séances faciles TROP VITE et leurs séances
    rapides TROP LENTEMENT." Footings vraiment lents (pace [390, 350]),
    séances rapides franchement à l'allure cible ou plus rapide.
13. Repos = entraînement. Entre deux séances dures (interval, seuil,
    spécifique, sortie longue), prévoir au moins une journée facile.

Réponds par le JSON conforme au schéma. Aucun commentaire."""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

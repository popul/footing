#!/usr/bin/env python3
"""footing — serveur multitenant.

Le tenant est l'identité injectée par le forward-auth en amont (Traefik +
Authentik). On lit `X-Authentik-Email` (clé canonique du compte, stable
quand l'utilisateur renomme son username) et on namespace tout l'état
disque sous /data/<user_id>/. Les sessions en mémoire (login Garmin, jobs
de génération de plan) sont également scopées par user_id.
"""
import http.server
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date as _date
from socketserver import ThreadingMixIn

STATIC_DIR = os.environ.get('STATIC_DIR', '/app/static')
DATA_DIR = os.environ.get('DATA_DIR', '/data')
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://macbookprom5.home:1234/v1')
LLM_MODEL = os.environ.get('LLM_MODEL', 'qwen/qwen3.6-35b-a3b')
LLM_TIMEOUT = float(os.environ.get('LLM_TIMEOUT', '300'))
PORT = int(os.environ.get('PORT', '8080'))
# Fallback user when USER_HEADER absent (dev local, smoke tests).
DEFAULT_USER = os.environ.get('DEFAULT_USER', 'default')
USER_HEADER = os.environ.get('USER_HEADER', 'X-Authentik-Email')
MAX_BODY = 1_000_000

# A user_id is normalised to [a-zA-Z0-9_-]{1,64}. Anything else is rejected
# so a header value can't escape its directory (../ etc.).
_USER_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

# Per-user locks indexed by user_id; created on first use.
_write_locks = {}
_garmin_locks = {}
_plan_locks = {}
_user_locks_lock = threading.Lock()
login_lock = threading.Lock()
plan_jobs_lock = threading.Lock()


def _sanitize_user(name):
    name = (name or '').strip()
    if not name:
        return None
    # Allow common separators in display names by mapping them.
    name = name.replace('@', '_').replace('.', '_').replace(' ', '_')
    if not _USER_RE.match(name):
        return None
    return name


def _get_lock(table, user_id):
    with _user_locks_lock:
        lk = table.get(user_id)
        if lk is None:
            lk = threading.Lock()
            table[user_id] = lk
        return lk


def _user_dir(user_id):
    return os.path.join(DATA_DIR, user_id)


def _state_file(user_id):
    return os.path.join(_user_dir(user_id), 'state.json')


def _plan_file(user_id):
    return os.path.join(_user_dir(user_id), 'plan.json')


def _garth_dir(user_id):
    return os.path.join(_user_dir(user_id), '.garth')


# ============================================================
# Garmin Connect — tokens loaded lazily, push workout + schedule
# ============================================================
SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running"}
STEP_TYPE_MAP = {
    "warmup":   (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "recovery": (4, "recovery"),
    "rest":     (5, "rest"),
    "repeat":   (6, "repeat"),
    "other":    (7, "other"),
}


def _step_type(name):
    sid, skey = STEP_TYPE_MAP[name]
    return {"stepTypeId": sid, "stepTypeKey": skey}


def _end_condition(dur):
    if 'time' in dur:
        return {"conditionTypeId": 2, "conditionTypeKey": "time"}, float(dur['time'])
    if 'dist' in dur:
        return {"conditionTypeId": 3, "conditionTypeKey": "distance"}, float(dur['dist'])
    return {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}, None


def _pace_target(pace):
    if not pace:
        return {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}, None, None
    slow, fast = pace
    speed_slow = round(1000.0 / float(slow), 4)
    speed_fast = round(1000.0 / float(fast), 4)
    return {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}, speed_slow, speed_fast


def _build_steps(steps, counter):
    out = []
    for step in steps:
        counter[0] += 1
        order = counter[0]
        if step.get('type') == 'repeat':
            substeps = _build_steps(step.get('steps', []), counter)
            out.append({
                "stepOrder": order,
                "stepId": order,
                "type": "RepeatGroupDTO",
                "stepType": _step_type("repeat"),
                "smartRepeat": False,
                "numberOfIterations": int(step['count']),
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "workoutSteps": substeps,
            })
            continue
        cond, val = _end_condition(step.get('dur', {}))
        target, v1, v2 = _pace_target(step.get('pace'))
        node = {
            "stepOrder": order,
            "stepId": order,
            "type": "ExecutableStepDTO",
            "stepType": _step_type(step['type']),
            "endCondition": cond,
            "targetType": target,
        }
        if val is not None:
            node["endConditionValue"] = val
        if v1 is not None:
            node["targetValueOne"] = v1
            node["targetValueTwo"] = v2
        out.append(node)
    return out


def build_workout(name, steps):
    counter = [0]
    return {
        "workoutName": name[:80],
        "sportType": SPORT_RUNNING,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": SPORT_RUNNING,
            "workoutSteps": _build_steps(steps, counter),
        }],
    }


def _import_garth():
    import garth  # noqa: F401
    return garth


def garmin_resume(user_id):
    garth_dir = _garth_dir(user_id)
    if not os.path.isdir(garth_dir):
        return None, 'non connecté'
    try:
        garth = _import_garth()
        garth.resume(garth_dir)
        return garth, None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _short_garmin_name(prof):
    full = prof.get('fullName')
    if full and full.strip():
        return full.strip()
    name = prof.get('displayName') or 'connecté'
    if len(name) >= 30 and name.count('-') >= 3:
        return name.split('-')[0]
    return name


def garmin_status(user_id):
    garth, err = garmin_resume(user_id)
    if err:
        return {'ok': False, 'error': err}
    try:
        prof = garth.connectapi('/userprofile-service/socialProfile')
        return {'ok': True, 'name': _short_garmin_name(prof)}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def garmin_logout(user_id):
    garth_dir = _garth_dir(user_id)
    if os.path.isdir(garth_dir):
        shutil.rmtree(garth_dir, ignore_errors=True)
    return {'ok': True}


class LoginSession:
    __slots__ = ('user_id', 'mfa_requested', 'mfa_code', 'mfa_provided',
                 'done', 'success', 'error', 'name', 'created')

    def __init__(self, user_id):
        self.user_id = user_id
        self.mfa_requested = threading.Event()
        self.mfa_code = None
        self.mfa_provided = threading.Event()
        self.done = threading.Event()
        self.success = False
        self.error = None
        self.name = None
        self.created = time.monotonic()


login_sessions = {}  # sid -> LoginSession (user_id stocké sur la session)


def _login_worker(sess, email, password):
    try:
        garth = _import_garth()

        def prompt_mfa():
            sess.mfa_requested.set()
            if not sess.mfa_provided.wait(timeout=300):
                raise RuntimeError('MFA timeout (5 min sans code)')
            return sess.mfa_code or ''

        try:
            garth.login(email, password, prompt_mfa=prompt_mfa)
        except TypeError:
            garth.login(email, password)
        garth_dir = _garth_dir(sess.user_id)
        os.makedirs(garth_dir, exist_ok=True)
        garth.save(garth_dir)
        try:
            prof = garth.connectapi('/userprofile-service/socialProfile')
            sess.name = _short_garmin_name(prof)
        except Exception:
            pass
        sess.success = True
    except Exception as e:
        sess.error = f'{type(e).__name__}: {e}'
    finally:
        sess.done.set()


def _gc_login_sessions():
    cutoff = time.monotonic() - 600  # 10 min
    with login_lock:
        for sid in [k for k, v in login_sessions.items() if v.created < cutoff]:
            login_sessions.pop(sid, None)


def garmin_start_login(user_id, email, password):
    if not email or not password:
        raise ValueError('email et mot de passe requis')
    _gc_login_sessions()
    sid = secrets.token_urlsafe(16)
    sess = LoginSession(user_id)
    with login_lock:
        login_sessions[sid] = sess
    threading.Thread(target=_login_worker, args=(sess, email, password), daemon=True).start()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if sess.done.is_set() or sess.mfa_requested.is_set():
            break
        time.sleep(0.1)

    if sess.done.is_set():
        with login_lock:
            login_sessions.pop(sid, None)
        if sess.success:
            return {'ok': True, 'name': sess.name}
        return {'ok': False, 'error': sess.error or 'login failed'}
    if sess.mfa_requested.is_set():
        return {'ok': False, 'needs_mfa': True, 'session': sid}
    with login_lock:
        login_sessions.pop(sid, None)
    return {'ok': False, 'error': 'login timeout (Garmin lent ?)'}


def garmin_submit_mfa(user_id, sid, code):
    if not sid or not code:
        raise ValueError('session et code requis')
    with login_lock:
        sess = login_sessions.get(sid)
    if not sess or sess.user_id != user_id:
        return {'ok': False, 'error': 'session inconnue ou expirée'}
    sess.mfa_code = code.strip()
    sess.mfa_provided.set()
    if not sess.done.wait(timeout=60):
        return {'ok': False, 'error': 'login bloqué après MFA'}
    with login_lock:
        login_sessions.pop(sid, None)
    if sess.success:
        return {'ok': True, 'name': sess.name}
    return {'ok': False, 'error': sess.error or 'login failed'}


def garmin_push(user_id, payload):
    name = payload.get('name') or 'Workout'
    steps = payload.get('steps') or []
    iso_date = payload.get('date')
    if not steps:
        raise ValueError('steps vide')
    if not iso_date:
        raise ValueError('date manquante')
    _date.fromisoformat(iso_date)

    garth, err = garmin_resume(user_id)
    if err:
        raise RuntimeError(err)

    workout = build_workout(name, steps)
    garth_dir = _garth_dir(user_id)
    with _get_lock(_garmin_locks, user_id):
        created = garth.connectapi('/workout-service/workout', method='POST', json=workout)
        wid = created.get('workoutId')
        if not wid:
            raise RuntimeError(f'no workoutId in response: {created}')
        garth.connectapi(f'/workout-service/schedule/{wid}', method='POST', json={'date': iso_date})
        try:
            garth.save(garth_dir)
        except Exception:
            pass
    return {'workoutId': wid, 'date': iso_date}


# ============================================================
# Plan storage + LLM generation
# ============================================================
PLAN_DAYS = ('Mardi', 'Jeudi', 'Dimanche')
DAY_OFFSET = {'Lundi': 0, 'Mardi': 1, 'Mercredi': 2, 'Jeudi': 3,
              'Vendredi': 4, 'Samedi': 5, 'Dimanche': 6}
DISTANCE_MIN_WEEKS = {'5K': 6, '10K': 8, 'HM': 10, 'M': 14}
DISTANCE_M = {'5K': 5000, '10K': 10000, 'HM': 21097, 'M': 42195}

_DUR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "time": {"type": "integer"},
        "dist": {"type": "integer"},
        "lap": {"type": "boolean"},
    },
}
_PACE_SCHEMA = {
    "type": "array",
    "items": {"type": "integer"},
    "minItems": 2,
    "maxItems": 2,
}
_EXEC_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": ["warmup", "cooldown", "interval", "recovery", "other"]},
        "dur": _DUR_SCHEMA,
        "pace": _PACE_SCHEMA,
    },
    "required": ["type", "dur"],
}
_REPEAT_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": ["repeat"]},
        "count": {"type": "integer"},
        "steps": {
            "type": "array",
            "items": _EXEC_STEP_SCHEMA,
        },
    },
    "required": ["type", "count", "steps"],
}
_TOP_STEP_SCHEMA = {
    "anyOf": [_EXEC_STEP_SCHEMA, _REPEAT_STEP_SCHEMA],
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "raceDate": {"type": "string"},
        "raceDistance": {"type": "string", "enum": ["5K", "10K", "HM", "M"]},
        "targetTime": {"type": "string"},
        "weeks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "num": {"type": "integer"},
                    "phase": {"type": "string"},
                    "light": {"type": "boolean"},
                    "race": {"type": "boolean"},
                    "volume": {"type": "string"},
                    "sessions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "day": {"type": "string", "enum": list(PLAN_DAYS)},
                                "title": {"type": "string"},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "do": {"type": "string"},
                                            "pace": {"type": "string"},
                                        },
                                        "required": ["do"],
                                    },
                                },
                                "note": {"type": "string"},
                                "garmin": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string"},
                                        "steps": {
                                            "type": "array",
                                            "items": _TOP_STEP_SCHEMA,
                                        },
                                    },
                                    "required": ["name", "steps"],
                                },
                            },
                            "required": ["day", "title", "items"],
                        },
                    },
                },
                "required": ["num", "phase", "light", "volume", "sessions"],
            },
        },
    },
    "required": ["raceDate", "raceDistance", "targetTime", "weeks"],
}


def load_plan(user_id):
    try:
        with open(_plan_file(user_id), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def save_plan(user_id, plan):
    plan_file = _plan_file(user_id)
    with _get_lock(_plan_locks, user_id):
        os.makedirs(os.path.dirname(plan_file), exist_ok=True)
        tmp = plan_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, plan_file)


def delete_plan(user_id):
    with _get_lock(_plan_locks, user_id):
        try:
            os.unlink(_plan_file(user_id))
        except FileNotFoundError:
            pass


def _parse_hms(s):
    raw = (s or '').strip().lower().replace(' ', '')
    if not raw:
        raise ValueError('temps vide')

    if 'h' in raw:
        h, _, rest = raw.partition('h')
        rest = rest.replace('m', ':').replace('s', '')
        rest = rest.strip(':')
        h_int = int(h) if h else 0
        if not rest:
            m_int, s_int = 0, 0
        else:
            parts = rest.split(':')
            m_int = int(parts[0]) if parts[0] else 0
            s_int = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        return h_int * 3600 + m_int * 60 + s_int

    if 'm' in raw:
        m, _, rest = raw.partition('m')
        rest = rest.replace('s', '')
        return int(m) * 60 + (int(rest) if rest else 0)

    parts = raw.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    raise ValueError(f'temps invalide: {s}')


def _weeks_between(start_iso, end_iso):
    a = _date.fromisoformat(start_iso)
    b = _date.fromisoformat(end_iso)
    days = (b - a).days
    if days < 0:
        return 0
    return days // 7 + 1


def build_plan_prompt(inputs, n_weeks):
    distance = inputs['distance']
    distance_m = DISTANCE_M.get(distance)
    target_sec = _parse_hms(inputs['targetTime'])
    target_pace = target_sec / (distance_m / 1000.0)
    current_dist = inputs['currentDistance']
    current_dist_m = DISTANCE_M.get(current_dist)
    current_sec = _parse_hms(inputs['currentTime'])
    current_pace = current_sec / (current_dist_m / 1000.0)
    weekly_km = inputs.get('weeklyKm') or 25
    days_per_week = inputs.get('daysPerWeek') or 3
    long_run_day = inputs.get('longRunDay') or 'Dimanche'
    start_date = inputs['startDate']
    race_date = inputs['raceDate']

    valid_days = ['Mardi', 'Mercredi', 'Jeudi', 'Vendredi'] if days_per_week >= 4 else ['Mardi', 'Jeudi']
    if days_per_week >= 4:
        valid_days = valid_days[:days_per_week - 1]
    valid_days = valid_days + [long_run_day]

    return [
        {"role": "system", "content": (
            "Tu es un coach de course à pied expérimenté. Tu produis des plans d'entraînement structurés au format JSON, "
            "en français, exploitables directement par une montre Garmin. "
            "Ton output doit être STRICTEMENT le JSON demandé, sans texte autour. "
            "/no_think"
        )},
        {"role": "user", "content": f"""Génère un plan d'entraînement pour atteindre un objectif de course.

PARAMÈTRES UTILISATEUR
- Distance course      : {distance} ({distance_m} m)
- Date de la course    : {race_date} (dimanche)
- Temps cible          : {inputs['targetTime']} (allure cible {target_pace:.0f} s/km)
- Niveau actuel        : {inputs['currentTime']} sur {current_dist} (allure actuelle {current_pace:.0f} s/km)
- Volume hebdomadaire  : {weekly_km} km
- Date de début        : {start_date} (lundi)
- Jours d'entraînement : {days_per_week} par semaine
- Sortie longue        : {long_run_day}
- Nombre de semaines   : {n_weeks}

CONTRAINTES
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
8. Allures de référence (s/km) :
   - Endurance fondamentale : [390, 350]
   - Trot récupération      : [420, 390]
   - Allure cible course    : [{int(target_pace) + 5}, {int(target_pace) - 5}]
   Adapte les autres allures au niveau de l'athlète.
9. `items` reflète `garmin.steps` en français lisible (ex: {{do:"20 min échauffement", pace:"5:50–6:30/km"}}).
10. Range les écarts d'allures dans des ranges courts (5–15s).

Réponds par le JSON conforme au schéma. Aucun commentaire."""}
    ]


def llm_generate(messages, schema):
    import requests
    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 16000,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "training_plan",
                "strict": True,
                "schema": schema,
            },
        },
    }
    r = requests.post(
        f'{LLM_BASE_URL}/chat/completions',
        json=body,
        headers={'Content-Type': 'application/json'},
        timeout=LLM_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f'LLM HTTP {r.status_code}: {r.text[:300]}')
    data = r.json()
    msg = data['choices'][0].get('message', {})
    content = (msg.get('content') or '').strip() or (msg.get('reasoning_content') or '').strip()
    if not content:
        raise RuntimeError('LLM réponse vide')
    return json.loads(content)


def validate_plan(plan, inputs):
    errs = []
    weeks = plan.get('weeks') or []
    expected_n = _weeks_between(inputs['startDate'], inputs['raceDate'])
    min_n = DISTANCE_MIN_WEEKS.get(inputs['distance'], 8)

    if len(weeks) < min_n:
        errs.append(f'plan trop court ({len(weeks)} sem, min {min_n} pour {inputs["distance"]})')
    if abs(len(weeks) - expected_n) > 1:
        errs.append(f'nombre de semaines {len(weeks)} attendu ~{expected_n}')

    for w in weeks:
        sessions = w.get('sessions') or []
        if not sessions:
            errs.append(f'semaine {w.get("num")}: aucune séance')
        for s in sessions:
            if s.get('day') not in PLAN_DAYS:
                errs.append(f'semaine {w.get("num")}: jour invalide "{s.get("day")}"')

    if weeks:
        last = weeks[-1]
        if not last.get('race'):
            errs.append(f'dernière semaine {last.get("num")} non marquée race=true')

    target_sec = _parse_hms(inputs['targetTime'])
    target_pace = target_sec / (DISTANCE_M[inputs['distance']] / 1000.0)
    if target_pace < 180 or target_pace > 540:
        errs.append(f'allure cible irréaliste ({target_pace:.0f} s/km)')

    return errs


class FieldErrors(Exception):
    def __init__(self, errors):
        super().__init__(', '.join(f'{k}: {v}' for k, v in errors.items()))
        self.errors = errors


def _validate_inputs(inputs):
    errs = {}
    required_labels = {
        'distance': 'distance',
        'raceDate': 'date de la course',
        'targetTime': 'temps cible',
        'currentDistance': 'distance de référence',
        'currentTime': 'temps récent',
        'startDate': 'date de début',
    }
    for k, label in required_labels.items():
        if not inputs.get(k):
            errs[k] = 'requis'

    if inputs.get('distance') and inputs['distance'] not in DISTANCE_M:
        errs['distance'] = 'distance invalide'
    if inputs.get('currentDistance') and inputs['currentDistance'] not in DISTANCE_M:
        errs['currentDistance'] = 'distance invalide'

    target_sec = None
    if inputs.get('targetTime') and 'targetTime' not in errs:
        try:
            target_sec = _parse_hms(inputs['targetTime'])
        except ValueError:
            errs['targetTime'] = 'format invalide (ex: 45:00 ou 1:30:00)'

    cur_sec = None
    if inputs.get('currentTime') and 'currentTime' not in errs:
        try:
            cur_sec = _parse_hms(inputs['currentTime'])
        except ValueError:
            errs['currentTime'] = 'format invalide (ex: 51:00 ou 1:45:00)'

    if inputs.get('raceDate') and 'raceDate' not in errs:
        try:
            _date.fromisoformat(inputs['raceDate'])
        except ValueError:
            errs['raceDate'] = 'date invalide'
    if inputs.get('startDate') and 'startDate' not in errs:
        try:
            _date.fromisoformat(inputs['startDate'])
        except ValueError:
            errs['startDate'] = 'date invalide'

    if 'startDate' not in errs and 'raceDate' not in errs and inputs.get('startDate') and inputs.get('raceDate'):
        n_weeks = _weeks_between(inputs['startDate'], inputs['raceDate'])
        if n_weeks <= 0:
            errs['raceDate'] = 'doit être postérieure à la date de début'
        elif inputs.get('distance') in DISTANCE_M:
            min_n = DISTANCE_MIN_WEEKS[inputs['distance']]
            if n_weeks < min_n:
                errs['raceDate'] = f'délai trop court : {n_weeks} sem, minimum {min_n} pour un {inputs["distance"]}'

    if (target_sec is not None and cur_sec is not None
            and inputs.get('distance') in DISTANCE_M
            and inputs.get('currentDistance') in DISTANCE_M):
        target_pace = target_sec / (DISTANCE_M[inputs['distance']] / 1000.0)
        cur_pace = cur_sec / (DISTANCE_M[inputs['currentDistance']] / 1000.0)
        if target_pace < cur_pace * 0.85:
            errs['targetTime'] = (
                f'objectif probablement irréaliste : {target_pace:.0f} s/km '
                f'vs {cur_pace:.0f} s/km sur ta réf actuelle'
            )

    if errs:
        raise FieldErrors(errs)


def generate_plan(inputs):
    _validate_inputs(inputs)
    n_weeks = _weeks_between(inputs['startDate'], inputs['raceDate'])

    messages = build_plan_prompt(inputs, n_weeks)
    plan = llm_generate(messages, PLAN_SCHEMA)
    errs = validate_plan(plan, inputs)
    if errs:
        messages.append({"role": "assistant", "content": json.dumps(plan)})
        messages.append({"role": "user", "content": (
            "Le plan a les défauts suivants. Corrige-les en gardant la structure et les contraintes :\n- "
            + "\n- ".join(errs)
            + "\nRéponds UNIQUEMENT par le JSON corrigé."
        )})
        plan = llm_generate(messages, PLAN_SCHEMA)
        errs = validate_plan(plan, inputs)

    plan['_inputs'] = inputs
    plan['_warnings'] = errs
    return plan


# ---- Async job runner ----
plan_jobs = {}  # jid -> { user_id, status, plan, error, created, finished, inputs }


def _gc_plan_jobs():
    cutoff = time.time() - 3600
    with plan_jobs_lock:
        stale = [k for k, v in plan_jobs.items()
                 if v.get('finished') and v['finished'] < cutoff]
        for k in stale:
            plan_jobs.pop(k, None)


def _plan_worker(jid, inputs):
    try:
        plan = generate_plan(inputs)
        with plan_jobs_lock:
            if jid in plan_jobs:
                plan_jobs[jid].update({
                    'status': 'done',
                    'plan': plan,
                    'finished': time.time(),
                })
    except FieldErrors as e:
        with plan_jobs_lock:
            if jid in plan_jobs:
                plan_jobs[jid].update({
                    'status': 'error',
                    'errors': e.errors,
                    'finished': time.time(),
                })
    except Exception as e:
        with plan_jobs_lock:
            if jid in plan_jobs:
                plan_jobs[jid].update({
                    'status': 'error',
                    'error': f'{type(e).__name__}: {e}',
                    'finished': time.time(),
                })


def start_plan_job(user_id, inputs):
    _validate_inputs(inputs)
    _gc_plan_jobs()
    jid = secrets.token_urlsafe(12)
    with plan_jobs_lock:
        plan_jobs[jid] = {
            'user_id': user_id,
            'status': 'running',
            'created': time.time(),
            'inputs': inputs,
        }
    threading.Thread(target=_plan_worker, args=(jid, inputs), daemon=True).start()
    return {'jobId': jid}


def get_plan_job(user_id, jid):
    with plan_jobs_lock:
        job = plan_jobs.get(jid)
        if not job or job.get('user_id') != user_id:
            return None
        snap = dict(job)
    out = {
        'status': snap['status'],
        'elapsedSec': int(time.time() - snap['created']),
    }
    if snap['status'] == 'done':
        out['plan'] = snap['plan']
    elif snap['status'] == 'error':
        if 'errors' in snap:
            out['errors'] = snap['errors']
        else:
            out['error'] = snap.get('error') or 'unknown error'
    return out


def cancel_plan_job(user_id, jid):
    with plan_jobs_lock:
        job = plan_jobs.get(jid)
        if job and job.get('user_id') == user_id:
            plan_jobs.pop(jid, None)
    return {'ok': True}


# ============================================================
# HTTP handler
# ============================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def send_header(self, key, value):
        if key.lower() == 'last-modified':
            return
        super().send_header(key, value)

    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def _user(self):
        raw = self.headers.get(USER_HEADER) or DEFAULT_USER
        user_id = _sanitize_user(raw)
        if not user_id:
            return None
        return user_id

    def _require_user(self):
        user_id = self._user()
        if not user_id:
            self._json(401, {'error': 'utilisateur non identifié (header forward-auth manquant)'})
            return None
        return user_id

    def _json(self, code, body):
        data = json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def do_GET(self):
        if self.path == '/api/whoami':
            user_id = self._require_user()
            if not user_id:
                return
            # Les headers Authentik supplémentaires (email/name/groups/uid)
            # sont injectés par la Middleware forward-auth quand le user est
            # logué via la gate forward_domain. Ils servent uniquement à
            # l'affichage côté UI — le user_id reste la seule clé de
            # namespacing data sur /data/<user_id>/.
            return self._json(200, {
                'user':   user_id,
                'email':  self.headers.get('X-Authentik-Email') or '',
                'name':   self.headers.get('X-Authentik-Name') or '',
                'groups': self.headers.get('X-Authentik-Groups') or '',
                'uid':    self.headers.get('X-Authentik-Uid') or '',
            })
        if self.path == '/api/state':
            user_id = self._require_user()
            if not user_id:
                return
            try:
                with open(_state_file(user_id), 'rb') as f:
                    body = f.read()
            except FileNotFoundError:
                body = b'{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/api/garmin/status':
            user_id = self._require_user()
            if not user_id:
                return
            return self._json(200, garmin_status(user_id))
        if self.path == '/api/plan':
            user_id = self._require_user()
            if not user_id:
                return
            plan = load_plan(user_id)
            if plan is None:
                return self._json(404, {'error': 'no plan saved'})
            return self._json(200, plan)
        if self.path.startswith('/api/plan/jobs/'):
            user_id = self._require_user()
            if not user_id:
                return
            jid = self.path[len('/api/plan/jobs/'):]
            job = get_plan_job(user_id, jid)
            if job is None:
                return self._json(404, {'error': 'job introuvable'})
            return self._json(200, job)
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        return super().do_GET()

    def do_PUT(self):
        if self.path == '/api/state':
            user_id = self._require_user()
            if not user_id:
                return
            body = self._read_body()
            if body is None:
                self.send_response(413); self.end_headers(); return
            try:
                json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400); self.end_headers(); return
            state_file = _state_file(user_id)
            with _get_lock(_write_locks, user_id):
                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                tmp = state_file + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, state_file)
            self.send_response(204); self.end_headers()
            return
        if self.path == '/api/plan':
            user_id = self._require_user()
            if not user_id:
                return
            body = self._read_body()
            if body is None:
                return self._json(413, {'error': 'body too large'})
            try:
                plan = json.loads(body)
            except json.JSONDecodeError:
                return self._json(400, {'error': 'invalid json'})
            save_plan(user_id, plan)
            return self._json(200, {'ok': True})
        self.send_response(405); self.end_headers()

    def do_POST(self):
        if self.path in ('/api/garmin/push', '/api/garmin/login', '/api/garmin/login/mfa',
                         '/api/garmin/logout', '/api/plan/generate'):
            user_id = self._require_user()
            if not user_id:
                return
            body = self._read_body() if self.path != '/api/garmin/logout' else b'{}'
            if body is None:
                return self._json(413, {'error': 'body too large'})
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                return self._json(400, {'error': 'invalid json'})
            try:
                if self.path == '/api/garmin/push':
                    return self._json(200, garmin_push(user_id, payload))
                if self.path == '/api/garmin/login':
                    return self._json(200, garmin_start_login(user_id, payload.get('email'), payload.get('password')))
                if self.path == '/api/garmin/login/mfa':
                    return self._json(200, garmin_submit_mfa(user_id, payload.get('session'), payload.get('code')))
                if self.path == '/api/garmin/logout':
                    return self._json(200, garmin_logout(user_id))
                if self.path == '/api/plan/generate':
                    return self._json(202, start_plan_job(user_id, payload))
            except FieldErrors as e:
                return self._json(400, {'errors': e.errors})
            except ValueError as e:
                return self._json(400, {'error': str(e)})
            except Exception as e:
                return self._json(502, {'error': f'{type(e).__name__}: {e}'})
        self.send_response(405); self.end_headers()

    def do_DELETE(self):
        if self.path == '/api/plan':
            user_id = self._require_user()
            if not user_id:
                return
            delete_plan(user_id)
            return self._json(200, {'ok': True})
        if self.path.startswith('/api/plan/jobs/'):
            user_id = self._require_user()
            if not user_id:
                return
            jid = self.path[len('/api/plan/jobs/'):]
            return self._json(200, cancel_plan_job(user_id, jid))
        self.send_response(405); self.end_headers()


class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f'serving {STATIC_DIR} on :{PORT}, data root {DATA_DIR}, '
          f'user header {USER_HEADER} (fallback {DEFAULT_USER!r})', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

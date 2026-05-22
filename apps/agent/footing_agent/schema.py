"""JSON schema décrit la sortie attendue du LLM.

Copié verbatim depuis apps/web/server.py — toute évolution doit
rester compatible avec le frontend (apps/web/static/index.html lit
ces champs directement via adoptServerPlan / renderSession).
"""

PLAN_DAYS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
DISTANCE_MIN_WEEKS = {"5K": 6, "10K": 8, "HM": 10, "M": 14}
DISTANCE_M = {"5K": 5000, "10K": 10000, "HM": 21097, "M": 42195}

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

"""Tests du LLM client avec requests mocké : forme du body,
parsing de la réponse, errors HTTP — sans toucher au LM Studio réel.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from footing_agent.llm import LLMError, llm_complete
from footing_agent.schema import PLAN_SCHEMA


def _fake_response(status: int, content: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if content is not None:
        r.json.return_value = {"choices": [{"message": {"content": json.dumps(content)}}]}
    return r


def test_body_shape_uses_json_schema(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _fake_response(200, content={"raceDate": "2026-10-11", "raceDistance": "10K",
                                            "targetTime": "45:00", "weeks": []})

    with patch("footing_agent.llm.requests.post", side_effect=fake_post):
        llm_complete([{"role": "user", "content": "hi"}], PLAN_SCHEMA, base_url="http://x/v1", model="m")

    assert captured["url"] == "http://x/v1/chat/completions"
    body = captured["body"]
    assert body["model"] == "m"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] is PLAN_SCHEMA


def test_http_error_raises():
    with patch("footing_agent.llm.requests.post", return_value=_fake_response(500, text="boom")):
        with pytest.raises(LLMError, match="500"):
            llm_complete([{"role": "user", "content": "x"}], PLAN_SCHEMA)


def test_empty_content_raises():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"choices": [{"message": {"content": ""}}]}
    with patch("footing_agent.llm.requests.post", return_value=r):
        with pytest.raises(LLMError, match="vide"):
            llm_complete([{"role": "user", "content": "x"}], PLAN_SCHEMA)


def test_fallback_reasoning_content():
    """Certains modèles renvoient le JSON dans reasoning_content (Qwen think
    mode par ex). Le client doit s'en servir si content est vide."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": json.dumps({"raceDate": "2026-10-11", "raceDistance": "10K",
                                                  "targetTime": "45:00", "weeks": []}),
            }
        }]
    }
    with patch("footing_agent.llm.requests.post", return_value=r):
        out = llm_complete([{"role": "user", "content": "x"}], PLAN_SCHEMA)
    assert out["raceDistance"] == "10K"

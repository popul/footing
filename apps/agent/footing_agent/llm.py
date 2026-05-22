"""Client OpenAI-compatible vers LM Studio (ou tout endpoint équivalent).

Par défaut pointe sur l'instance LM Studio locale du repo
(macbookprom5:1234 avec qwen3.6-35b-a3b) ; override via env vars
LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT, ou via les paramètres de
llm_complete().
"""

import json
import os
from typing import Any

import requests


DEFAULT_BASE_URL = os.environ.get("LLM_BASE_URL", "http://macbookprom5.home:1234/v1")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3.6-35b-a3b")
DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "300"))


class LLMError(RuntimeError):
    pass


def llm_complete(
    messages: list[dict],
    schema: dict,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    max_tokens: int = 48000,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Appel chat-completion avec response_format = json_schema strict.

    Retourne le JSON parsé. Lève LLMError sur status HTTP != 200 ou
    réponse vide. Le tuple (`content`, fallback `reasoning_content`) gère
    les modèles qui renvoient le JSON dans le bloc de raisonnement.
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
        f"{base_url}/chat/completions",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    msg = data["choices"][0].get("message", {})
    content = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    if not content:
        raise LLMError("LLM réponse vide")
    return json.loads(content)

"""Client OpenAI-compatible vers LM Studio (ou tout endpoint équivalent).

Par défaut pointe sur l'instance LM Studio locale du repo
(macbookprom5:1234 avec qwen3.6-35b-a3b) ; override via env vars
LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT, ou via les paramètres de
llm_complete().

Debug : `export LLM_DEBUG_DUMP=/tmp/llm-dumps` → chaque appel dump
le body envoyé et la réponse raw (avant json.loads) dans ce dossier,
horodaté. Utile pour comprendre pourquoi le modèle dépasse max_tokens
ou produit du JSON invalide.
"""

import json
import os
import pathlib
import time
import uuid
from typing import Any

import requests


DEFAULT_BASE_URL = os.environ.get("LLM_BASE_URL", "http://macbookprom5.home:1234/v1")
# qwen3.5-27b distillé sur Claude Opus 4.6 pour le reasoning. Suit
# bien les critères d'acceptance du prompt sans dégénérer (vs MLX
# Qwen 3.5 35B qui bouclait jusqu'à hit max_tokens). Comme tous les
# Qwen-reasoning il route sa génération dans reasoning_content au
# lieu de content — le fallback `content or reasoning` côté
# llm_complete s'en occupe.
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen3.5-27b-claude-4.6-opus-reasoning-distilled")
DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "600"))
DEBUG_DUMP_DIR = os.environ.get("LLM_DEBUG_DUMP", "")


class LLMError(RuntimeError):
    pass


def _dump(prefix: str, ext: str, content: str) -> str | None:
    if not DEBUG_DUMP_DIR:
        return None
    pathlib.Path(DEBUG_DUMP_DIR).mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{prefix}.{ext}"
    path = pathlib.Path(DEBUG_DUMP_DIR) / fname
    path.write_text(content, encoding="utf-8")
    return str(path)


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
    _dump("request", "json", json.dumps(body, ensure_ascii=False, indent=2))

    r = requests.post(
        f"{base_url}/chat/completions",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if r.status_code != 200:
        _dump("response-http-error", "txt", f"status={r.status_code}\n\n{r.text}")
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")

    # Dump le payload complet AVANT toute manipulation — c'est ce qui
    # permet de voir reasoning_content, finish_reason (length=tronqué),
    # usage.completion_tokens, etc.
    dump_path = _dump("response-raw", "json", r.text)

    data = r.json()
    choice = data["choices"][0]
    msg = choice.get("message", {})
    finish = choice.get("finish_reason")
    usage = data.get("usage", {})

    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    payload = content or reasoning

    if not payload:
        raise LLMError(f"LLM réponse vide (finish={finish}, usage={usage})")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        hint = ""
        if finish == "length":
            hint = (
                f" — finish_reason=length, le modèle a été coupé à "
                f"max_tokens={max_tokens} (sortie {usage.get('completion_tokens','?')} tokens, "
                f"{len(payload)} chars)."
            )
        if dump_path:
            hint += f" Voir dump : {dump_path}"
        raise LLMError(f"JSON invalide: {e}{hint}") from e

"""Configurable LOCAL model client (OpenAI-compatible /v1/chat/completions).

Dionysus calls a self-hosted model endpoint for draft text. The endpoint is
configurable and provider-neutral — Ollama, llama.cpp, vLLM, and LM Studio all
expose the OpenAI chat-completions shape. When the endpoint is unset, or on ANY
error/timeout, callers fall back to the deterministic templates in
``app/generator.py`` so generation never crashes Mise's path and CI runs offline
with no live model calls.

Local inference is treated as free: ``cost_usd`` is always ``0.0``.
"""

from __future__ import annotations

import logging
import time

import httpx

from . import config

log = logging.getLogger("dionysus.model_client")


def is_enabled() -> bool:
    return bool(config.MODEL_ENDPOINT and config.MODEL_NAME)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.MODEL_API_KEY:
        headers["Authorization"] = f"Bearer {config.MODEL_API_KEY}"
    return headers


def complete(messages, *, max_tokens=None, temperature=None,
             client: httpx.Client | None = None) -> dict | None:
    """Call the local model. Returns a result dict, or ``None`` on any failure.

    Result: ``{"text", "model", "latency_ms", "cost_usd"}``. ``None`` signals the
    caller to fall back to deterministic drafting. An injected ``client`` is used
    as-is and not closed (the caller owns it); otherwise one is created per call.
    """
    if not is_enabled():
        return None
    payload = {
        "model": config.MODEL_NAME,
        "messages": list(messages),
        "temperature": config.MODEL_TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.MODEL_MAX_TOKENS if max_tokens is None else max_tokens,
        "stream": False,
    }
    url = f"{config.MODEL_ENDPOINT}/chat/completions"
    owns_client = client is None
    start = time.perf_counter()
    try:
        if owns_client:
            client = httpx.Client(timeout=config.MODEL_TIMEOUT)
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("local model call failed: %s", exc)
        return None
    finally:
        if owns_client and client is not None:
            client.close()
    latency_ms = int((time.perf_counter() - start) * 1000)
    text = _extract_text(data)
    if not text:
        log.warning("local model returned empty content")
        return None
    return {
        "text": text,
        "model": str(data.get("model") or config.MODEL_NAME),
        "latency_ms": latency_ms,
        "cost_usd": 0.0,
    }


def _extract_text(data) -> str:
    try:
        return str(data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""

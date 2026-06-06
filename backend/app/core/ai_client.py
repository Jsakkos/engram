"""Shared AI client for structured-JSON completions across providers.

Wraps anthropic, openai, openrouter, and gemini behind a single
`complete_json` entry point. Each provider adapter handles its own
authentication and structured-JSON convention (prompt-only for anthropic,
response_format for openai/openrouter, responseSchema for gemini).
"""

import asyncio
import json
import logging
import random

import httpx

from app.core.errors import AIProviderError

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "openrouter": "anthropic/claude-haiku-4-5-20251001",
    "gemini": "gemini-2.5-flash-lite",
}

_TIMEOUT_SECONDS = 30.0

MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


async def _with_429_retry(coro_factory):
    """Call coro_factory() up to MAX_RETRIES+1 times, backing off on 429.

    coro_factory must be a no-arg callable returning a fresh coroutine each call.
    """
    delay = _BACKOFF_BASE
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == MAX_RETRIES:
                raise
            jitter = random.uniform(0, 0.25 * delay)
            await asyncio.sleep(delay + jitter)
            delay *= 2
    return None


async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
    raise_on_error: bool = False,
) -> dict | None:
    """Send a prompt to an LLM provider and return its JSON response as a dict.

    Returns None on any failure (network, HTTP, malformed JSON). Callers must
    treat None as "no usable result" and fall back to other behaviour.

    When ``raise_on_error`` is True, a transport/HTTP failure raises
    ``AIProviderError`` and an unexpected (non-transport) error propagates as-is,
    so callers can distinguish a provider outage from a genuine bug. The
    early-return paths (empty ``api_key``, unknown provider) and an empty or
    unparseable response body still return None regardless.
    """
    if not api_key:
        logger.debug("complete_json called with empty api_key; returning None")
        return None

    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        logger.warning("Unknown AI provider: %s", provider)
        return None

    if provider == "anthropic":

        def factory():
            return _call_anthropic(prompt, api_key, model, max_tokens)
    elif provider == "openai":

        def factory():
            return _call_openai_compatible(
                prompt, api_key, OPENAI_API_URL, model, max_tokens, schema
            )
    elif provider == "openrouter":

        def factory():
            return _call_openai_compatible(
                prompt, api_key, OPENROUTER_API_URL, model, max_tokens, schema
            )
    elif provider == "gemini":

        def factory():
            return _call_gemini(prompt, api_key, model, max_tokens, schema)
    else:
        logger.warning("Unsupported AI provider: %s", provider)
        return None

    try:
        return await _with_429_retry(factory)
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e, exc_info=True)
        if raise_on_error:
            raise AIProviderError(f"{provider} request failed: {e}") from e
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        if raise_on_error:
            # Deliberate: let unexpected (non-transport) errors propagate unwrapped so
            # callers classify them as internal errors, not retryable provider errors.
            raise
        return None


def _to_gemini_schema(schema):
    """Translate a standard JSON Schema into Gemini's OpenAPI-subset dialect.

    Gemini's ``responseSchema`` is Protobuf-backed and accepts only a single
    scalar ``type`` plus a separate ``nullable`` flag. A JSON-Schema union such
    as ``type: ["object", "null"]`` is rejected with HTTP 400 ("Proto field is
    not repeating, cannot start list"), which silently disables *all* Gemini
    structured-output calls. This rewrites union types to ``type: <T>`` +
    ``nullable: True`` and recurses through ``properties``/``items`` so nested
    shapes are converted too. Returns a new object; the caller's schema is left
    unmutated (it is shared with other providers).
    """
    if isinstance(schema, list):
        return [_to_gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            if "null" in value:
                out["nullable"] = True
            # Gemini takes a single type; keep the first concrete one.
            out["type"] = non_null[0] if non_null else "null"
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


def _parse_json_text(text: str) -> dict | None:
    """Parse JSON, tolerating ```json fences and surrounding whitespace."""
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse AI response as JSON: %s", text[:200])
        return None
    return data if isinstance(data, dict) else None


async def _call_anthropic(prompt: str, api_key: str, model: str, max_tokens: int) -> dict | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        if not content:
            return None
        text = content[0].get("text", "")
        return _parse_json_text(text)


async def _call_openai_compatible(
    prompt: str,
    api_key: str,
    api_url: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
) -> dict | None:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        text = choices[0].get("message", {}).get("content", "")
        return _parse_json_text(text)


async def _call_gemini(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
) -> dict | None:
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    generation_config: dict = {
        "responseMimeType": "application/json",
        "maxOutputTokens": max_tokens,
        "temperature": 0,
    }
    if schema is not None:
        generation_config["responseSchema"] = _to_gemini_schema(schema)

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            url,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            return None
        text = parts[0].get("text", "")
        return _parse_json_text(text)

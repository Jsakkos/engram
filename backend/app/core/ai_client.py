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

from app.core.errors import AIProviderError, ProviderErrorCode
from app.core.security import sanitize_log_value

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

_MAX_DETAIL_CHARS = 2048

# Human sentences per cause. Rendered verbatim in the UI, so they must be
# actionable and must never contain the provider's raw body.
_CAUSE_MESSAGES: dict[ProviderErrorCode, str] = {
    "bad_key": "The API key was rejected. Check it was copied in full and belongs to {provider}.",
    "no_credits": (
        "{provider} accepted the key but the account has no API credits. "
        "Note that a paid chat subscription does not include API usage."
    ),
    "rate_limited": "{provider} is rate limiting this key. Wait a moment and try again.",
    "model_unavailable": "This key does not have access to the model Engram uses for {provider}.",
    "bad_request": "{provider} rejected the request.",
    "network": "Could not reach {provider}. Check the internet connection.",
    "timeout": "{provider} did not respond in time.",
    "response_truncated": (
        "{provider} cut the response off before it was complete. "
        "This usually means the reply exceeded the token budget."
    ),
    "malformed_response": "{provider} returned a reply that was not valid JSON.",
    "unknown": "{provider} returned an unexpected error.",
}


def classify_provider_error(provider: str, exc: Exception) -> tuple[ProviderErrorCode, str]:
    """Map a provider failure to a stable ``(code, human_message)`` pair.

    The provider's own body is the only thing that separates a permanently
    exhausted quota from transient throttling (both are HTTP 429 on OpenAI), so
    this reads it. The body is never returned to the caller; it is summarised
    into a fixed sentence and left to the logs.
    """
    code: ProviderErrorCode
    if isinstance(exc, httpx.TimeoutException):
        code = "timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        code = _classify_status(provider, exc)
    elif isinstance(exc, httpx.HTTPError):
        code = "network"
    else:
        code = "unknown"
    return code, _CAUSE_MESSAGES[code].format(provider=provider)


def _classify_status(provider: str, exc: httpx.HTTPStatusError) -> ProviderErrorCode:
    status = exc.response.status_code
    try:
        body = (exc.response.text or "")[:_MAX_DETAIL_CHARS].lower()
    except Exception:  # noqa: BLE001 — a body we cannot read must not mask the status
        body = ""

    # OpenRouter signals exhausted credit with 402, unlike OpenAI's 429.
    if status == 402:
        return "no_credits"
    if status in (401, 403):
        # Gemini uses 403 PERMISSION_DENIED for model access, not for a bad key.
        if status == 403 and "permission_denied" in body:
            return "model_unavailable"
        return "bad_key"
    if status == 404:
        return "model_unavailable"
    if status == 429:
        if "insufficient_quota" in body or "exceeded your current quota" in body:
            return "no_credits"
        return "rate_limited"
    if status == 400:
        # Gemini returns 400 INVALID_ARGUMENT for nearly every validation failure
        # (bad prompt, bad schema, bad model name, ...), not just a bad key — see
        # PR #344, where a schema-union request 400'd with "Proto field is not
        # repeating, cannot start list." Only a body that actually names the API
        # key counts as bad_key; everything else is an honest bad_request.
        if "api key" in body:
            return "bad_key"
        return "bad_request"
    return "unknown"


def detail_from(exc: Exception) -> str:
    """The provider's own body, capped, for logs and diagnostics only."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)[:_MAX_DETAIL_CHARS]
    try:
        return (response.text or "")[:_MAX_DETAIL_CHARS]
    except Exception:  # noqa: BLE001
        return ""


async def _with_429_retry(coro_factory, *, provider: str, retries: int = MAX_RETRIES):
    """Call coro_factory() up to retries+1 times, backing off on 429.

    A 429 that classifies as ``no_credits`` is permanent within any retry window,
    so it is re-raised immediately rather than paying the backoff ladder for a
    guaranteed failure.

    coro_factory must be a no-arg callable returning a fresh coroutine each call.
    """
    delay = _BACKOFF_BASE
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == retries:
                raise
            if classify_provider_error(provider, e)[0] == "no_credits":
                raise
            jitter = random.uniform(0, 0.25 * delay)
            await asyncio.sleep(delay + jitter)
            delay *= 2
    return None


class _TruncatedResponse(Exception):
    """Internal: provider stopped generating because it hit the token budget.

    Not an AIProviderError: complete_json translates it, so the adapters stay
    free of the raise_on_error policy decision.
    """


async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
    raise_on_error: bool = False,
    retries: int = MAX_RETRIES,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict | None:
    """Send a prompt to an LLM provider and return its JSON response as a dict.

    Returns None on any failure (network, HTTP, malformed JSON) when
    ``raise_on_error`` is False (the default). Callers must treat None as "no
    usable result" and fall back to other behaviour.

    When ``raise_on_error`` is True, a transport/HTTP failure raises
    ``AIProviderError`` (carrying a classified ``code`` and provider ``detail``),
    a truncated response (provider stopped at the token budget) raises it with
    code ``response_truncated``, an empty or unparseable response body raises it
    with code ``malformed_response``, and an unexpected (non-transport) error
    propagates as-is — so callers can distinguish a provider outage or a
    truncated/garbled reply from "the model ran and was not confident" (which
    stays a plain ``None`` from the layer above ``complete_json``, e.g.
    ``match_episode_via_llm`` returning None for a low-confidence match). The
    early-return paths (empty ``api_key``, unknown provider) still return None
    regardless of ``raise_on_error`` — see the trace in the code, they exit
    before the try/except that implements the policy above.

    ``retries`` and ``timeout`` let a caller (e.g. a "test connection"
    pre-flight check) fail fast instead of paying the full backoff ladder.
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
            return _call_anthropic(prompt, api_key, model, max_tokens, timeout)
    elif provider == "openai":

        def factory():
            return _call_openai_compatible(
                prompt, api_key, OPENAI_API_URL, model, max_tokens, schema, timeout
            )
    elif provider == "openrouter":

        def factory():
            return _call_openai_compatible(
                prompt, api_key, OPENROUTER_API_URL, model, max_tokens, schema, timeout
            )
    elif provider == "gemini":

        def factory():
            return _call_gemini(prompt, api_key, model, max_tokens, schema, timeout)
    else:
        logger.warning("Unsupported AI provider: %s", provider)
        return None

    try:
        result = await _with_429_retry(factory, provider=provider, retries=retries)
    except _TruncatedResponse:
        logger.warning("AI provider %s truncated its response (token budget)", provider)
        if raise_on_error:
            raise AIProviderError(
                _CAUSE_MESSAGES["response_truncated"].format(provider=provider),
                code="response_truncated",
            ) from None
        return None
    except httpx.HTTPError as e:
        code, message = classify_provider_error(provider, e)
        logger.warning(
            "AI provider %s failed (%s): %s | body=%s",
            provider,
            code,
            e,
            sanitize_log_value(detail_from(e)),
            exc_info=True,
        )
        if raise_on_error:
            raise AIProviderError(message, code=code, detail=detail_from(e)) from e
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        if raise_on_error:
            # Deliberate: let unexpected (non-transport) errors propagate unwrapped so
            # callers classify them as internal errors, not retryable provider errors.
            raise
        return None

    if result is None and raise_on_error:
        # The transport succeeded but the body was empty or unparseable. Callers
        # with raise_on_error set need this separated from "the model ran and had
        # no confident answer", which is also a None from the layer above.
        raise AIProviderError(
            _CAUSE_MESSAGES["malformed_response"].format(provider=provider),
            code="malformed_response",
        )
    return result


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


def _parse_json_text(text: str | None) -> dict | None:
    """Parse JSON, tolerating ```json fences and surrounding whitespace.

    Accepts None so a provider that sends an explicit null text field yields a
    clean "no usable result" rather than an AttributeError.
    """
    if not text:
        return None
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


async def _call_anthropic(
    prompt: str, api_key: str, model: str, max_tokens: int, timeout: float
) -> dict | None:
    async with httpx.AsyncClient(timeout=timeout) as client:
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
        if data.get("stop_reason") == "max_tokens":
            raise _TruncatedResponse
        content = data.get("content") or []
        if not content:
            return None
        text = content[0].get("text") or ""
        return _parse_json_text(text)


async def _call_openai_compatible(
    prompt: str,
    api_key: str,
    api_url: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
    timeout: float,
) -> dict | None:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
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
        if choices[0].get("finish_reason") == "length":
            raise _TruncatedResponse
        text = choices[0].get("message", {}).get("content") or ""
        return _parse_json_text(text)


async def _call_gemini(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
    timeout: float,
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

    async with httpx.AsyncClient(timeout=timeout) as client:
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
        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise _TruncatedResponse
        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            return None
        text = parts[0].get("text") or ""
        return _parse_json_text(text)

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
import re

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

# Ollama and LM Studio both expose the OpenAI-compatible /v1/chat/completions
# contract, so they reuse _call_openai_compatible with only the base URL
# differing. They are grouped here because several invariants elsewhere
# (credential presence, timeout, error wording) key off "is this local?" and
# must not each hardcode the slug list.
LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio"})

LOCAL_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

# DEFAULT_MODELS deliberately has no entry for the local slugs: there is no
# correct default, because the answer depends on which weights the user pulled.
# That means DEFAULT_MODELS can no longer double as the set of providers that
# exist, which is what this is for.
KNOWN_PROVIDERS = frozenset(DEFAULT_MODELS) | LOCAL_PROVIDERS


def resolve_local_base_url(provider: str, configured: str) -> str:
    """Base URL for a local provider: the configured value, else the default.

    Returns "" for a remote provider so a caller can use a truthy check as
    "is there a local endpoint here?" without a second membership test.
    A trailing slash is stripped so callers can append "/chat/completions"
    unconditionally without producing a double slash.
    """
    if provider not in LOCAL_PROVIDERS:
        return ""
    url = (configured or "").strip() or LOCAL_DEFAULT_BASE_URLS[provider]
    return url.rstrip("/")


def ai_is_configured(provider: str, api_key: str) -> bool:
    """True if this provider has what it needs to make a call.

    Local providers authenticate to nothing, so requiring a key would silently
    disable them at every call site that guards on one. Four such gates exist
    (this module, curator, matching_coordinator, identification_coordinator);
    they all call this rather than testing the key directly.
    """
    return provider in LOCAL_PROVIDERS or bool(api_key)


_TIMEOUT_SECONDS = 30.0

MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds

_MAX_DETAIL_CHARS = 2048

# Model ids are user-settable config (AppConfig.ai_model) and the Gemini one is
# interpolated into a URL *path*, so an unvalidated value is a request-forgery
# primitive, not just a typo: "../../v1beta/tunedModels/x" would retarget the
# request. Slashes are allowed because OpenRouter ids legitimately contain one
# ("anthropic/claude-haiku-4-5-20251001"); requiring each segment to start with an
# alphanumeric is what keeps that safe, since it rules out "." and "..". Anything
# with a space, query, or fragment is rejected outright.
# Anchored with \Z, not $: Python's $ also matches just before a single trailing
# newline, so "gemini-2.5-flash-lite\n" would pass a ^...$ check despite carrying
# exactly the whitespace this guard exists to reject.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]*(?:/[A-Za-z0-9][A-Za-z0-9._@:-]*)*\Z")

# How many model ids to name in a "your key cannot see this model" message. A key
# can list dozens; the message is rendered verbatim in the UI.
_MAX_LISTED_MODELS = 12


def _is_safe_model_name(model: str) -> bool:
    """True if ``model`` is safe to place in a request path or body.

    Rejects path traversal, absolute paths, whitespace, and URL metacharacters.
    Requiring every slash-separated segment to *start* with an alphanumeric is
    what rules out "..", "." and empty segments, so no separate traversal check
    is needed.
    """
    if not model or len(model) > 200:
        return False
    return bool(_MODEL_NAME_RE.match(model))


# Display names for the four provider slugs. The slugs themselves are lowercase
# identifiers, and these sentences are read by users, so "OpenAI accepted the
# key" beats "openai accepted the key". Mirrors AI_PROVIDER_LABELS in the
# frontend's ConfigWizard, which labels the same four providers.
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
}

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

# Shown when AppConfig.ai_model fails _is_safe_model_name. Says what to do, since
# the user cannot be expected to know which character offended.
_INVALID_MODEL_MESSAGE = (
    "The configured model name is not valid. Clear the AI model field in Settings "
    "to use Engram's default for {provider}."
)


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
    return code, _CAUSE_MESSAGES[code].format(provider=_PROVIDER_LABELS.get(provider, provider))


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


async def _gemini_available_models(api_key: str, timeout: float) -> list[str]:
    """Model ids this key can call generateContent on, or ``[]`` if unknowable.

    Gemini answers a 404 for a model the key cannot see, and resolves auth
    *before* the model (a bad key is 400, a missing key 403), so reaching a 404
    proves the credential works and the model is the whole problem. ListModels is
    the only thing that turns that into an actionable message, and it is free.

    Never raises: this runs while another failure is already being reported, and
    turning one error into two would be strictly worse than saying less.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                GEMINI_API_BASE,
                headers={"x-goog-api-key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001 — a best-effort hint must not mask the real error
        logger.debug("Gemini ListModels probe failed", exc_info=True)
        return []

    names: list[str] = []
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = str(entry.get("name") or "").removeprefix("models/")
        if name:
            names.append(name)
    return names


def _with_model_hint(message: str, model: str, available: list[str]) -> str:
    """Append what was asked for, and what the key could have had instead."""
    message = f"{message} Engram asked for '{model}'."
    if not available:
        return message
    shown = ", ".join(available[:_MAX_LISTED_MODELS])
    if len(available) > _MAX_LISTED_MODELS:
        shown += ", ..."
    return f"{message} Models available to this key: {shown}. Set one in Settings."


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
        logger.warning("Unknown AI provider: %s", sanitize_log_value(provider))
        return None

    if not _is_safe_model_name(model):
        # Unlike the two early returns above, this one honours raise_on_error: a
        # bad model name is a config mistake the user can fix, and silently
        # returning None would send them hunting for a key or network problem.
        logger.warning("Rejecting unsafe AI model name: %s", sanitize_log_value(model))
        if raise_on_error:
            raise AIProviderError(
                _INVALID_MODEL_MESSAGE.format(provider=_PROVIDER_LABELS.get(provider, provider)),
                code="bad_request",
            )
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
        logger.warning("Unsupported AI provider: %s", sanitize_log_value(provider))
        return None

    try:
        result = await _with_429_retry(factory, provider=provider, retries=retries)
    except _TruncatedResponse:
        # `provider` comes from AppConfig.ai_provider, which the user sets through
        # PUT /api/config, so it is untrusted input on every log line below.
        logger.warning(
            "AI provider %s truncated its response (token budget)",
            sanitize_log_value(provider),
        )
        if raise_on_error:
            raise AIProviderError(
                _CAUSE_MESSAGES["response_truncated"].format(provider=provider),
                code="response_truncated",
            ) from None
        return None
    except httpx.HTTPError as e:
        code, message = classify_provider_error(provider, e)
        # str(e) embeds the request URL, which is built from `model` (also
        # user-settable config), so it is sanitized alongside provider and body.
        logger.warning(
            "AI provider %s failed (%s): %s | body=%s",
            sanitize_log_value(provider),
            code,
            sanitize_log_value(str(e)),
            sanitize_log_value(detail_from(e)),
            exc_info=True,
        )
        if raise_on_error:
            if provider == "gemini" and code == "model_unavailable":
                # Only on the human-facing paths (the connection test and the
                # single-title Inspector re-match set raise_on_error). The bulk
                # matcher would pay one extra request per title for a message
                # nobody reads.
                message = _with_model_hint(
                    message, model, await _gemini_available_models(api_key, timeout)
                )
            raise AIProviderError(message, code=code, detail=detail_from(e)) from e
        return None
    except Exception as e:
        logger.warning(
            "AI provider %s unexpected error: %s",
            sanitize_log_value(provider),
            sanitize_log_value(str(e)),
            exc_info=True,
        )
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

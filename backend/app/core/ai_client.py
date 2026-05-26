"""Shared AI client for structured-JSON completions across providers.

Wraps anthropic, openai, openrouter, and gemini behind a single
`complete_json` entry point. Each provider adapter handles its own
authentication and structured-JSON convention (prompt-only for anthropic,
response_format for openai/openrouter, responseSchema for gemini).
"""

import json
import logging

import httpx

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


async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict | None:
    """Send a prompt to an LLM provider and return its JSON response as a dict.

    Returns None on any failure (network, HTTP, malformed JSON). Callers must
    treat None as "no usable result" and fall back to other behaviour.
    """
    if not api_key:
        logger.debug("complete_json called with empty api_key; returning None")
        return None

    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        logger.warning("Unknown AI provider: %s", provider)
        return None

    try:
        if provider == "anthropic":
            return await _call_anthropic(prompt, api_key, model, max_tokens)
        # additional providers wired in later tasks
        logger.warning("Unsupported AI provider: %s", provider)
        return None
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e, exc_info=True)
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        return None


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

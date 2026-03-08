"""AI-powered disc title resolution.

When TMDB lookup fails (obscure titles, non-English discs, abbreviations),
this module sends the volume label to an LLM API to identify the title,
then re-queries TMDB with the corrected name.

Supports Anthropic (Claude) and OpenAI as providers.
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

# Provider API endpoints
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# Models to use per provider
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
OPENAI_MODEL = "gpt-4o-mini"

IDENTIFICATION_PROMPT = """You are a media identification assistant. Given a disc volume label from a Blu-ray or DVD, identify the movie or TV show it contains.

Volume label: {volume_label}

Respond with ONLY a JSON object (no markdown, no explanation) in this exact format:
{{"title": "Official Title", "year": 2020, "type": "movie" or "tv"}}

Rules:
- "title" must be the official English title as it appears on TMDB/IMDb
- "year" is the original release year (integer)
- "type" is either "movie" or "tv"
- If you cannot identify the disc, respond with: {{"title": null, "year": null, "type": null}}
- Do NOT guess — only identify if you are confident"""


async def identify_from_label(
    volume_label: str,
    provider: str,
    api_key: str,
) -> dict | None:
    """Send volume label to an LLM to identify the disc content.

    Returns dict with keys: title, year, type (or None on failure).
    """
    prompt = IDENTIFICATION_PROMPT.format(volume_label=volume_label)

    try:
        if provider == "anthropic":
            result = await _call_anthropic(prompt, api_key)
        elif provider == "openai":
            result = await _call_openai(prompt, api_key)
        else:
            logger.warning(f"Unknown AI provider: {provider}")
            return None

        if not result:
            return None

        parsed = _parse_response(result)
        if parsed and parsed.get("title"):
            logger.info(
                f"AI identified '{volume_label}' as: "
                f"{parsed['title']} ({parsed.get('year')}) [{parsed.get('type')}]"
            )
            return parsed
        return None

    except Exception as e:
        logger.warning(f"AI identification failed for '{volume_label}': {e}")
        return None


async def _call_anthropic(prompt: str, api_key: str) -> str | None:
    """Call the Anthropic Messages API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("content") and len(data["content"]) > 0:
            return data["content"][0].get("text", "")
        return None


async def _call_openai(prompt: str, api_key: str) -> str | None:
    """Call the OpenAI Chat Completions API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            OPENAI_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0,
            },
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return None


def _parse_response(text: str) -> dict | None:
    """Parse the LLM response JSON."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse AI response as JSON: {text[:200]}")
        return None

    # Validate expected fields
    if not isinstance(data, dict):
        return None

    title = data.get("title")
    if not title:
        return None

    return {
        "title": str(title),
        "year": int(data["year"]) if data.get("year") else None,
        "type": data.get("type"),
    }

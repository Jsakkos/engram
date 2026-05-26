"""AI-powered disc title resolution.

Delegates to the shared `app.core.ai_client.complete_json` for transport
and JSON parsing. This module owns only the disc-title prompt and
response-shape validation.
"""

import logging

from app.core.ai_client import _parse_json_text, complete_json

logger = logging.getLogger(__name__)

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
    raw = await complete_json(
        prompt=prompt,
        schema=None,
        provider=provider,
        api_key=api_key,
        max_tokens=200,
    )
    return _validate(raw, volume_label)


def _validate(raw: dict | None, volume_label: str) -> dict | None:
    """Validate and normalize the response dict."""
    if not raw:
        return None
    title = raw.get("title")
    if not title:
        return None

    year_raw = raw.get("year")
    try:
        year = int(year_raw) if year_raw is not None else None
    except (TypeError, ValueError):
        year = None

    parsed = {
        "title": str(title),
        "year": year,
        "type": raw.get("type"),
    }
    logger.info(
        "AI identified '%s' as: %s (%s) [%s]",
        volume_label,
        parsed["title"],
        parsed["year"],
        parsed["type"],
    )
    return parsed


# Keep _parse_response as a backwards-compatible shim for existing tests.
def _parse_response(text: str) -> dict | None:
    """Test-shim — preserves the v1 contract for unit tests."""
    parsed = _parse_json_text(text)
    return _validate(parsed, "test")

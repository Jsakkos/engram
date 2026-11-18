"""LLM-based episode identification fallback.

When Engram's primary audio-fingerprint matcher returns a low-confidence
match, this module fetches the candidate season's TMDB synopses, sends
them along with the ripped episode's cleaned Whisper transcript to the
configured AI provider, and returns the LLM's suggested episode.

Always treats the result as a *suggestion* — the caller must route it
through the review queue, never auto-organize.
"""

import logging
from dataclasses import dataclass

from app.core.ai_client import (
    DEFAULT_MODELS,
    LOCAL_COMPLETION_MAX_TOKENS,
    LOCAL_PROVIDERS,
    complete_json,
)
from app.core.episode_namespace import season_episodes
from app.core.security import sanitize_log_value
from app.matcher.episode_identification import _clean_subtitle_text

logger = logging.getLogger(__name__)

MIN_TRANSCRIPT_CHARS = 500  # silent/corrupt audio yields too little signal for synopsis matching


@dataclass
class RunnerUp:
    """Second-best episode guess from the LLM. Typed (not a bare dict) so the
    field names stay locked between the JSON schema and the Python object."""

    episode: int
    confidence: float


PROMPT_TEMPLATE = """You are identifying which episode of "{show_name}" Season {season} this is, given the episode's full dialogue transcript.

Candidate episodes (within this season):
{candidates_block}

Episode transcript (cleaned, lowercase):
\"\"\"
{transcript}
\"\"\"

Rules:
- Weight plot-specific events (named characters, unique locations, distinctive plot beats) over generic dialogue, action sounds, or recurring phrases.
- If the transcript does NOT match any candidate (e.g. wrong show/season), respond with `confidence: 0`.
- `runner_up` is your second-best guess; null if no plausible alternative.

Respond with ONLY a JSON object in this exact format:
{{"episode": <int>, "confidence": <float 0..1>, "reasoning": "<one sentence>", "runner_up": {{"episode": <int>, "confidence": <float>}} or null}}
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "episode": {"type": "integer"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "runner_up": {
            "type": ["object", "null"],
            "properties": {
                "episode": {"type": "integer"},
                "confidence": {"type": "number"},
            },
        },
    },
    "required": ["episode", "confidence"],
}


@dataclass
class LLMEpisodeMatch:
    episode: int
    confidence: float
    reasoning: str
    runner_up: RunnerUp | None
    model: str


def _valid_candidate(raw: object, episode_numbers: set[int]) -> bool:
    """Validate provider output even when structured generation is unavailable."""
    if not isinstance(raw, dict):
        return False
    episode = raw.get("episode")
    confidence = raw.get("confidence")
    return (
        type(episode) is int
        and episode in episode_numbers
        and isinstance(confidence, int | float)
        and not isinstance(confidence, bool)
        # This also rejects NaN and infinities without coercing strings or bools.
        and 0 <= confidence <= 1
    )


async def match_episode_via_llm(
    *,
    transcript: str,
    show_name: str,
    season: int,
    tmdb_show_id: str,
    ai_provider: str,
    ai_api_key: str,
    tmdb_api_key: str,
    ai_model: str | None = None,
    ai_local_base_url: str = "",
    raise_on_error: bool = False,
) -> LLMEpisodeMatch | None:
    """Run LLM episode matching. Returns None on no-confident-match or empty response.

    ``ai_model`` is the user's optional override (AppConfig.ai_model); None means
    the provider default. It is recorded on the returned match so per-track
    provenance names the model that actually answered.

    ``ai_local_base_url`` is the user's local-server endpoint
    (AppConfig.ai_local_base_url). It applies only to the local providers; blank
    means the slug's default port.

    When ``raise_on_error`` is True, a provider/transport failure raises
    ``AIProviderError`` (instead of being swallowed to None by ``complete_json``)
    so callers can distinguish a provider outage from "no confident match".
    """
    cleaned = _clean_subtitle_text(transcript)
    safe_show = sanitize_log_value(show_name)
    safe_season = sanitize_log_value(season)
    if len(cleaned) < MIN_TRANSCRIPT_CHARS:
        logger.info(
            "LLM matcher: transcript too short (%d chars) for %s S%s",
            len(cleaned),
            safe_show,
            safe_season,
        )
        return None

    # The roster in the bound namespace, so the episode number the LLM picks is
    # in the job's own numbering (TheTVDB for a tvdb job).
    episodes = season_episodes(tmdb_show_id, season, tmdb_api_key)
    if not episodes:
        logger.warning(
            "LLM matcher: no episode synopses for show_id=%s season=%s",
            sanitize_log_value(tmdb_show_id),
            safe_season,
        )
        return None

    candidates_block = "\n".join(
        f'- Episode {ep["episode_number"]}: "{ep.get("name", "")}" — {ep.get("overview", "") or "(no synopsis)"}'
        for ep in episodes
    )
    prompt = PROMPT_TEMPLATE.format(
        show_name=show_name,
        season=season,
        candidates_block=candidates_block,
        transcript=cleaned,
    )

    raw = await complete_json(
        prompt=prompt,
        schema=RESPONSE_SCHEMA,
        provider=ai_provider,
        api_key=ai_api_key,
        model=ai_model or None,
        # Local reasoning models need output tokens for thinking before the final JSON.
        max_tokens=LOCAL_COMPLETION_MAX_TOKENS if ai_provider in LOCAL_PROVIDERS else 512,
        base_url=ai_local_base_url,
        raise_on_error=raise_on_error,
    )
    if not raw:
        return None

    episode_numbers = {ep["episode_number"] for ep in episodes}
    if not _valid_candidate(raw, episode_numbers):
        logger.warning("LLM matcher: response has an invalid episode or confidence")
        return None
    episode = raw["episode"]
    confidence = float(raw["confidence"])

    if confidence <= 0.0:
        logger.info(
            "LLM matcher: confidence==0 (wrong show/season signal) for %s S%s",
            safe_show,
            safe_season,
        )
        return None

    runner_up_raw = raw.get("runner_up")
    runner_up: RunnerUp | None = None
    if _valid_candidate(runner_up_raw, episode_numbers) and runner_up_raw["episode"] != episode:
        runner_up = RunnerUp(
            episode=runner_up_raw["episode"],
            confidence=float(runner_up_raw["confidence"]),
        )

    return LLMEpisodeMatch(
        episode=episode,
        confidence=confidence,
        reasoning=str(raw.get("reasoning") or ""),
        runner_up=runner_up,
        model=ai_model or DEFAULT_MODELS.get(ai_provider, "unknown"),
    )

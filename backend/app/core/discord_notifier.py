"""Discord webhook notifications for job completion events."""

import chevron
import httpx
from chevron.tokenizer import ChevronError, tokenize
from loguru import logger

from app.models.disc_job import DiscJob

ALLOWED_TEMPLATE_VARS = frozenset(
    {
        "title",
        "drive",
        "job_id",
        "content_type",
        "season",
        "tmdb_name",
        "tmdb_year",
        "duration",
        "error",
        "subtitle_status",
        "subtitles_downloaded",
        "subtitles_total",
        "subtitles_failed",
        "path",
        "total_titles",
    }
)

DEFAULT_TEMPLATE_COMPLETED = "**{{title}}**"
DEFAULT_TEMPLATE_FAILED = "**{{title}}**"


def validate_discord_template(template: str) -> str | None:
    """Validate a Discord notification template. Returns an error message, or None if valid."""
    try:
        tags = list(tokenize(template))
    except ChevronError as e:
        return str(e)

    unknown = sorted(
        {
            key.strip()
            for tag_type, key in tags
            if tag_type != "literal" and key.strip() not in ALLOWED_TEMPLATE_VARS
        }
    )
    if unknown:
        return f"Unknown template variable(s): {', '.join(unknown)}"
    return None


def _format_duration(job: DiscJob) -> str:
    if job.created_at is None or job.completed_at is None:
        return ""
    seconds = int((job.completed_at - job.created_at).total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def build_template_context(job: DiscJob | None, job_id: int) -> dict[str, str]:
    """Build the chevron render context from a DiscJob row."""
    if job is None:
        return {"title": f"Job #{job_id}"} | dict.fromkeys(ALLOWED_TEMPLATE_VARS - {"title"}, "")

    title = job.detected_title or job.volume_label or f"Job #{job_id}"
    return {
        "title": title,
        "drive": job.volume_label or "",
        "job_id": str(job.id or job_id),
        "content_type": job.content_type.value.replace("_", " ").title(),
        "season": str(job.detected_season) if job.detected_season is not None else "",
        "tmdb_name": job.tmdb_name or "",
        "tmdb_year": str(job.tmdb_year) if job.tmdb_year is not None else "",
        "duration": _format_duration(job),
        "error": job.error_message or "",
        "subtitle_status": job.subtitle_status or "",
        "subtitles_downloaded": str(job.subtitles_downloaded),
        "subtitles_total": str(job.subtitles_total),
        "subtitles_failed": str(job.subtitles_failed),
        "path": job.final_path or "",
        "total_titles": str(job.total_titles),
    }


def render_discord_template(template: str, context: dict) -> str:
    return chevron.render(template, context)


async def notify_discord(webhook_url: str, job_id: int, description: str, state: str) -> None:
    """POST a Discord embed to webhook_url. No-op if URL is empty."""
    if not webhook_url:
        return

    color = 0x00B97A if state == "completed" else 0xE53935  # green / red
    emoji = "✅" if state == "completed" else "❌"

    payload = {
        "embeds": [
            {
                "title": f"{emoji} Disc {state.title()}",
                "description": description,
                "color": color,
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.debug(f"Discord notification sent for job {job_id} ({state})")
    except Exception:
        logger.warning(f"Discord notification failed for job {job_id}", exc_info=True)

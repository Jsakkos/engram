"""Discord webhook notifications for job lifecycle events."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import chevron
import httpx
from chevron.tokenizer import ChevronError, tokenize
from loguru import logger

from app import __version__
from app.core.security import is_safe_dashboard_url
from app.models.disc_job import ContentType, DiscJob, JobState, TitleState


@dataclass(frozen=True)
class NotificationEvent:
    """Presentation for one notifiable job state.

    Replaces the old `state == "completed"` binary. `key` is the config suffix
    (discord_template_<key>, discord_notify_<key>), deliberately "review" rather
    than "review_needed" so config field names stay readable.
    """

    key: str
    label: str
    emoji: str
    color: int


EVENTS: dict[JobState, NotificationEvent] = {
    JobState.COMPLETED: NotificationEvent("completed", "Disc Completed", "✅", 0x00B97A),
    JobState.FAILED: NotificationEvent("failed", "Disc Failed", "❌", 0xE53935),
    JobState.REVIEW_NEEDED: NotificationEvent("review", "Review Needed", "🔍", 0xF59E0B),
}

# Deliberately NOT in EVENTS: "the disc is copied and out of the drive" is a
# hardware milestone, not a JobState, and it must fire for a disc that goes on
# to park in REVIEW_NEEDED. Delivered from JobManager._release_drive rather than
# from a state-machine callback.
RIPPED_EVENT = NotificationEvent("ripped", "Disc Ripped", "💿", 0x3B82F6)

ALLOWED_TEMPLATE_VARS = frozenset(
    {
        "title",
        # Misnamed: renders volume_label, not the drive letter. Kept for
        # back-compat with saved templates; {{volume_label}} is the correct name
        # and {{drive_id}} is the actual drive.
        "drive",
        "volume_label",
        "drive_id",
        "disc_number",
        "discdb_disc_slug",
        "job_id",
        "content_type",
        "season",
        "tmdb_name",
        "tmdb_year",
        "duration",
        "error",
        "review_reason",
        "episodes",
        "state",
        "subtitle_status",
        "subtitles_downloaded",
        "subtitles_total",
        "subtitles_failed",
        "path",
        "total_titles",
        "rip_outcome",
    }
)

DEFAULT_TEMPLATE_COMPLETED = "**{{{title}}}**"
DEFAULT_TEMPLATE_FAILED = "**{{{title}}}**"
DEFAULT_TEMPLATE_REVIEW = "**{{{title}}}**"
DEFAULT_TEMPLATE_RIPPED = "**{{{title}}}**"

DEFAULT_TEMPLATES = {
    "completed": DEFAULT_TEMPLATE_COMPLETED,
    "failed": DEFAULT_TEMPLATE_FAILED,
    "review": DEFAULT_TEMPLATE_REVIEW,
    "ripped": DEFAULT_TEMPLATE_RIPPED,
}


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


def build_template_context(
    job: DiscJob | None, job_id: int, titles: list | None = None
) -> dict[str, str]:
    """Build the chevron render context from a DiscJob row.

    ``titles`` is optional so every existing two-argument caller keeps working;
    without it, {{episodes}} renders empty.
    """
    if job is None:
        return {"title": f"Job #{job_id}"} | dict.fromkeys(ALLOWED_TEMPLATE_VARS - {"title"}, "")

    title = job.detected_title or job.volume_label or f"Job #{job_id}"
    return {
        "title": title,
        "drive": job.volume_label or "",
        "volume_label": job.volume_label or "",
        "drive_id": job.drive_id or "",
        "disc_number": str(job.disc_number) if job.disc_number is not None else "",
        "discdb_disc_slug": job.discdb_disc_slug or "",
        "job_id": str(job.id or job_id),
        "content_type": job.content_type.value.replace("_", " ").title(),
        "season": str(job.detected_season) if job.detected_season is not None else "",
        "tmdb_name": job.tmdb_name or "",
        "tmdb_year": str(job.tmdb_year) if job.tmdb_year is not None else "",
        "duration": _format_duration(job),
        "error": job.error_message or "",
        "review_reason": job.review_reason or "",
        "episodes": summarize_episodes(titles or []),
        "state": job.state.value,
        "subtitle_status": job.subtitle_status or "",
        "subtitles_downloaded": str(job.subtitles_downloaded),
        "subtitles_total": str(job.subtitles_total),
        "subtitles_failed": str(job.subtitles_failed),
        "path": job.final_path or "",
        "total_titles": str(job.total_titles),
        # Filled by the caller via extra_context, not derived from the row:
        # whether this was a clean rip, a mid-rip abort or a re-rip is known
        # only at the eject site.
        "rip_outcome": "",
    }


def render_discord_template(template: str, context: dict) -> str:
    return chevron.render(template, context, partials_dict={})


def format_disc_identity(job: DiscJob) -> str:
    """Human-readable identity for THIS disc, not the show it belongs to.

    A box set's detected_title is identical on every disc, which is exactly the
    problem this solves. Falls back volume_label, then discdb_disc_slug, then
    a bare disc number.
    """
    disc_number = job.disc_number or 1
    if job.volume_label:
        if disc_number > 1:
            return f"{job.volume_label} (Disc {disc_number})"
        return job.volume_label
    if job.discdb_disc_slug:
        return job.discdb_disc_slug
    return f"Disc {disc_number}"


# Discord's documented embed limits. Exceeding one makes the API reject the
# whole message with a 400, which notify_discord swallows, so a too-long
# error_message would silently cost the user the notification entirely.
_MAX_FIELD_VALUE = 1024
_MAX_DESCRIPTION = 4096


def _truncate(value: str, limit: int) -> str:
    """Clip to Discord's limit, marking the cut so nobody reads a partial value as complete."""
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _field(name: str, value: str, inline: bool = True) -> dict | None:
    """Build one embed field, or None when the value is empty.

    Emptiness is the universal drop signal: it lets a single field list serve
    movies, TV, imports and all three events without conditional branching.
    """
    return (
        {"name": name, "value": _truncate(value, _MAX_FIELD_VALUE), "inline": inline}
        if value
        else None
    )


_EPISODE_CODE = re.compile(r"^S(\d+)E(\d+)$", re.IGNORECASE)


def summarize_episodes(titles: list) -> str:
    """One-line manifest of what actually landed, e.g. "S01E01-E04 (4 episodes)".

    Counts every completed, non-extra title, but only ranges the ones whose
    matched_episode parses as SxxEyy. Ranges never span a season boundary.
    """
    landed = [t for t in titles if t.state == TitleState.COMPLETED and not t.is_extra]
    if not landed:
        return ""

    parsed: list[tuple[int, int]] = []
    for title in landed:
        match = _EPISODE_CODE.match((title.matched_episode or "").strip())
        if match:
            parsed.append((int(match.group(1)), int(match.group(2))))

    count = len(landed)
    noun = "episode" if count == 1 else "episodes"
    if not parsed:
        return ""

    parsed = sorted(set(parsed))
    runs: list[list[tuple[int, int]]] = [[parsed[0]]]
    for season, episode in parsed[1:]:
        prev_season, prev_episode = runs[-1][-1]
        if season == prev_season and episode == prev_episode + 1:
            runs[-1].append((season, episode))
        else:
            runs.append([(season, episode)])

    parts = []
    for run in runs:
        start_season, start_episode = run[0]
        start = f"S{start_season:02d}E{start_episode:02d}"
        if len(run) == 1:
            parts.append(start)
        else:
            parts.append(f"{start}-E{run[-1][1]:02d}")

    return f"{', '.join(parts)} ({count} {noun})"


def build_embed_fields(
    job: DiscJob | None, titles: list, event: NotificationEvent, *, rip_outcome: str = ""
) -> list[dict]:
    """Structured embed fields for one notification. Empty values are dropped."""
    if job is None:
        return []

    is_tv = job.content_type == ContentType.TV
    reason = job.review_reason if event.key == "review" else job.error_message

    subtitles = ""
    if job.subtitle_status:
        subtitles = f"{job.subtitles_downloaded}/{job.subtitles_total}"
        if job.subtitles_failed:
            subtitles += f", {job.subtitles_failed} failed"

    candidates = [
        _field("Status", rip_outcome if event.key == "ripped" else ""),
        _field("Disc", format_disc_identity(job)),
        _field(
            "Season",
            f"Season {job.detected_season}" if is_tv and job.detected_season is not None else "",
        ),
        _field(
            "Episodes",
            summarize_episodes(titles) if event.key == "completed" else "",
            inline=False,
        ),
        _field("Duration", _format_duration(job)),
        _field("Tracks", f"{job.total_titles} titles" if job.total_titles else ""),
        _field("Subtitles", subtitles),
        _field("Reason", reason or "", inline=False),
        _field("Library", (job.final_path or "") if event.key == "completed" else "", inline=False),
    ]
    return [f for f in candidates if f is not None]


def build_dashboard_link(base_url: str, job_id: int) -> str | None:
    """Deep link to this job's detail page, or None when no base URL is configured.

    Re-validates the stored value rather than trusting it: config can be written
    by an older build or edited in the DB directly, and an unvalidated string
    here becomes a clickable link in a chat channel. A rejected value is logged
    rather than silently dropped, so "why is there no link" is diagnosable.

    Any query string or fragment on the configured base is discarded. Appending
    a path to ``http://host/?x=1`` would bury ``/history/42`` inside the query
    and yield a plausible-looking but broken link.
    """
    if not base_url:
        return None
    if not is_safe_dashboard_url(base_url):
        logger.warning(f"Ignoring unsafe dashboard_base_url in config: {base_url!r}")
        return None

    parsed = urlsplit(base_url)
    origin_and_path = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return f"{origin_and_path.rstrip('/')}/history/{job_id}"


def build_embed(
    job: DiscJob | None,
    titles: list,
    event: NotificationEvent,
    description: str,
    *,
    poster_url: str | None = None,
    link_url: str | None = None,
    rip_outcome: str = "",
) -> dict:
    """Assemble the Discord embed for one notification.

    The user template renders into `description`; everything else is structured
    so disc identity shows up without anyone editing a template.
    """
    embed: dict = {
        "title": f"{event.emoji} {event.label}",
        "description": _truncate(description, _MAX_DESCRIPTION),
        "color": event.color,
        "timestamp": datetime.now(UTC).isoformat(),
        "footer": {"text": f"Engram v{__version__}"},
        "fields": build_embed_fields(job, titles, event, rip_outcome=rip_outcome),
    }
    if link_url:
        embed["url"] = link_url
    if poster_url:
        embed["thumbnail"] = {"url": poster_url}
    return embed


async def notify_discord(
    webhook_url: str,
    job_id: int,
    embed: dict,
    content: str = "",
    raise_on_error: bool = False,
) -> None:
    """POST a prebuilt embed to webhook_url. No-op if URL is empty.

    Pure transport: it knows nothing about job semantics. `content` is the only
    part Discord resolves mentions in, so it carries the configured mention and
    never rendered template output.

    Swallowing every error is the default and is load-bearing: this runs as a
    fire-and-forget task on the pipeline path, where an unreachable webhook must
    never affect a rip. ``raise_on_error=True`` opts into the opposite contract
    for the one caller that needs a real delivery verdict, the "send test
    message" endpoint: reporting success there when Discord rejected the post
    would defeat the entire point of the button.
    """
    if not webhook_url:
        return

    payload: dict = {"embeds": [embed]}
    if content:
        payload["content"] = content

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.debug(f"Discord notification sent for job {job_id}")
    except Exception:
        logger.warning(f"Discord notification failed for job {job_id}", exc_info=True)
        if raise_on_error:
            raise

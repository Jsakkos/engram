"""Tests for Discord webhook notifications."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.discord_notifier import (
    DEFAULT_TEMPLATE_COMPLETED,
    build_template_context,
    notify_discord,
    render_discord_template,
    validate_discord_template,
)
from app.models.disc_job import ContentType, DiscJob

# --------------------------------------------------------------------------- #
# Event table
# --------------------------------------------------------------------------- #


def test_event_table_covers_the_three_notifiable_states():
    from app.core.discord_notifier import EVENTS
    from app.models.disc_job import JobState

    assert set(EVENTS) == {JobState.COMPLETED, JobState.FAILED, JobState.REVIEW_NEEDED}
    assert EVENTS[JobState.COMPLETED].key == "completed"
    assert EVENTS[JobState.COMPLETED].color == 0x00B97A
    assert EVENTS[JobState.FAILED].key == "failed"
    assert EVENTS[JobState.FAILED].color == 0xE53935
    assert EVENTS[JobState.REVIEW_NEEDED].key == "review"
    assert EVENTS[JobState.REVIEW_NEEDED].label == "Review Needed"


def test_event_table_has_no_entry_for_transient_states():
    """RIPPING and friends must not resolve to an event; that guard is what stops
    a mid-pipeline transition from posting to Discord."""
    from app.core.discord_notifier import EVENTS
    from app.models.disc_job import JobState

    assert EVENTS.get(JobState.RIPPING) is None
    assert EVENTS.get(JobState.MATCHING) is None


# --------------------------------------------------------------------------- #
# notify_discord unit tests
# --------------------------------------------------------------------------- #


def _mock_http_client():
    """AsyncClient double whose .post records the call and reports success."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_notify_discord_noop_on_empty_url():
    """Empty webhook URL means no HTTP call is made."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        await notify_discord("", job_id=1, embed={"title": "x"})
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_discord_posts_the_embed_verbatim():
    """notify_discord is pure transport: it wraps the embed and posts, nothing else."""
    mock_client = _mock_http_client()
    embed = {"title": "✅ Disc Completed", "description": "**The Wire**", "color": 0x00B97A}

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord("https://discord.com/api/webhooks/123/abc", job_id=5, embed=embed)

    url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/123/abc"
    assert kwargs["json"]["embeds"] == [embed]


@pytest.mark.asyncio
async def test_notify_discord_omits_content_key_when_blank():
    """A blank content must not be sent as an empty message body."""
    mock_client = _mock_http_client()

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord("https://discord.com/api/webhooks/1/a", job_id=1, embed={}, content="")

    assert "content" not in mock_client.post.call_args[1]["json"]


@pytest.mark.asyncio
async def test_notify_discord_includes_content_when_set():
    """The mention rides as message content, which is the only part Discord pings on."""
    mock_client = _mock_http_client()

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord(
            "https://discord.com/api/webhooks/1/a", job_id=1, embed={}, content="<@1234>"
        )

    assert mock_client.post.call_args[1]["json"]["content"] == "<@1234>"


@pytest.mark.asyncio
async def test_notify_discord_swallows_http_errors():
    """HTTP errors are caught and logged, never raised."""
    import httpx

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord("https://discord.com/api/webhooks/123/abc", job_id=3, embed={})


# --------------------------------------------------------------------------- #
# build_embed
# --------------------------------------------------------------------------- #


def test_build_embed_sets_title_description_and_color_from_event():
    from app.core.discord_notifier import EVENTS, build_embed
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", content_type=ContentType.TV, detected_title="The Wire")
    embed = build_embed(job, [], EVENTS[JobState.COMPLETED], "**The Wire**")

    assert embed["title"] == "✅ Disc Completed"
    assert embed["description"] == "**The Wire**"
    assert embed["color"] == 0x00B97A
    assert "timestamp" in embed
    assert embed["footer"]["text"].startswith("Engram")


# --------------------------------------------------------------------------- #
# validate_discord_template
# --------------------------------------------------------------------------- #


def test_validate_discord_template_accepts_valid_template():
    assert validate_discord_template("{{title}} - {{duration}}") is None


def test_validate_discord_template_accepts_empty_string():
    """Empty template is valid — falls back to the built-in default at render time."""
    assert validate_discord_template("") is None


def test_validate_discord_template_rejects_unknown_variable():
    error = validate_discord_template("{{bogus}}")
    assert error is not None
    assert "bogus" in error


def test_validate_discord_template_rejects_malformed_syntax():
    """Malformed mustache syntax is rejected; the exact wording is chevron's own
    (we pass its exception through verbatim), so we only assert rejection, not
    the message text — pinning that would test chevron, not our code."""
    error = validate_discord_template("{{title")
    assert error is not None


def test_validate_discord_template_rejects_unknown_variable_in_section_tag():
    error = validate_discord_template("{{#bogus}}x{{/bogus}}")
    assert error is not None
    assert "bogus" in error


def test_validate_discord_template_accepts_comment_tag():
    """Mustache comments ({{! ... }}) are inert and shouldn't be flagged as unknown
    variables. Chevron's tokenizer already filters comment tags out internally —
    this pins that behavior against a future chevron version regressing it."""
    assert validate_discord_template("{{title}} {{! a comment }}") is None


# --------------------------------------------------------------------------- #
# build_template_context / render_discord_template
# --------------------------------------------------------------------------- #


def test_build_template_context_default_template_matches_current_output():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.TV,
        detected_title="Breaking Bad",
        volume_label="BREAKING_BAD_S1D1",
    )
    context = build_template_context(job, job_id=1)
    assert context["title"] == "Breaking Bad"


def test_build_template_context_error_populated_on_failed_job():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        volume_label="BAD_DISC",
        error_message="disc unreadable",
    )
    context = build_template_context(job, job_id=2)
    assert context["error"] == "disc unreadable"


def test_build_template_context_error_empty_on_completed_job():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        detected_title="Inception",
        volume_label="INCEPTION_2010",
    )
    context = build_template_context(job, job_id=3)
    assert context["error"] == ""


def test_build_template_context_duration_formatted_when_both_timestamps_set():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        detected_title="Inception",
        volume_label="INCEPTION_2010",
        created_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 11, 12, tzinfo=UTC),
    )
    context = build_template_context(job, job_id=4)
    assert context["duration"] == "1h 12m"


def test_build_template_context_duration_empty_when_not_completed():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        detected_title="Inception",
        volume_label="INCEPTION_2010",
        completed_at=None,
    )
    context = build_template_context(job, job_id=5)
    assert context["duration"] == ""


def test_build_template_context_falls_back_when_job_is_none():
    """Job vanished before re-fetch — context still yields a usable title, no crash."""
    context = build_template_context(None, job_id=42)
    assert context["title"] == "Job #42"


def test_render_discord_template_default_does_not_html_escape_title():
    """The built-in default must reproduce the old raw-f-string output byte-for-byte,
    including titles with mustache-escaped characters — regression test for the
    DEFAULT_TEMPLATE_* switch to unescaped {{{title}}} syntax."""
    rendered = render_discord_template(DEFAULT_TEMPLATE_COMPLETED, {"title": "Law & Order"})
    assert rendered == "**Law & Order**"


def test_render_discord_template_does_not_resolve_partial_tags_from_filesystem():
    """{{>title}} tokenizes as a partial (not a variable) and passes validation since
    'title' is an allowed variable name too. Without partials_dict={}, chevron would
    try to load ./title.mustache from the backend's CWD at render time."""
    rendered = render_discord_template("{{>title}}", {"title": "Inception"})
    assert rendered == ""


# --------------------------------------------------------------------------- #
# _send_discord_notification — notification logic tests
# (call the worker directly; _notify_discord_on_terminal only schedules the task)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_notification_noop_when_no_webhook():
    """No webhook URL configured → notify_discord never called."""
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="")

    with patch("app.core.discord_notifier.notify_discord") as mock_notify:
        await job_manager._send_discord_notification(99, JobState.COMPLETED)
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_send_notification_noop_for_non_notifiable_state():
    """Called with a state that has no event mapping (RIPPING) means notify_discord
    is never called."""
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(99, JobState.RIPPING)
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_send_notification_fires_on_completed():
    """COMPLETED with webhook URL → notify_discord called with job label."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.TV,
            detected_title="Breaking Bad",
            volume_label="BREAKING_BAD_S1D1",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(job_id, JobState.COMPLETED)

    mock_notify.assert_called_once()
    embed = mock_notify.call_args[0][2]
    assert embed["description"] == "**Breaking Bad**"
    assert "Completed" in embed["title"]


@pytest.mark.asyncio
async def test_send_notification_fires_on_failed():
    """FAILED with webhook URL → notify_discord called with 'failed' state."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.MOVIE,
            volume_label="INCEPTION_2010",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(job_id, JobState.FAILED)

    mock_notify.assert_called_once()
    embed = mock_notify.call_args[0][2]
    assert "Failed" in embed["title"]
    assert embed["color"] == 0xE53935


@pytest.mark.asyncio
async def test_send_notification_falls_back_to_volume_label():
    """When detected_title is empty, volume_label is used as the notification label."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.MOVIE,
            detected_title=None,
            volume_label="UNKNOWN_DISC",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(job_id, JobState.COMPLETED)

    assert mock_notify.call_args[0][2]["description"] == "**UNKNOWN_DISC**"


@pytest.mark.asyncio
async def test_send_notification_uses_configured_completed_template():
    """A custom discord_template_completed renders instead of the default."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_template_completed="Done: {{title}} ({{drive}})",
    )

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.TV,
            detected_title="Breaking Bad",
            volume_label="BREAKING_BAD_S1D1",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(job_id, JobState.COMPLETED)

    assert mock_notify.call_args[0][2]["description"] == "Done: Breaking Bad (BREAKING_BAD_S1D1)"

    await update_config(discord_template_completed="")


@pytest.mark.asyncio
async def test_send_notification_uses_configured_failed_template():
    """A custom discord_template_failed renders and includes the error."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_template_failed="Failed: {{title}}: {{error}}",
    )

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.MOVIE,
            volume_label="BAD_DISC",
            error_message="disc unreadable",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(job_id, JobState.FAILED)

    assert mock_notify.call_args[0][2]["description"] == "Failed: BAD_DISC: disc unreadable"

    await update_config(discord_template_failed="")


@pytest.mark.asyncio
async def test_send_notification_swallows_internal_errors():
    """Errors inside the worker never propagate (best-effort)."""
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    with patch(
        "app.core.discord_notifier.notify_discord",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network dead"),
    ):
        await job_manager._send_discord_notification(999, JobState.COMPLETED)


@pytest.mark.asyncio
async def test_terminal_callback_schedules_task():
    """_notify_discord_on_terminal fires _send_discord_notification as a background task."""
    from app.models import JobState
    from app.services.job_manager import job_manager

    with patch.object(
        job_manager, "_send_discord_notification", new_callable=AsyncMock
    ) as mock_send:
        await job_manager._notify_discord_on_terminal(1, JobState.COMPLETED)
        await asyncio.sleep(0)  # yield to let the task start

    mock_send.assert_called_once_with(1, JobState.COMPLETED)


@pytest.mark.asyncio
async def test_advance_job_via_state_machine_fires_notification():
    """advance_job_via_state_machine ORGANIZING→COMPLETED schedules Discord notification."""
    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.MOVIE,
            detected_title="Inception",
            volume_label="INCEPTION_2010",
            state=JobState.ORGANIZING,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch.object(
        job_manager, "_send_discord_notification", new_callable=AsyncMock
    ) as mock_send:
        new_state = await job_manager.advance_job_via_state_machine(job_id)
        await asyncio.sleep(0)

    assert new_state == "completed"
    mock_send.assert_called_once()
    assert mock_send.call_args[0][1] == JobState.COMPLETED


# --------------------------------------------------------------------------- #
# New notification config fields
# --------------------------------------------------------------------------- #


def test_app_config_notification_defaults():
    """New notification fields default to on, with blank templates/mention/link."""
    from app.models.app_config import AppConfig

    config = AppConfig()
    assert config.discord_template_review == ""
    assert config.discord_notify_completed is True
    assert config.discord_notify_failed is True
    assert config.discord_notify_review is True
    assert config.discord_mention_review == ""
    assert config.dashboard_base_url == ""

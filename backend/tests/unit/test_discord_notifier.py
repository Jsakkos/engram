"""Tests for Discord webhook notifications."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.discord_notifier import (
    build_template_context,
    notify_discord,
    render_discord_template,
    validate_discord_template,
)
from app.models.disc_job import ContentType, DiscJob

# --------------------------------------------------------------------------- #
# notify_discord unit tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_discord_noop_on_empty_url():
    """Empty webhook URL → no HTTP call made."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        await notify_discord("", job_id=1, description="**Show Name**", state="completed")
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_discord_posts_completed_embed():
    """COMPLETED state → green embed with checkmark title, description passed through verbatim."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord(
            "https://discord.com/api/webhooks/123/abc",
            job_id=5,
            description="**The Wire**",
            state="completed",
        )

    mock_client.post.assert_called_once()
    url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/123/abc"
    embed = kwargs["json"]["embeds"][0]
    assert "✅" in embed["title"]
    assert "Completed" in embed["title"]
    assert embed["description"] == "**The Wire**"
    assert embed["color"] == 0x00B97A  # green


@pytest.mark.asyncio
async def test_notify_discord_posts_failed_embed():
    """FAILED state → red embed with X title, description passed through verbatim."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord(
            "https://discord.com/api/webhooks/123/abc",
            job_id=5,
            description="**Mystery Disc**",
            state="failed",
        )

    embed = mock_client.post.call_args[1]["json"]["embeds"][0]
    assert "❌" in embed["title"]
    assert "Failed" in embed["title"]
    assert embed["description"] == "**Mystery Disc**"
    assert embed["color"] == 0xE53935  # red


@pytest.mark.asyncio
async def test_notify_discord_swallows_http_errors():
    """HTTP errors are caught and logged, never raised."""
    import httpx

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Should not raise
        await notify_discord(
            "https://discord.com/api/webhooks/123/abc",
            job_id=3,
            description="**Some Disc**",
            state="completed",
        )


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
    error = validate_discord_template("{{title")
    assert error is not None


def test_validate_discord_template_rejects_unknown_variable_in_section_tag():
    error = validate_discord_template("{{#bogus}}x{{/bogus}}")
    assert error is not None
    assert "bogus" in error


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
    rendered = render_discord_template("**{{title}}**", context)
    assert rendered == "**Breaking Bad**"


def test_build_template_context_error_populated_on_failed_job():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        volume_label="BAD_DISC",
        error_message="disc unreadable",
    )
    context = build_template_context(job, job_id=2)
    rendered = render_discord_template("{{error}}", context)
    assert rendered == "disc unreadable"


def test_build_template_context_error_empty_on_completed_job():
    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        detected_title="Inception",
        volume_label="INCEPTION_2010",
    )
    context = build_template_context(job, job_id=3)
    rendered = render_discord_template("[{{error}}]", context)
    assert rendered == "[]"


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
    rendered = render_discord_template("{{title}}", context)
    assert rendered == "Job #42"


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
    _, description, state = (
        mock_notify.call_args[0][1],
        mock_notify.call_args[0][2],
        mock_notify.call_args[0][3],
    )
    assert description == "**Breaking Bad**"
    assert state == "completed"


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
    state = mock_notify.call_args[0][3]
    assert state == "failed"


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

    description = mock_notify.call_args[0][2]
    assert description == "**UNKNOWN_DISC**"


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

    description = mock_notify.call_args[0][2]
    assert description == "Done: Breaking Bad (BREAKING_BAD_S1D1)"

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
        discord_template_failed="Failed: {{title}} — {{error}}",
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

    description = mock_notify.call_args[0][2]
    assert description == "Failed: BAD_DISC — disc unreadable"

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

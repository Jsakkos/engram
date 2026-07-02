"""Tests for Discord webhook notifications."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.discord_notifier import notify_discord
from app.models.disc_job import ContentType, DiscJob

# --------------------------------------------------------------------------- #
# notify_discord unit tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_discord_noop_on_empty_url():
    """Empty webhook URL → no HTTP call made."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        await notify_discord("", job_id=1, label="Show Name", state="completed")
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_discord_posts_completed_embed():
    """COMPLETED state → green embed with checkmark title."""
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
            label="The Wire",
            state="completed",
        )

    mock_client.post.assert_called_once()
    url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/123/abc"
    embed = kwargs["json"]["embeds"][0]
    assert "✅" in embed["title"]
    assert "Completed" in embed["title"]
    assert "The Wire" in embed["description"]
    assert embed["color"] == 0x00B97A  # green


@pytest.mark.asyncio
async def test_notify_discord_posts_failed_embed():
    """FAILED state → red embed with X title."""
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
            label="Mystery Disc",
            state="failed",
        )

    embed = mock_client.post.call_args[1]["json"]["embeds"][0]
    assert "❌" in embed["title"]
    assert "Failed" in embed["title"]
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
            label="Some Disc",
            state="completed",
        )


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
    _, label, state = (
        mock_notify.call_args[0][1],
        mock_notify.call_args[0][2],
        mock_notify.call_args[0][3],
    )
    assert label == "Breaking Bad"
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

    label = mock_notify.call_args[0][2]
    assert label == "UNKNOWN_DISC"


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

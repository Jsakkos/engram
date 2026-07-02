"""Discord webhook notifications for job completion events."""

import httpx
from loguru import logger


async def notify_discord(webhook_url: str, job_id: int, label: str, state: str) -> None:
    """POST a Discord embed to webhook_url. No-op if URL is empty."""
    if not webhook_url:
        return

    color = 0x00B97A if state == "completed" else 0xE53935  # green / red
    emoji = "✅" if state == "completed" else "❌"

    payload = {
        "embeds": [
            {
                "title": f"{emoji} Disc {state.title()}",
                "description": f"**{label}**",
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

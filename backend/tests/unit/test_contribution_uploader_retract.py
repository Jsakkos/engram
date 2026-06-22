"""Tests for ContributionUploader._sweep_queue draining FingerprintRetraction rows."""

import asyncio

import httpx
import pytest

from app.database import async_session, init_db
from app.models.fingerprint import FingerprintRetraction
from app.services.config_service import update_config
from app.services.contribution_uploader import ContributionUploader


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import text as _t

        await session.execute(_t("DELETE FROM fingerprint_retractions"))
        await session.commit()
    # Enable both consent gates so _sweep_queue doesn't short-circuit.
    await update_config(
        enable_fingerprint_contributions=True,
        fingerprint_disclosure_accepted=True,
    )


async def test_retraction_row_posts_to_v1_retract_and_marks_success(monkeypatch):
    async with async_session() as session:
        session.add(
            FingerprintRetraction(
                pseudonym="00000000-0000-4000-8000-000000000000",
                tmdb_id=1396,
                season=3,
                episode=10,
                fingerprint_sha256=b"\x07" * 32,
            )
        )
        await session.commit()

    seen = {}

    async def fake_post(self, url, json=None, **kw):
        seen["url"] = url
        seen["json"] = json
        return httpx.Response(
            200,
            json={"deleted": 1, "canonical": "requeued"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    uploader = ContributionUploader()
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(1)
        drained, _ = await uploader._sweep_queue(
            FingerprintRetraction, uploader._upload_retraction_row, client, sem
        )
    assert drained == 1
    assert seen["url"].endswith("/v1/retract")
    assert seen["json"]["episode"] == 10
    assert "fingerprint_sha256_b64" in seen["json"]

"""Phase 2 fingerprint contribution uploader.

Drains the local FingerprintContribution queue by POST-ing to a remote
fingerprint network server. Phase 1 built the queue; this service drains it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger
from sqlmodel import select

from app.database import async_session
from app.models.fingerprint import FingerprintContribution
from app.services.config_service import get_config

CONTRIBUTION_LOG_PATH = Path("~/.engram/cache/contribution_log.jsonl").expanduser()

_BATCH_SIZE = 50
_MAX_ATTEMPTS = 5
_UPLOAD_TIMEOUT = 30.0


class ContributionUploader:
    """Background service: drain FingerprintContribution queue over HTTPS."""

    def __init__(self, poll_interval_seconds: int = 3600) -> None:
        self.poll_interval = poll_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._upload_loop(), name="contribution_uploader")
        logger.info("ContributionUploader started (poll interval: {}s)", self.poll_interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected — we just cancelled the task ourselves.
        logger.info("ContributionUploader stopped")

    async def _upload_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                await self._process_batch()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ContributionUploader loop error — will retry next interval")

    async def _process_batch(self) -> None:
        """Fetch up to _BATCH_SIZE pending rows and upload each one."""
        cfg = await get_config()
        if not cfg.fingerprint_server_url:
            logger.debug("fingerprint_server_url not configured; skipping upload batch")
            return

        async with async_session() as session:
            stmt = (
                select(FingerprintContribution)
                .where(FingerprintContribution.upload_status.is_(None))
                .where(FingerprintContribution.upload_attempts < _MAX_ATTEMPTS)
                .limit(_BATCH_SIZE)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                await self._upload_one(row, session, server_url=cfg.fingerprint_server_url)

    async def _upload_one(
        self,
        contrib: FingerprintContribution,
        session,
        server_url: str,
    ) -> None:
        from app.matcher.chromaprint_extractor import ChromaprintResult

        try:
            fp = ChromaprintResult.from_blob(contrib.chromaprint_blob)
            payload = {
                "pseudonym": contrib.pseudonym,
                "tmdb_id": contrib.tmdb_id,
                "season": contrib.season,
                "episode": contrib.episode,
                "match_confidence": contrib.match_confidence,
                "match_source": contrib.match_source,
                "disc_content_hash": (
                    contrib.disc_content_hash.hex() if contrib.disc_content_hash else None
                ),
                "chromaprint": {
                    "v": 1,
                    "duration": fp.duration_seconds,
                    "hashes": fp.hashes,
                },
            }
        except Exception as e:
            logger.error(f"Failed to deserialize chromaprint blob for contrib {contrib.id}: {e}")
            contrib.upload_status = "failed"
            contrib.upload_error_msg = f"Blob deserialization error: {e}"
            await session.commit()
            return

        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                    resp = await client.post(
                        f"{server_url.rstrip('/')}/v1/contribute",
                        json=payload,
                    )
                    resp.raise_for_status()

                contrib.upload_status = "success"
                contrib.uploaded_at = datetime.now(UTC)
                await session.commit()
                self._append_audit_log(contrib)
                logger.info(
                    f"Uploaded contribution {contrib.id} "
                    f"(tmdb={contrib.tmdb_id} s{contrib.season}e{contrib.episode})"
                )
                return

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if 400 <= status < 500:
                    contrib.upload_status = "failed"
                    contrib.upload_error_msg = f"HTTP {status} (permanent)"
                    contrib.upload_attempts += 1
                    await session.commit()
                    logger.warning(f"Contrib {contrib.id}: permanent HTTP {status}; marking failed")
                    return
                # 5xx — transient, fall through to retry
                contrib.upload_attempts += 1
                await session.commit()
                logger.warning(
                    f"Contrib {contrib.id}: transient HTTP {status}, attempt {attempt + 1}"
                )

            except httpx.HTTPError as e:
                contrib.upload_attempts += 1
                await session.commit()
                logger.warning(f"Contrib {contrib.id}: network error, attempt {attempt + 1}: {e}")

            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)

        # Exhausted retries
        contrib.upload_status = "failed"
        contrib.upload_error_msg = f"Retries exhausted after {_MAX_ATTEMPTS} attempts"
        await session.commit()
        logger.error(f"Contrib {contrib.id}: upload failed after {_MAX_ATTEMPTS} attempts")

    @staticmethod
    def _append_audit_log(contrib: FingerprintContribution) -> None:
        """Append a redacted audit line to the JSONL contribution log."""
        try:
            CONTRIBUTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "contrib_id": contrib.id,
                "tmdb_id": contrib.tmdb_id,
                "season": contrib.season,
                "episode": contrib.episode,
                "pseudonym_prefix": (contrib.pseudonym or "")[:8],
            }
            with CONTRIBUTION_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            logger.warning("Failed to write contribution audit log", exc_info=True)

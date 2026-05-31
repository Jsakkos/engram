"""Phase 2 fingerprint contribution uploader.

Drains the local FingerprintContribution queue by POST-ing to a remote
fingerprint network server. Phase 1 built the queue; this service drains it.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger
from sqlmodel import select

from app import __version__
from app.database import async_session
from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL
from app.models.fingerprint import FingerprintContribution
from app.services.config_service import get_config
from app.services.zstd_varint_codec import encode_zstd_varint, fingerprint_sha256

CONTRIBUTION_LOG_PATH = Path("~/.engram/cache/contribution_log.jsonl").expanduser()

_BATCH_SIZE = 50
_MAX_ATTEMPTS = 5
_UPLOAD_TIMEOUT = 30.0


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header value (integer seconds) into float seconds.

    Returns None when the header is absent or not a non-negative integer. We do
    not support the HTTP-date form — our server only ever emits integer seconds —
    so callers fall back to exponential backoff when this returns None.
    """
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except (ValueError, AttributeError):
        return None
    return float(seconds) if seconds >= 0 else None


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
                await self._process_batch()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ContributionUploader loop error — will retry next interval")

    async def _process_batch(self) -> None:
        """Fetch up to _BATCH_SIZE pending rows and upload each one.

        Pre-flight privacy gate — nothing leaves the machine unless BOTH hold:
          1. the user has not opted out (enable_fingerprint_contributions),
          2. the user has accepted the disclosure (fingerprint_disclosure_accepted).
        The server URL falls back to DEFAULT_FINGERPRINT_SERVER_URL when unset, so
        existing installs (NULL column) still engage. If data is queued but
        consent is missing, fire the JIT disclosure event — and upload nothing.
        """
        cfg = await get_config()
        if not cfg.enable_fingerprint_contributions:
            logger.debug("fingerprint contributions disabled by user; skipping upload batch")
            return

        # NULL/blank stored URL means "use the default network base origin".
        server_url = cfg.fingerprint_server_url or DEFAULT_FINGERPRINT_SERVER_URL

        # Collect IDs in a short-lived session so the connection is released
        # before any per-row exponential backoff sleep.
        async with async_session() as session:
            stmt = (
                select(FingerprintContribution.id)
                .where(FingerprintContribution.upload_status.is_(None))
                .where(FingerprintContribution.upload_attempts < _MAX_ATTEMPTS)
                .limit(_BATCH_SIZE)
            )
            row_ids = (await session.execute(stmt)).scalars().all()

        if not row_ids:
            return

        if not cfg.fingerprint_disclosure_accepted:
            # Data is queued but the user hasn't consented yet. Prompt; don't upload.
            logger.info(
                "%d fingerprint contribution(s) queued but disclosure not accepted; prompting user",
                len(row_ids),
            )
            await self._notify_disclosure_required(
                len(row_ids), cfg.contribution_pseudonym, server_url
            )
            return

        for row_id in row_ids:
            async with async_session() as session:
                row = await session.get(FingerprintContribution, row_id)
                if row is None:
                    continue  # deleted between the ID query and now
                await self._upload_one(row, session, server_url=server_url)

    async def _notify_disclosure_required(
        self, pending_count: int, pseudonym: str | None, server_url: str | None
    ) -> None:
        """Broadcast the JIT disclosure event (best-effort; never raises).

        Carries the per-install pseudonym + server URL so the modal can show the
        user exactly which identity and endpoint a contribution would use. These
        ride the per-install WS event rather than the LAN-readable /api/config
        endpoint to keep the contribution identifier off a wider surface.
        """
        try:
            from app.api.websocket import manager as ws_manager
            from app.services.event_broadcaster import EventBroadcaster

            await EventBroadcaster(ws_manager).broadcast_fingerprint_disclosure_required(
                pending_count, pseudonym or "", server_url or ""
            )
        except Exception:
            logger.warning("Failed to broadcast fingerprint disclosure event", exc_info=True)

    async def _upload_one(
        self,
        contrib: FingerprintContribution,
        session,
        server_url: str,
    ) -> None:
        from app.matcher.chromaprint_extractor import ChromaprintResult

        try:
            fp = ChromaprintResult.from_blob(contrib.chromaprint_blob)
            fingerprint_bytes = encode_zstd_varint(fp.hashes)
            sha256_bytes = fingerprint_sha256(fp.hashes)
            payload = {
                "wire_format_version": 1,
                "pseudonym": contrib.pseudonym,
                "tmdb_id": contrib.tmdb_id,
                "season": contrib.season,
                "episode": contrib.episode,
                "fingerprint_b64": base64.b64encode(fingerprint_bytes).decode("ascii"),
                "fingerprint_sha256_b64": base64.b64encode(sha256_bytes).decode("ascii"),
                "disc_content_hash_b64": (
                    base64.b64encode(contrib.disc_content_hash).decode("ascii")
                    if contrib.disc_content_hash
                    else None
                ),
                "match_confidence": contrib.match_confidence,
                "match_source": contrib.match_source,
                "client_version": __version__,
            }
        except Exception as e:
            logger.error(
                f"Failed to encode chromaprint for contrib {contrib.id}: {e}", exc_info=True
            )
            contrib.upload_status = "failed"
            contrib.upload_error_msg = f"Blob deserialization error: {e}"
            await session.commit()
            return

        # Honour the lifetime attempt cap: prior failures consumed some budget.
        remaining = _MAX_ATTEMPTS - contrib.upload_attempts
        for attempt in range(remaining):
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

            if attempt < remaining - 1:
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

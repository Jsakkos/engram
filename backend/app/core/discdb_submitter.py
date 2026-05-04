"""TheDiscDB API Submission Client.

Submits disc metadata and scan logs to TheDiscDB's ingestion API.
All functions are non-throwing — errors are captured in SubmissionResult.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.discdb_exporter import (
    generate_export,
    get_makemkv_log_dir,
)
from app.models.app_config import AppConfig
from app.models.disc_job import DiscJob, DiscTitle

logger = logging.getLogger(__name__)

SUBMIT_TIMEOUT = 30  # seconds


@dataclass
class SubmissionResult:
    """Result of a TheDiscDB submission attempt."""

    success: bool = False
    submission_id: str | None = None
    contribute_url: str | None = None
    error: str | None = None


def _auth_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"ApiKey {api_key}"}


async def submit_disc(
    payload: dict,
    api_key: str,
    base_url: str,
) -> SubmissionResult:
    """Submit disc data JSON to TheDiscDB API.

    POST {base_url}/api/engram/disc

    The API returns {"id": int, "contentHash": str, "updated": bool}.
    If the payload contains a release_id, the contribution page is at
    {base_url}/contribute/engram/{release_id}.
    """
    url = f"{base_url.rstrip('/')}/api/engram/disc"
    try:
        async with httpx.AsyncClient(timeout=SUBMIT_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(api_key))
            resp.raise_for_status()
            data = resp.json()

            # Map API response fields to our result model
            submission_id = data.get("submission_id") or str(data.get("id", ""))
            contribute_url = data.get("contribute_url")
            if not contribute_url:
                release_id = payload.get("disc", {}).get("release_id")
                if release_id:
                    contribute_url = f"{base_url.rstrip('/')}/contribute/engram/{release_id}"

            return SubmissionResult(
                success=True,
                submission_id=submission_id or None,
                contribute_url=contribute_url,
            )
    except httpx.HTTPStatusError as e:
        msg = f"TheDiscDB API returned {e.response.status_code}"
        if e.response.status_code == 401:
            msg = "TheDiscDB API key is invalid or expired"
        logger.warning(f"Disc submission failed: {msg}")
        return SubmissionResult(error=msg)
    except httpx.HTTPError as e:
        msg = f"Network error submitting to TheDiscDB: {e}"
        logger.warning(msg)
        return SubmissionResult(error=msg)


async def submit_scan_log(
    content_hash: str,
    log_path: Path,
    api_key: str,
    base_url: str,
) -> bool:
    """Submit MakeMKV scan log as text/plain to TheDiscDB API.

    POST {base_url}/api/engram/disc/{content_hash}/logs/scan
    """
    if not log_path.exists():
        logger.debug(f"No scan log at {log_path}, skipping submission")
        return False

    url = f"{base_url.rstrip('/')}/api/engram/disc/{content_hash}/logs/scan"
    log_text = log_path.read_text(encoding="utf-8", errors="replace")

    try:
        async with httpx.AsyncClient(timeout=SUBMIT_TIMEOUT) as client:
            resp = await client.post(
                url,
                content=log_text,
                headers={
                    **_auth_headers(api_key),
                    "Content-Type": "text/plain",
                },
            )
            resp.raise_for_status()
            return True
    except httpx.HTTPError as e:
        logger.warning(f"Scan log submission failed for {content_hash}: {e}")
        return False


async def submit_job(
    job: DiscJob,
    titles: list[DiscTitle],
    config: AppConfig,
    app_version: str = "unknown",
) -> SubmissionResult:
    """Orchestrate full submission: disc data + scan log."""
    if not job.content_hash:
        return SubmissionResult(error="No content hash available")

    # Generate the export payload (also writes local JSON file)
    export_dir = generate_export(job, titles, config, app_version=app_version)
    if not export_dir:
        return SubmissionResult(error="Export skipped (no data or all discdb-sourced)")

    # Read the generated JSON payload
    import json

    json_path = export_dir / "disc_data.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    # Submit disc data
    result = await submit_disc(payload, config.discdb_api_key, config.discdb_api_url)
    if not result.success:
        return result

    # Submit scan log (best-effort, don't fail submission if this fails)
    log_dir = get_makemkv_log_dir(job.id)
    scan_log = log_dir / "scan.log"
    await submit_scan_log(
        job.content_hash,
        scan_log,
        config.discdb_api_key,
        config.discdb_api_url,
    )

    logger.info(f"Job {job.id}: Submitted to TheDiscDB (submission_id={result.submission_id})")
    return result


@dataclass
class BatchSubmissionResult:
    """Result of batch submission for a release group."""

    submitted: int = 0
    failed: int = 0
    results: list[dict] = field(default_factory=list)
    contribute_url: str | None = None


async def submit_release_group(
    release_group_id: str,
    session: AsyncSession,
    config: AppConfig,
    app_version: str = "unknown",
) -> BatchSubmissionResult:
    """Submit all completed jobs in a release group sequentially."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.disc_job import JobState

    result = BatchSubmissionResult()

    jobs_query = await session.execute(
        select(DiscJob).where(
            DiscJob.release_group_id == release_group_id,
            DiscJob.state == JobState.COMPLETED,
        )
    )
    jobs = list(jobs_query.scalars().all())

    # Pre-load all titles to avoid N+1 queries
    job_ids = [j.id for j in jobs]
    if job_ids:
        all_titles_query = await session.execute(
            select(DiscTitle).where(DiscTitle.job_id.in_(job_ids))
        )
        all_titles = all_titles_query.scalars().all()
        titles_by_job: dict[int, list] = {}
        for t in all_titles:
            titles_by_job.setdefault(t.job_id, []).append(t)
    else:
        titles_by_job = {}

    for job in jobs:
        titles = titles_by_job.get(job.id, [])

        job_result = await submit_job(job, titles, config, app_version)

        entry = {
            "job_id": job.id,
            "success": job_result.success,
            "submission_id": job_result.submission_id,
            "contribute_url": job_result.contribute_url,
            "error": job_result.error,
        }
        result.results.append(entry)

        if job_result.success:
            result.submitted += 1
            if job_result.contribute_url:
                result.contribute_url = job_result.contribute_url
            job.submitted_at = datetime.now(UTC)
            job.discdb_submission_id = job_result.submission_id
            job.discdb_contribute_url = job_result.contribute_url
            session.add(job)
        else:
            result.failed += 1

    await session.commit()
    return result

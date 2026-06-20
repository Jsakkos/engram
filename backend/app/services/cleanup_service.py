"""Cleanup Service - Handles staging directory cleanup and DiscDB export.

Extracted from JobManager to isolate cleanup concerns.
"""

import asyncio
import logging
import time
from pathlib import Path

from sqlmodel import select

from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import DiscTitle

logger = logging.getLogger(__name__)


class CleanupService:
    """Manages staging cleanup policies and auto-export."""

    async def on_job_terminal(self, job_id: int, state: JobState) -> None:
        """Called by state machine when a job reaches COMPLETED or FAILED."""
        from app.services.config_service import get_config

        config = await get_config()
        policy = config.staging_cleanup_policy

        # "manual" and "after_days" need no action here ("after_days" is handled
        # by a background task).
        if policy == "on_completion" or (policy == "on_success" and state == JobState.COMPLETED):
            await self.delete_staging(job_id)

        # Auto-export for TheDiscDB contributions
        from app.core.features import DISCDB_CONTRIBUTIONS_ENABLED

        if (
            DISCDB_CONTRIBUTIONS_ENABLED
            and state == JobState.COMPLETED
            and config.discdb_contributions_enabled
        ):
            await self.auto_export_for_discdb(job_id, config)

    async def delete_staging(self, job_id: int) -> None:
        """Delete the staging directory for a job.

        For a disc rip, staging_path is a regenerable temp dir under the
        staging root, so deleting it just reclaims space. For a watch-folder
        import (drive_id == "import"), staging_path is the user's *original*
        source folder on disk (the import_watch_path or a subfolder of it).
        Successfully imported files are *moved* out of it, so cleanup has
        nothing legitimate to reclaim — and an rmtree would destroy whatever
        remains, including content the importer never scanned (e.g. Season
        subfolders skipped by the flat-file scan). Never delete an import
        source.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job or not job.staging_path:
                return

            if job.drive_id == "import":
                logger.info(
                    "Skipping staging cleanup for import job %s: %s is the user's "
                    "source directory, not regenerable staging",
                    job_id,
                    job.staging_path,
                )
                return

            staging_path = Path(job.staging_path)
            if not staging_path.exists():
                return

            try:
                import shutil

                shutil.rmtree(staging_path)
                logger.info(f"Cleaned up staging directory: {staging_path}")
            except Exception as e:
                logger.warning(f"Failed to clean staging for job {job_id}: {e}")

    async def auto_export_for_discdb(self, job_id: int, config) -> None:
        """Auto-export disc data for TheDiscDB contribution."""
        # Brief delay to let MakeMKV log files flush to disk
        await asyncio.sleep(2)
        try:
            from app.core.discdb_exporter import generate_export, mark_exported

            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job or not job.content_hash:
                    return

                stmt = select(DiscTitle).where(DiscTitle.job_id == job_id)
                titles = list((await session.execute(stmt)).scalars().all())

                from app import __version__

                export_dir = generate_export(job, titles, config, app_version=__version__)
                if export_dir:
                    await mark_exported(job_id, session)
                    logger.info(f"Job {job_id}: Auto-exported disc data to {export_dir}")
        except Exception as e:
            logger.warning(f"Job {job_id}: Failed to auto-export disc data: {e}")

    async def run_timed_cleanup(self, staging_root: str, max_age_days: int) -> None:
        """Background task: periodically delete staging dirs older than max_age_days."""
        import shutil

        interval = 3600  # Check every hour
        while True:
            try:
                await asyncio.sleep(interval)
                root = Path(staging_root)
                if not root.exists():
                    continue

                now = time.time()
                cutoff = now - (max_age_days * 86400)

                for d in root.iterdir():
                    # This path does its own rmtree without the import guard in
                    # delete_staging, so it relies on a structural invariant to
                    # never touch a user's import source: it only deletes dirs
                    # that are (a) directly inside the managed staging_root and
                    # (b) named "job_*". Import sources are the user's
                    # import_watch_path (or a subfolder of it), which is neither
                    # under staging_root nor named "job_*" — disc-rip staging
                    # dirs are the only things created with that prefix here. If
                    # that convention ever changes, add a drive_id == "import"
                    # DB cross-check before deleting.
                    if not d.is_dir() or not d.name.startswith("job_"):
                        continue
                    try:
                        mtime = d.stat().st_mtime
                        if mtime < cutoff:
                            shutil.rmtree(d)
                            logger.info(
                                f"Timed cleanup: deleted staging dir {d.name} "
                                f"(age: {(now - mtime) / 86400:.1f} days)"
                            )
                    except Exception as e:
                        logger.warning(f"Timed cleanup: failed to delete {d}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Timed cleanup error: {e}", exc_info=True)

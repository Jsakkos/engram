"""Recovery actions for a FAILED job (#713).

A failed job is terminal: nothing here moves it out of FAILED. Retrying hands
the files it left in staging to the import pipeline as a NEW job
(``JobManager.create_job_from_staging``), so the failure stays in History as
what happened and the retry gets its own row, its own ownership check in
``import_guard`` (FAILED never blocks), and its own outcome. Nothing here ever
needs the disc: the retry reads only files already on disk, so a drive that
has since been emptied or loaded with another disc is irrelevant.
"""

import json
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.models.disc_job import DiscJob, DiscTitle, JobState, TitleState


@dataclass(frozen=True)
class StagingRecovery:
    """Whether a failed job's staging folder can be retried, and if not, why."""

    staging_path: str | None
    staging_exists: bool
    files: tuple[str, ...]
    # Set only when the retry cannot simply re-glob the folder: the original
    # job was a manifest import, or an interrupted track left a partial file.
    retry_manifest: dict | None
    reason: str | None

    @property
    def can_retry(self) -> bool:
        return self.reason is None


def assess_staging_recovery(job: DiscJob, titles: Iterable[DiscTitle]) -> StagingRecovery:
    staging = job.staging_path
    staging_dir = Path(staging) if staging else None
    exists = bool(staging_dir and staging_dir.is_dir())

    def refuse(reason: str, files: tuple[str, ...] = ()) -> StagingRecovery:
        return StagingRecovery(staging, exists, files, None, reason)

    if job.state != JobState.FAILED:
        return refuse("Only a failed job can be retried from staging.")
    if staging_dir is None:
        return refuse("This job never created a staging folder.")
    if not exists:
        return refuse("The staging folder no longer exists (it may have been cleaned up).")

    # The track MakeMKV was writing when the job died holds a truncated file;
    # feeding it to the matcher would only produce a wrong or failed match.
    partial = {
        str(Path(t.output_filename))
        for t in titles
        if t.state == TitleState.RIPPING and t.output_filename
    }

    manifest: dict | None = None
    if job.import_manifest_json:
        try:
            manifest = json.loads(job.import_manifest_json)
        except (ValueError, TypeError):
            manifest = None
    if manifest is not None:
        candidates = [Path(f) for f in manifest.get("files", [])]
    else:
        candidates = sorted(staging_dir.glob("*.mkv"))

    files = tuple(str(f) for f in candidates if f.is_file() and str(f) not in partial)
    if not files:
        return refuse("The staging folder holds no ripped files to retry.")

    retry_manifest: dict | None = None
    if manifest is not None:
        retry_manifest = {**manifest, "files": list(files)}
    elif len(files) != len(candidates):
        retry_manifest = {
            "root": str(staging_dir),
            "files": list(files),
            "picked_is_show": False,
            "picked_is_season": False,
        }
    return StagingRecovery(staging, exists, files, retry_manifest, None)


def open_in_file_manager(path: Path) -> None:
    """Reveal ``path`` in the host's file manager. Never blocks on the child."""
    # ``path`` is always a job's stored staging_path, never client input.
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

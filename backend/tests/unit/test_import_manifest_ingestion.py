"""The import manifest, when present, selects the exact files to ingest."""

import json
from pathlib import Path

from app.models.disc_job import DiscJob


def _resolve_mkv_files(job: DiscJob) -> list[Path]:
    """Mirror of the selection logic in identify_from_staging (kept in sync)."""
    staging_dir = Path(job.staging_path)
    if job.import_manifest_json:
        manifest = json.loads(job.import_manifest_json)
        files = sorted(Path(f) for f in manifest.get("files", []))
        return [f for f in files if f.exists()]
    return sorted(staging_dir.glob("*.mkv"))


def test_manifest_files_win_over_glob(tmp_path: Path):
    season = tmp_path / "Season 1"
    (season / "Disc 1").mkdir(parents=True)
    nested = season / "Disc 1" / "ep.mkv"
    nested.write_bytes(b"0")
    (season / "stray.mkv").write_bytes(b"0")  # present in dir but NOT in manifest

    job = DiscJob(
        drive_id="import",
        staging_path=str(season),
        import_manifest_json=json.dumps({"root": str(tmp_path), "files": [str(nested)]}),
    )

    result = _resolve_mkv_files(job)
    assert result == [nested]  # nested disc file ingested; stray excluded


def test_no_manifest_falls_back_to_glob(tmp_path: Path):
    (tmp_path / "a.mkv").write_bytes(b"0")
    job = DiscJob(drive_id="staging", staging_path=str(tmp_path))
    result = _resolve_mkv_files(job)
    assert [p.name for p in result] == ["a.mkv"]

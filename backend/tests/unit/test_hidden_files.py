"""macOS AppleDouble sidecars (``._name``) are never media.

On an exFAT/FAT/SMB volume macOS writes ``._<file>`` beside every file it
creates. ``._Show_t00.mkv`` matches ``*.mkv`` and carries a title index, and its
4 KB size never changes while MakeMKV grows the real file, so the completion
detector used to report it as a finished title and hand the stub to the matcher.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import import_scanner
from app.core.extractor import TitleCompletionDetector
from app.core.media_formats import (
    glob_media,
    is_hidden_file,
    is_importable_video,
    is_macos_metadata_file,
    list_video_files,
    rglob_media,
)
from app.core.organizer import organize_movie
from app.models.app_config import AppConfig

REAL = "Generic TV Series Season 1 Disc 2_t00.mkv"
SIDECAR = "._" + REAL


@pytest.mark.unit
class TestHiddenFiles:
    def test_dot_files_are_hidden(self):
        assert is_hidden_file(SIDECAR)
        assert is_hidden_file(".DS_Store")
        assert not is_hidden_file(REAL)

    def test_only_macos_litter_is_deletable(self):
        assert is_macos_metadata_file(SIDECAR)
        assert is_macos_metadata_file(".DS_Store")
        assert not is_macos_metadata_file(".plexmatch")
        assert not is_macos_metadata_file(REAL)

    def test_sidecar_is_not_importable(self):
        assert not is_importable_video(SIDECAR)
        assert is_importable_video(REAL)

    def test_globs_skip_sidecars(self, tmp_path):
        (tmp_path / REAL).write_bytes(b"x" * 10)
        (tmp_path / SIDECAR).write_bytes(b"x" * 4)
        sub = tmp_path / "Season 01"
        sub.mkdir()
        (sub / "._S01E01.mkv").write_bytes(b"x")
        (sub / "S01E01.mkv").write_bytes(b"x")

        assert [p.name for p in glob_media(tmp_path, "*.mkv")] == [REAL]
        assert [p.name for p in glob_media(tmp_path, "*_t00.mkv")] == [REAL]
        assert sorted(p.name for p in rglob_media(tmp_path, "*.mkv")) == [REAL, "S01E01.mkv"]
        assert [p.name for p in list_video_files(tmp_path)] == [REAL]


@pytest.mark.unit
class TestDetectorIgnoresSidecar:
    def test_stable_sidecar_is_never_reported_complete(self):
        """The reported bug: the stub held still while the real title grew."""
        det = TitleCompletionDetector(stable_required=3)
        done: list[str] = []
        for size in range(1, 10):
            done += [f for f, _ in det.poll({SIDECAR: 4096, REAL: size * 1_000_000})]
        assert done == []
        assert [f for f, _ in det.poll({SIDECAR: 4096, REAL: 9_000_000}, force=True)] == [REAL]


@pytest.mark.unit
class TestStagingCleanupIgnoresSidecars:
    def _organize(
        self,
        tmp_path,
        stray: str | None,
        *,
        in_staging: bool = True,
        watch: bool = False,
        sidecar: bool = True,
    ):
        library = tmp_path / "lib"
        library.mkdir()
        staging_root = tmp_path / "staging"
        staging_root.mkdir()
        # A rip lives in an Engram ``job_*`` folder; an import is the user's own folder.
        source = staging_root / "job_1" if in_staging else tmp_path / "imports"
        source.mkdir()
        (source / "t00.mkv").write_bytes(b"feature" * 10)
        if sidecar:
            (source / "._t00.mkv").write_bytes(b"x" * 4)
        if stray:
            (source / stray).write_bytes(b"x")
        cfg = AppConfig(
            library_movies_path=str(library),
            staging_path=str(staging_root),
            import_watch_path=str(source) if watch else None,
            naming_movie_format="{title} ({year})",
        )
        with patch("app.services.config_service.get_config_sync", return_value=cfg):
            result = organize_movie(source, "Hairspray", 2007, library, already_clean=True)
        assert result["success"] is True
        return source

    def test_staging_with_a_users_dot_file_left_is_kept_untouched(self, tmp_path):
        """A user's own dot-file keeps the folder, as a visible file would."""
        staging = self._organize(tmp_path, ".plexmatch")
        assert sorted(p.name for p in staging.iterdir()) == ["._t00.mkv", ".plexmatch"]

    def test_staging_with_only_sidecars_left_is_removed(self, tmp_path):
        assert not self._organize(tmp_path, ".DS_Store").exists()

    def test_staging_with_a_real_file_left_is_kept_untouched(self, tmp_path):
        staging = self._organize(tmp_path, "notes.txt")
        assert sorted(p.name for p in staging.iterdir()) == ["._t00.mkv", "notes.txt"]

    def test_import_folder_outside_staging_is_never_removed(self, tmp_path):
        """A manual import's source is the user's folder: its sidecars stay, so does it."""
        source = self._organize(tmp_path, ".DS_Store", in_staging=False)
        assert sorted(p.name for p in source.iterdir()) == [".DS_Store", "._t00.mkv"]

    def test_import_watch_folder_is_never_removed(self, tmp_path):
        """Removing the watch folder would break every later watch scan."""
        source = self._organize(tmp_path, ".DS_Store", in_staging=False, watch=True)
        assert sorted(p.name for p in source.iterdir()) == [".DS_Store", "._t00.mkv"]

    def test_import_folder_left_empty_is_kept(self, tmp_path):
        """Even with nothing left in it, a folder the user picked is not Engram's to remove."""
        source = self._organize(tmp_path, None, in_staging=False, sidecar=False)
        assert source.is_dir() and list(source.iterdir()) == []

    def test_staging_left_empty_is_removed(self, tmp_path):
        assert not self._organize(tmp_path, None, sidecar=False).exists()

    def test_import_watch_folder_inside_staging_is_kept(self, tmp_path):
        """A watch folder placed under ``staging_path`` is still the user's."""
        source = self._organize(tmp_path, ".DS_Store", watch=True)
        assert sorted(p.name for p in source.iterdir()) == [".DS_Store", "._t00.mkv"]


@pytest.mark.unit
class TestIsoSidecarsAreNotDiscImages:
    def test_scan_lists_only_the_real_iso(self, tmp_path):
        (tmp_path / "Movie.iso").write_bytes(b"x" * 64)
        (tmp_path / "._Movie.iso").write_bytes(b"x" * 4)
        result = import_scanner.scan(tmp_path)
        assert [d.path.name for d in result.disc_images] == ["Movie.iso"]

    def test_picking_the_sidecar_directly_yields_no_disc_image(self, tmp_path):
        sidecar = tmp_path / "._Movie.iso"
        sidecar.write_bytes(b"x" * 4)
        assert import_scanner.scan(sidecar).disc_images == []


@pytest.mark.unit
def test_no_raw_mkv_glob_in_app():
    """Every ``*.mkv`` directory scan goes through ``glob_media`` / ``rglob_media``.

    A plain ``Path.glob("*.mkv")`` also returns ``._name.mkv`` sidecars. If this
    fails, switch the listed call to the helper from ``app.core.media_formats``.

    Not exhaustive: it only catches a ``.glob(``/``.rglob(`` call with a literal
    ``mkv`` on the same line. A pattern held in a variable, ``iterdir()`` plus an
    ``endswith(".mkv")`` filter, and ``os.walk`` all slip past it, so new scans of
    those shapes must filter with ``is_hidden_file`` by hand.
    """
    app_dir = Path(__file__).resolve().parents[2] / "app"
    raw_glob = re.compile(r"\.r?glob\([^)\n]*mkv")
    offenders = [
        f"{path.relative_to(app_dir)}:{n}"
        for path in sorted(app_dir.rglob("*.py"))
        if path.name != "media_formats.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if raw_glob.search(line)
    ]
    assert offenders == []

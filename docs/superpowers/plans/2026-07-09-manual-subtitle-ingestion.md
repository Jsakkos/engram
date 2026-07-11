# Manual Subtitle Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user bulk-import a folder of manually downloaded `.srt` files into the review page for episodes none of the 3 automated subtitle sources covered, so the existing matcher can use them exactly like an automated find.

**Architecture:** Two new job-scoped endpoints (`preview`, `commit`) write user-supplied `.srt` text straight into the on-disk cache directory `LocalSubtitleProvider` already scans first — no matcher changes. The frontend reads files client-side via `FileReader` and posts plain JSON (no multipart), consistent with every other endpoint in the app. A new `SubtitleUploadModal` component hooks into the existing "no reference subtitle" warning in `ReviewQueue.tsx`.

**Tech Stack:** FastAPI + Pydantic (backend), React + TypeScript (frontend), pytest (backend tests), vitest + React Testing Library (frontend unit tests).

**Spec:** [docs/superpowers/specs/2026-07-09-manual-subtitle-ingestion-design.md](../specs/2026-07-09-manual-subtitle-ingestion-design.md)

---

## Key existing code this plan reuses (don't reinvent)

- `is_valid_srt_file(file_path: Path) -> bool` — `backend/app/matcher/subtitle_utils.py:10`
- `parse_season_episode(filename: str) -> EpisodeInfo | None` — `backend/app/matcher/subtitle_provider.py:31`
- `corpus_dir_name(tmdb_id, show_name: str) -> str` — `backend/app/matcher/subtitle_utils.py:260`
- `sanitize_filename(filename: str) -> str` — `backend/app/matcher/subtitle_utils.py:239`
- `reference_coverage(cache_dir, tmdb_id, show_name, season, episode_numbers) -> dict[str, str]` — `backend/app/matcher/episode_identification.py:281` (returns `"precomputed"`/`"downloaded"`/`"missing"` per `SxxEyy` code — this is the exact function that already powers the `has_reference` flag in the season roster)
- `get_job_or_404` FastAPI dependency — `backend/app/api/routes.py:56`
- `apiFetch<T>` / `apiFetchVoid` fetch wrappers — `frontend/src/api/client.ts:42,51`
- `useSeasonRoster` hook's `reload` — `frontend/src/hooks/useSeasonRoster.ts` (used via `frontend/src/components/ReviewQueue.tsx:191`)
- `SvActionButton`, `SvPanel`, `SvLabel`, `sv` tokens — `frontend/src/app/components/synapse`

---

### Task 1: `is_valid_srt_content` — validate in-memory SRT text

**Files:**
- Modify: `backend/app/matcher/subtitle_utils.py:10-53`
- Test: `backend/tests/unit/test_subtitle_utils.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_subtitle_utils.py` (append a new test class; keep the existing `is_valid_srt_file` tests untouched):

```python
from app.matcher.subtitle_utils import is_valid_srt_content, is_valid_srt_file


class TestIsValidSrtContent:
    def test_accepts_plain_srt_text(self):
        content = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"
        assert is_valid_srt_content(content) is True

    def test_rejects_html(self):
        content = "<!DOCTYPE html><html><body>Not a subtitle</body></html>" + "x" * 60
        assert is_valid_srt_content(content) is False

    def test_rejects_too_short(self):
        assert is_valid_srt_content("short") is False

    def test_rejects_text_without_timestamps(self):
        content = "Just some plain text with no SRT timing markers at all here." * 2
        assert is_valid_srt_content(content) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_subtitle_utils.py -v`
Expected: `FAIL` — `ImportError: cannot import name 'is_valid_srt_content'`

- [ ] **Step 3: Refactor `is_valid_srt_file` to share the sniffing logic, and add `is_valid_srt_content`**

In `backend/app/matcher/subtitle_utils.py`, replace the existing `is_valid_srt_file` function (lines 10-53) with:

```python
_HTML_MARKERS = ("<!doctype", "<html", "<head", "<body", "<div")


def _looks_like_srt(header: str) -> bool:
    """True if a decoded text header passes SRT sniffing: no HTML markers, and
    contains the SRT timestamp arrow ``-->``. Shared by the file-based and
    in-memory content validators below.
    """
    header = header.lower()
    if any(marker in header for marker in _HTML_MARKERS):
        return False
    return "-->" in header


def is_valid_srt_file(file_path: Path) -> bool:
    """Validate that ``file_path`` is a real SRT subtitle file, not HTML
    or other garbage masquerading as one.

    Checks:
    1. File exists and is at least 50 bytes.
    2. Header doesn't contain HTML markers.
    3. Contains the SRT timestamp arrow ``-->`` somewhere in the header.

    Lives in ``subtitle_utils`` so every provider client and the
    scheduler can validate downloads without importing
    ``testing_service`` (which would create a circular dependency:
    ``testing_service`` imports the scheduler, which imports
    ``is_valid_srt_file``).
    """
    try:
        if not file_path.exists() or file_path.stat().st_size < 50:
            return False

        # Decode by BOM. TVsubtitles (and others) sometimes serve
        # UTF-16-encoded SRTs; read as UTF-8 those keep a NUL between every
        # character, so the ASCII ``-->`` check below never matches and a
        # perfectly valid subtitle gets rejected. Read a generous chunk of
        # raw bytes (UTF-16 is 2 bytes/char, so 1000 bytes ≈ 500 chars —
        # still well past the first timestamp).
        raw = file_path.read_bytes()[:1000]
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            header = raw.decode("utf-16", errors="ignore")
        else:
            header = raw.decode("utf-8", errors="ignore")

        if not _looks_like_srt(header):
            logger.warning(f"Rejecting {file_path.name}: not a valid SRT (HTML or no timestamp markers)")
            return False

        return True

    except Exception as e:
        logger.warning(f"Error validating {file_path}: {e}")
        return False


def is_valid_srt_content(content: str) -> bool:
    """Validate in-memory SRT text (e.g. a manually uploaded file read
    client-side) using the same sniffing heuristics as ``is_valid_srt_file``,
    without touching disk. Size is checked in UTF-8 bytes to match the
    on-disk 50-byte threshold.
    """
    if len(content.encode("utf-8")) < 50:
        return False
    return _looks_like_srt(content[:1000])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_subtitle_utils.py -v`
Expected: `PASS` — all tests (old and new) green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/subtitle_utils.py backend/tests/unit/test_subtitle_utils.py
git commit -m "feat: add is_valid_srt_content for in-memory SRT validation"
```

---

### Task 2: `manual_subtitle_import.classify_files` — preview logic

**Files:**
- Create: `backend/app/matcher/manual_subtitle_import.py`
- Test: `backend/tests/unit/test_manual_subtitle_import.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_manual_subtitle_import.py`:

```python
"""Unit tests for manual subtitle bulk-import preview/commit logic."""

from unittest.mock import patch

from app.matcher.manual_subtitle_import import (
    MAX_CONTENT_BYTES,
    PreviewInputFile,
    classify_files,
)

VALID_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"


class TestClassifyFiles:
    def test_ready_when_unparseable_slot_is_missing(self, tmp_path):
        files = [PreviewInputFile(filename="Show.Name.S01E05.srt", content=VALID_SRT)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E05": "missing"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert len(results) == 1
        assert results[0].season == 1
        assert results[0].episode == 5
        assert results[0].status == "ready"
        assert results[0].warning is None

    def test_already_covered_when_reference_exists(self, tmp_path):
        files = [PreviewInputFile(filename="Show.Name.S01E02.srt", content=VALID_SRT)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E02": "downloaded"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "already_covered"

    def test_unparseable_filename(self, tmp_path):
        files = [PreviewInputFile(filename="no_episode_info.srt", content=VALID_SRT)]
        results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "unparseable"
        assert results[0].season is None
        assert results[0].episode is None

    def test_out_of_range_season_is_unparseable(self, tmp_path):
        files = [PreviewInputFile(filename="Show.S99E01.srt", content=VALID_SRT)]
        results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "unparseable"

    def test_invalid_content_rejected(self, tmp_path):
        files = [PreviewInputFile(filename="Show.S01E01.srt", content="not really a subtitle" * 5)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E01": "missing"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "invalid_content"

    def test_content_too_large_rejected(self, tmp_path):
        oversized = VALID_SRT + ("x" * (MAX_CONTENT_BYTES + 1))
        files = [PreviewInputFile(filename="Show.S01E01.srt", content=oversized)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E01": "missing"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "invalid_content"

    def test_duplicate_within_batch(self, tmp_path):
        files = [
            PreviewInputFile(filename="Show.S01E05.srt", content=VALID_SRT),
            PreviewInputFile(filename="Show.S01E05.alt.srt", content=VALID_SRT),
        ]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E05": "missing"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "ready"
        assert results[1].status == "duplicate"

    def test_encoding_warning_on_replacement_char(self, tmp_path):
        content = VALID_SRT + "caf�\n"
        files = [PreviewInputFile(filename="Show.S01E05.srt", content=content)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E05": "missing"}):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "ready"
        assert results[0].warning == "possible encoding issue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_manual_subtitle_import.py -v`
Expected: `FAIL` — `ModuleNotFoundError: No module named 'app.matcher.manual_subtitle_import'`

- [ ] **Step 3: Create the module with constants, dataclasses, and `classify_files`**

Create `backend/app/matcher/manual_subtitle_import.py`:

```python
"""Manual subtitle bulk-import: preview/commit logic for user-supplied .srt files.

Feeds directly into the existing subtitle cache that ``LocalSubtitleProvider``
scans (``subtitle_provider.py``) — writing a correctly-named file there makes it
available to matching identically to an automated find, so this module owns
parsing/validation/writing only and never touches the matcher.

See docs/superpowers/specs/2026-07-09-manual-subtitle-ingestion-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.matcher.episode_identification import reference_coverage
from app.matcher.subtitle_provider import parse_season_episode
from app.matcher.subtitle_utils import is_valid_srt_content

MIN_SEASON = 0
MAX_SEASON = 50
MIN_EPISODE = 1
MAX_EPISODE = 999
MAX_FILES_PER_BATCH = 60
MAX_CONTENT_BYTES = 2 * 1024 * 1024


@dataclass
class PreviewInputFile:
    filename: str
    content: str


@dataclass
class PreviewFileResult:
    filename: str
    season: int | None
    episode: int | None
    status: str  # "ready" | "already_covered" | "unparseable" | "invalid_content" | "duplicate"
    warning: str | None = None


def _in_range(season: int, episode: int) -> bool:
    return MIN_SEASON <= season <= MAX_SEASON and MIN_EPISODE <= episode <= MAX_EPISODE


def _encoding_warning(content: str) -> str | None:
    return "possible encoding issue" if "�" in content else None


def classify_files(
    cache_dir: Path,
    tmdb_id: int | None,
    show_name: str,
    files: list[PreviewInputFile],
) -> list[PreviewFileResult]:
    """Classify each uploaded file for the preview confirmation table.

    Parses season/episode from the filename (same parser ``LocalSubtitleProvider``
    relies on elsewhere), checks whether a reference already exists via the same
    ``reference_coverage`` function that powers the season-roster's ``has_reference``
    flag, and flags duplicates within the batch (first file wins the slot,
    regardless of its own validity).
    """
    parsed: list[tuple[PreviewInputFile, int | None, int | None]] = []
    seasons_needed: dict[int, list[int]] = {}
    for f in files:
        info = parse_season_episode(f.filename)
        season = info.season if info else None
        episode = info.episode if info else None
        if season is not None and episode is not None and _in_range(season, episode):
            seasons_needed.setdefault(season, []).append(episode)
        else:
            season = episode = None
        parsed.append((f, season, episode))

    coverage_by_season: dict[int, dict[str, str]] = {
        season: reference_coverage(cache_dir, tmdb_id, show_name, season, episodes)
        for season, episodes in seasons_needed.items()
    }

    results: list[PreviewFileResult] = []
    seen: set[tuple[int, int]] = set()
    for f, season, episode in parsed:
        if season is None or episode is None:
            results.append(PreviewFileResult(f.filename, None, None, "unparseable"))
            continue

        key = (season, episode)
        if key in seen:
            results.append(
                PreviewFileResult(
                    f.filename, season, episode, "duplicate",
                    warning="same episode as an earlier file in this batch",
                )
            )
            continue
        seen.add(key)

        if len(f.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            results.append(
                PreviewFileResult(f.filename, season, episode, "invalid_content", warning="file too large")
            )
            continue
        if not is_valid_srt_content(f.content):
            results.append(
                PreviewFileResult(f.filename, season, episode, "invalid_content", warning="not a valid SRT")
            )
            continue

        code = f"S{season:02d}E{episode:02d}"
        if coverage_by_season.get(season, {}).get(code, "missing") != "missing":
            results.append(PreviewFileResult(f.filename, season, episode, "already_covered"))
            continue

        results.append(PreviewFileResult(f.filename, season, episode, "ready", warning=_encoding_warning(f.content)))

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_manual_subtitle_import.py -v`
Expected: `PASS` — all `TestClassifyFiles` tests green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/manual_subtitle_import.py backend/tests/unit/test_manual_subtitle_import.py
git commit -m "feat: add classify_files preview logic for manual subtitle import"
```

---

### Task 3: `manual_subtitle_import.commit_files` — write logic

**Files:**
- Modify: `backend/app/matcher/manual_subtitle_import.py`
- Test: `backend/tests/unit/test_manual_subtitle_import.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_manual_subtitle_import.py`:

```python
from app.matcher.manual_subtitle_import import CommitInputFile, commit_files


class TestCommitFiles:
    def test_writes_file_to_expected_path(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=5, content=VALID_SRT)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E05": "missing"}):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "imported"
        dest = tmp_path / "data" / "123" / "Show Name - S01E05.srt"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == VALID_SRT

    def test_skips_when_already_covered_at_commit_time(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=2, content=VALID_SRT)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E02": "downloaded"}):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "skipped"
        assert outcomes[0].reason == "already_covered"
        dest = tmp_path / "data" / "123" / "Show Name - S01E02.srt"
        assert not dest.exists()

    def test_rejects_out_of_range_season(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=999, episode=1, content=VALID_SRT)]
        outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "error"
        assert "range" in outcomes[0].reason

    def test_rejects_invalid_content(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=1, content="not a subtitle" * 5)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E01": "missing"}):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "error"

    def test_duplicate_within_batch_skips_second(self, tmp_path):
        files = [
            CommitInputFile(filename="a.srt", season=1, episode=5, content=VALID_SRT),
            CommitInputFile(filename="b.srt", season=1, episode=5, content=VALID_SRT),
        ]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E05": "missing"}):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "imported"
        assert outcomes[1].status == "skipped"
        assert outcomes[1].reason == "duplicate within this batch"

    def test_sanitizes_show_name_in_filename(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=1, content=VALID_SRT)]
        with patch("app.matcher.manual_subtitle_import.reference_coverage", return_value={"S01E01": "missing"}):
            commit_files(tmp_path, 123, "Law & Order: SVU", files)
        dest = tmp_path / "data" / "123" / "Law & Order - SVU - S01E01.srt"
        assert dest.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_manual_subtitle_import.py -v`
Expected: `FAIL` — `ImportError: cannot import name 'CommitInputFile'`

- [ ] **Step 3: Add `CommitInputFile`, `CommitFileOutcome`, and `commit_files`**

First, update the `subtitle_utils` import line near the top of `backend/app/matcher/manual_subtitle_import.py` — `commit_files` needs two more names than `classify_files` did:

```python
from app.matcher.subtitle_utils import corpus_dir_name, is_valid_srt_content, sanitize_filename
```

Then append to `backend/app/matcher/manual_subtitle_import.py`:

```python
@dataclass
class CommitInputFile:
    filename: str
    season: int
    episode: int
    content: str


@dataclass
class CommitFileOutcome:
    filename: str
    season: int
    episode: int
    status: str  # "imported" | "skipped" | "error"
    reason: str | None = None


def commit_files(
    cache_dir: Path,
    tmdb_id: int | None,
    show_name: str,
    files: list[CommitInputFile],
) -> list[CommitFileOutcome]:
    """Validate and write each confirmed file into the subtitle cache.

    Re-validates everything independently of whatever the preview step said —
    this must never trust a client-echoed preview verdict, since a reference
    could have appeared between preview and commit, or the payload could be
    tampered with. Writes to exactly the path/filename ``LocalSubtitleProvider``
    scans, so the very next season-roster or download-subtitles pass sees it.
    """
    dest_dir = cache_dir / "data" / corpus_dir_name(tmdb_id, show_name)
    show_name_for_file = sanitize_filename(show_name) or "Unknown Show"

    outcomes: list[CommitFileOutcome] = []
    claimed: set[tuple[int, int]] = set()

    for f in files:
        if not _in_range(f.season, f.episode):
            outcomes.append(
                CommitFileOutcome(f.filename, f.season, f.episode, "error", "season/episode out of range")
            )
            continue

        key = (f.season, f.episode)
        if key in claimed:
            outcomes.append(
                CommitFileOutcome(f.filename, f.season, f.episode, "skipped", "duplicate within this batch")
            )
            continue

        if len(f.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            outcomes.append(CommitFileOutcome(f.filename, f.season, f.episode, "error", "file too large"))
            continue
        if not is_valid_srt_content(f.content):
            outcomes.append(CommitFileOutcome(f.filename, f.season, f.episode, "error", "not a valid SRT"))
            continue

        code = f"S{f.season:02d}E{f.episode:02d}"
        coverage = reference_coverage(cache_dir, tmdb_id, show_name, f.season, [f.episode])
        if coverage.get(code, "missing") != "missing":
            outcomes.append(CommitFileOutcome(f.filename, f.season, f.episode, "skipped", "already_covered"))
            claimed.add(key)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{show_name_for_file} - {code}.srt"
        dest_path.write_text(f.content, encoding="utf-8")
        claimed.add(key)
        logger.info(f"Imported manual subtitle for {code} -> {dest_path}")
        outcomes.append(CommitFileOutcome(f.filename, f.season, f.episode, "imported"))

    return outcomes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_manual_subtitle_import.py -v`
Expected: `PASS` — all tests green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/manual_subtitle_import.py backend/tests/unit/test_manual_subtitle_import.py
git commit -m "feat: add commit_files write logic for manual subtitle import"
```

---

### Task 4: API endpoints — `POST /jobs/{job_id}/subtitles/preview` and `/commit`

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/unit/test_api_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_api_routes.py` (uses the existing `client`, `_seed_config`, `_seed_job` fixtures already in this file):

```python
class TestManualSubtitleImport:
    """Tests for POST /jobs/{job_id}/subtitles/preview and /commit."""

    VALID_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"

    async def _seed_tv_job(self, tmp_path, **kwargs):
        await _seed_config(subtitles_cache_path=str(tmp_path))
        defaults = dict(tmdb_id=999, detected_title="Test Show", detected_season=1)
        defaults.update(kwargs)
        return await _seed_job(**defaults)

    async def test_preview_requires_identified_tv_job(self, client, tmp_path):
        job = await self._seed_job_movie(tmp_path)
        response = await client.post(
            f"/api/jobs/{job.id}/subtitles/preview",
            json={"files": [{"filename": "x.srt", "content": self.VALID_SRT}]},
        )
        assert response.status_code == 400

    async def _seed_job_movie(self, tmp_path):
        await _seed_config(subtitles_cache_path=str(tmp_path))
        return await _seed_job(content_type=ContentType.MOVIE, tmdb_id=None, detected_season=None)

    async def test_preview_classifies_ready_file(self, client, tmp_path):
        job = await self._seed_tv_job(tmp_path)
        response = await client.post(
            f"/api/jobs/{job.id}/subtitles/preview",
            json={"files": [{"filename": "Test.Show.S01E05.srt", "content": self.VALID_SRT}]},
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["season"] == 1
        assert results[0]["episode"] == 5
        assert results[0]["status"] == "ready"

    async def test_preview_rejects_too_many_files(self, client, tmp_path):
        job = await self._seed_tv_job(tmp_path)
        files = [{"filename": f"Test.Show.S01E{i:02d}.srt", "content": self.VALID_SRT} for i in range(1, 62)]
        response = await client.post(f"/api/jobs/{job.id}/subtitles/preview", json={"files": files})
        assert response.status_code == 400

    async def test_commit_writes_file_and_reports_imported(self, client, tmp_path):
        job = await self._seed_tv_job(tmp_path)
        response = await client.post(
            f"/api/jobs/{job.id}/subtitles/commit",
            json={
                "files": [
                    {"filename": "x.srt", "season": 1, "episode": 5, "content": self.VALID_SRT},
                ]
            },
        )
        assert response.status_code == 200
        outcomes = response.json()["outcomes"]
        assert outcomes[0]["status"] == "imported"
        dest = tmp_path / "data" / "999" / "Test Show - S01E05.srt"
        assert dest.exists()

    async def test_commit_requires_identified_tv_job(self, client, tmp_path):
        job = await self._seed_job_movie(tmp_path)
        response = await client.post(
            f"/api/jobs/{job.id}/subtitles/commit",
            json={"files": [{"filename": "x.srt", "season": 1, "episode": 1, "content": self.VALID_SRT}]},
        )
        assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py::TestManualSubtitleImport -v`
Expected: `FAIL` — `404 Not Found` (routes don't exist yet).

- [ ] **Step 3: Add the Pydantic models and endpoints**

In `backend/app/api/routes.py`, add this import alongside the existing matcher imports near the top of the file (next to the `episode_identification` import at line 36):

```python
from app.matcher.manual_subtitle_import import (
    MAX_FILES_PER_BATCH,
    CommitInputFile,
    PreviewInputFile,
    classify_files,
    commit_files,
)
```

Then add the models and endpoints. Place them right after `get_season_roster` (after line 858, before `build_job_detail`):

```python
class ManualSubtitleFileIn(BaseModel):
    """One file in a manual-subtitle preview request, as read client-side."""

    filename: str
    content: str


class ManualSubtitlePreviewRequest(BaseModel):
    files: list[ManualSubtitleFileIn]


class ManualSubtitlePreviewResult(BaseModel):
    filename: str
    season: int | None = None
    episode: int | None = None
    status: Literal["ready", "already_covered", "unparseable", "invalid_content", "duplicate"]
    warning: str | None = None


class ManualSubtitlePreviewResponse(BaseModel):
    results: list[ManualSubtitlePreviewResult]


class ManualSubtitleCommitFileIn(BaseModel):
    filename: str
    season: int
    episode: int
    content: str


class ManualSubtitleCommitRequest(BaseModel):
    files: list[ManualSubtitleCommitFileIn]


class ManualSubtitleCommitOutcome(BaseModel):
    filename: str
    season: int
    episode: int
    status: Literal["imported", "skipped", "error"]
    reason: str | None = None


class ManualSubtitleCommitResponse(BaseModel):
    outcomes: list[ManualSubtitleCommitOutcome]


def _require_identified_tv_job(job: DiscJob) -> None:
    if job.content_type != ContentType.TV or not job.tmdb_id or not job.detected_title:
        raise HTTPException(
            status_code=400, detail="Job must be an identified TV show to import manual subtitles"
        )


@router.post("/jobs/{job_id}/subtitles/preview", response_model=ManualSubtitlePreviewResponse)
async def preview_manual_subtitles(
    request: ManualSubtitlePreviewRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> ManualSubtitlePreviewResponse:
    """Classify a batch of user-supplied .srt files before import.

    Read-only — does not write anything. See ``classify_files`` for the
    per-file status logic (ready / already_covered / unparseable /
    invalid_content / duplicate).
    """
    _require_identified_tv_job(job)
    if len(request.files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_BATCH})")

    from app.services.config_service import get_config

    config = await get_config()
    cache_dir = Path(config.subtitles_cache_path).expanduser()

    results = await asyncio.to_thread(
        classify_files,
        cache_dir,
        job.tmdb_id,
        job.detected_title,
        [PreviewInputFile(filename=f.filename, content=f.content) for f in request.files],
    )
    return ManualSubtitlePreviewResponse(
        results=[
            ManualSubtitlePreviewResult(
                filename=r.filename, season=r.season, episode=r.episode, status=r.status, warning=r.warning
            )
            for r in results
        ]
    )


@router.post("/jobs/{job_id}/subtitles/commit", response_model=ManualSubtitleCommitResponse)
async def commit_manual_subtitles(
    request: ManualSubtitleCommitRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> ManualSubtitleCommitResponse:
    """Write the user-confirmed subset of previewed files into the subtitle
    cache. Re-validates independently of the preview step (see ``commit_files``).
    """
    _require_identified_tv_job(job)
    if len(request.files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_BATCH})")

    from app.services.config_service import get_config

    config = await get_config()
    cache_dir = Path(config.subtitles_cache_path).expanduser()

    outcomes = await asyncio.to_thread(
        commit_files,
        cache_dir,
        job.tmdb_id,
        job.detected_title,
        [
            CommitInputFile(filename=f.filename, season=f.season, episode=f.episode, content=f.content)
            for f in request.files
        ],
    )
    return ManualSubtitleCommitResponse(
        outcomes=[
            ManualSubtitleCommitOutcome(
                filename=o.filename, season=o.season, episode=o.episode, status=o.status, reason=o.reason
            )
            for o in outcomes
        ]
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py::TestManualSubtitleImport -v`
Expected: `PASS` — all 5 tests green.

Also run the full unit suite to confirm nothing broke:

Run: `cd backend && uv run pytest tests/unit -q`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_api_routes.py
git commit -m "feat: add manual subtitle preview/commit API endpoints"
```

---

### Task 5: Integration test — prove the matcher needs no changes

**Files:**
- Modify: `backend/tests/integration/test_subtitle_workflow.py`

- [ ] **Step 1: Write the test**

Add to `backend/tests/integration/test_subtitle_workflow.py`. This test calls `commit_files`/`LocalSubtitleProvider` directly with `tmp_path` as the cache dir — it doesn't go through the API or `AppConfig`, so none of the file's existing `config.subtitles_cache_path` mocking is needed here:

```python
from app.matcher.manual_subtitle_import import CommitInputFile, commit_files
from app.matcher.subtitle_provider import LocalSubtitleProvider


class TestManualSubtitleFeedsLocalProvider:
    """Proves commit_files writes to exactly the path LocalSubtitleProvider
    scans — the whole point of this feature is that no matcher code needs to
    change for a manually imported subtitle to be used.
    """

    def test_committed_file_is_returned_by_local_provider(self, tmp_path):
        content = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"
        outcomes = commit_files(
            tmp_path, 555, "Manual Import Show",
            [CommitInputFile(filename="x.srt", season=1, episode=3, content=content)],
        )
        assert outcomes[0].status == "imported"

        provider = LocalSubtitleProvider(cache_dir=tmp_path)
        subs = provider.get_subtitles(show_name="Manual Import Show", season=1, tmdb_id=555)

        assert len(subs) == 1
        assert subs[0].episode_info.season == 1
        assert subs[0].episode_info.episode == 3
```

This task runs after Tasks 1-4, so `commit_files` already exists — this is not a red-green cycle over new production code, it's a regression-proof of the design's central claim (a manually committed file is indistinguishable from an automated find). It should pass on the first run; if it doesn't, that means `commit_files`' path construction has drifted from what `LocalSubtitleProvider` actually scans — fix `commit_files` in `backend/app/matcher/manual_subtitle_import.py` to match `LocalSubtitleProvider`, not the other way around.

- [ ] **Step 2: Run the test**

Run: `cd backend && uv run pytest tests/integration/test_subtitle_workflow.py::TestManualSubtitleFeedsLocalProvider -v`
Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_subtitle_workflow.py
git commit -m "test: prove manual subtitle import needs no matcher changes"
```

---

### Task 6: Frontend API client functions

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Add the types and functions**

No test file exists for `client.ts`'s thin fetch wrappers (consistent with the rest of the file — `reassignEpisode`, `rematchTitle`, etc. have no dedicated unit tests; they're exercised indirectly through the component that calls them, added in Task 7). Append to `frontend/src/api/client.ts`, after the `unskipRipTitle` function (after line 203):

```typescript
// ---------------------------------------------------------------------------
// Manual subtitle import
// ---------------------------------------------------------------------------

export interface ManualSubtitleFileIn {
  filename: string;
  content: string;
}

export type ManualSubtitlePreviewStatus =
  | 'ready'
  | 'already_covered'
  | 'unparseable'
  | 'invalid_content'
  | 'duplicate';

export interface ManualSubtitlePreviewResult {
  filename: string;
  season: number | null;
  episode: number | null;
  status: ManualSubtitlePreviewStatus;
  warning?: string | null;
}

/** Classify a batch of client-read .srt files without writing anything. */
export async function previewManualSubtitles(
  jobId: number,
  files: ManualSubtitleFileIn[],
): Promise<ManualSubtitlePreviewResult[]> {
  const res = await apiFetch<{ results: ManualSubtitlePreviewResult[] }>(
    `/api/jobs/${jobId}/subtitles/preview`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    },
  );
  return res.results;
}

export interface ManualSubtitleCommitFileIn {
  filename: string;
  season: number;
  episode: number;
  content: string;
}

export type ManualSubtitleCommitStatus = 'imported' | 'skipped' | 'error';

export interface ManualSubtitleCommitOutcome {
  filename: string;
  season: number;
  episode: number;
  status: ManualSubtitleCommitStatus;
  reason?: string | null;
}

/** Write the user-confirmed subset of previewed files into the subtitle cache. */
export async function commitManualSubtitles(
  jobId: number,
  files: ManualSubtitleCommitFileIn[],
): Promise<ManualSubtitleCommitOutcome[]> {
  const res = await apiFetch<{ outcomes: ManualSubtitleCommitOutcome[] }>(
    `/api/jobs/${jobId}/subtitles/commit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    },
  );
  return res.outcomes;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run build`
Expected: TypeScript compiles with no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: add previewManualSubtitles/commitManualSubtitles API client functions"
```

---

### Task 7: `SubtitleUploadModal` component

**Files:**
- Create: `frontend/src/components/ReviewQueue/SubtitleUploadModal.tsx`
- Test: `frontend/src/components/ReviewQueue/SubtitleUploadModal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ReviewQueue/SubtitleUploadModal.test.tsx`:

```typescript
import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { SubtitleUploadModal } from './SubtitleUploadModal';
import * as client from '../../api/client';

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof client>('../../api/client');
  return { ...actual, previewManualSubtitles: vi.fn(), commitManualSubtitles: vi.fn() };
});

function makeFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/plain' });
}

describe('SubtitleUploadModal', () => {
  beforeEach(() => {
    vi.mocked(client.previewManualSubtitles).mockReset();
    vi.mocked(client.commitManualSubtitles).mockReset();
  });

  it('opens the modal and previews selected files', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'ready' },
    ]);
    const onImported = vi.fn();
    render(<SubtitleUploadModal jobId={7} onImported={onImported} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, { target: { files: [makeFile('Show.S01E05.srt', '1\n00:00:01,000 --> 00:00:02,000\nHi\n')] } });

    await waitFor(() => expect(client.previewManualSubtitles).toHaveBeenCalledWith(7, [
      { filename: 'Show.S01E05.srt', content: '1\n00:00:01,000 --> 00:00:02,000\nHi\n' },
    ]));
    expect(await screen.findByText('S01E05')).toBeInTheDocument();
  });

  it('commits confirmed files and calls onImported', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'ready' },
    ]);
    vi.mocked(client.commitManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'imported' },
    ]);
    const onImported = vi.fn();
    render(<SubtitleUploadModal jobId={7} onImported={onImported} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, { target: { files: [makeFile('Show.S01E05.srt', '1\n00:00:01,000 --> 00:00:02,000\nHi\n')] } });
    await screen.findByText('S01E05');

    fireEvent.click(screen.getByRole('button', { name: /import/i }));

    await waitFor(() => expect(client.commitManualSubtitles).toHaveBeenCalledWith(7, [
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, content: '1\n00:00:01,000 --> 00:00:02,000\nHi\n' },
    ]));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
  });

  it('does not preselect an already-covered file for import', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E02.srt', season: 1, episode: 2, status: 'already_covered' },
    ]);
    render(<SubtitleUploadModal jobId={7} onImported={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, { target: { files: [makeFile('Show.S01E02.srt', 'content')] } });

    const checkbox = await screen.findByRole('checkbox', { name: /S01E02/i });
    expect(checkbox).not.toBeChecked();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test:unit -- SubtitleUploadModal`
Expected: `FAIL` — module doesn't exist yet.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/ReviewQueue/SubtitleUploadModal.tsx`:

```typescript
import { useRef, useState } from 'react';
import { SvActionButton, SvPanel, sv } from '../../app/components/synapse';
import {
  previewManualSubtitles,
  commitManualSubtitles,
  type ManualSubtitlePreviewStatus,
} from '../../api/client';

interface Row {
  filename: string;
  content: string;
  season: number | null;
  episode: number | null;
  status: ManualSubtitlePreviewStatus;
  warning?: string | null;
  checked: boolean;
}

const STATUS_LABEL: Record<ManualSubtitlePreviewStatus, string> = {
  ready: 'Ready to import',
  already_covered: 'Already has a reference (skipped)',
  unparseable: 'Could not detect episode — enter season/episode',
  invalid_content: 'Not a valid subtitle file',
  duplicate: 'Duplicate of another file in this batch',
};

function rowLabel(r: Row): string {
  return r.season != null && r.episode != null
    ? `S${String(r.season).padStart(2, '0')}E${String(r.episode).padStart(2, '0')}`
    : r.filename;
}

async function readFilesAsText(fileList: FileList): Promise<{ filename: string; content: string }[]> {
  return Promise.all(
    Array.from(fileList)
      .filter((f) => f.name.toLowerCase().endsWith('.srt'))
      .map(
        (f) =>
          new Promise<{ filename: string; content: string }>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve({ filename: f.name, content: String(reader.result ?? '') });
            reader.onerror = () => reject(reader.error);
            reader.readAsText(f);
          }),
      ),
  );
}

export function SubtitleUploadModal({
  jobId,
  onImported,
}: {
  jobId: number;
  onImported: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const openPicker = () => {
    setOpen(true);
    setRows([]);
    setError(null);
  };

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const read = await readFilesAsText(fileList);
      const contentByName = new Map(read.map((f) => [f.filename, f.content]));
      const results = await previewManualSubtitles(jobId, read);
      setRows(
        results.map((r) => ({
          filename: r.filename,
          content: contentByName.get(r.filename) ?? '',
          season: r.season,
          episode: r.episode,
          status: r.status,
          warning: r.warning,
          checked: r.status === 'ready',
        })),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to preview files');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const setRowOverride = (filename: string, field: 'season' | 'episode', value: string) => {
    const num = value === '' ? null : Number(value);
    setRows((prev) =>
      prev.map((r) => {
        if (r.filename !== filename) return r;
        const next = { ...r, [field]: num };
        next.checked = next.season != null && next.episode != null && r.status !== 'invalid_content';
        return next;
      }),
    );
  };

  const toggleRow = (filename: string) => {
    setRows((prev) => prev.map((r) => (r.filename === filename ? { ...r, checked: !r.checked } : r)));
  };

  const handleImport = async () => {
    const toImport = rows.filter((r) => r.checked && r.season != null && r.episode != null);
    if (toImport.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await commitManualSubtitles(
        jobId,
        toImport.map((r) => ({
          filename: r.filename,
          season: r.season as number,
          episode: r.episode as number,
          content: r.content,
        })),
      );
      setOpen(false);
      setRows([]);
      onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import subtitles');
    } finally {
      setBusy(false);
    }
  };

  const importCount = rows.filter((r) => r.checked).length;

  return (
    <>
      <SvActionButton tone="cyan" size="sm" onClick={openPicker}>
        Upload Subtitles
      </SvActionButton>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: `${sv.bg0}d9`,
          }}
        >
          <SvPanel glow pad={20} style={{ width: '100%', maxWidth: 640, maxHeight: '80vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ fontFamily: sv.mono, fontSize: 13, fontWeight: 700, color: sv.cyanHi, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                Upload Subtitles
              </div>

              <input
                ref={(el) => {
                  inputRef.current = el;
                  if (el) el.setAttribute('webkitdirectory', '');
                }}
                data-testid="subtitle-upload-input"
                type="file"
                multiple
                accept=".srt"
                onChange={handleFiles}
                style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}
              />

              {error && <div style={{ color: sv.red, fontFamily: sv.mono, fontSize: 11 }}>{error}</div>}

              {rows.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {rows.map((r) => (
                    <div
                      key={r.filename}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '6px 8px',
                        border: `1px solid ${sv.lineMid}`,
                        fontFamily: sv.mono,
                        fontSize: 11,
                      }}
                    >
                      <input
                        type="checkbox"
                        aria-label={rowLabel(r)}
                        checked={r.checked}
                        disabled={r.status === 'invalid_content'}
                        onChange={() => toggleRow(r.filename)}
                      />
                      <span style={{ minWidth: 64, color: sv.cyanHi, fontWeight: 700 }}>{rowLabel(r)}</span>
                      <span style={{ flex: 1, color: sv.inkDim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.filename}
                      </span>
                      {r.status === 'unparseable' ? (
                        <>
                          <input
                            type="number"
                            placeholder="S"
                            style={{ width: 44 }}
                            onChange={(e) => setRowOverride(r.filename, 'season', e.target.value)}
                          />
                          <input
                            type="number"
                            placeholder="E"
                            style={{ width: 44 }}
                            onChange={(e) => setRowOverride(r.filename, 'episode', e.target.value)}
                          />
                        </>
                      ) : (
                        <span style={{ color: r.status === 'ready' ? sv.green : sv.inkFaint }}>
                          {STATUS_LABEL[r.status]}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                <SvActionButton tone="neutral" size="md" onClick={() => setOpen(false)} disabled={busy}>
                  Cancel
                </SvActionButton>
                <SvActionButton
                  tone="cyan"
                  size="md"
                  onClick={handleImport}
                  disabled={busy || importCount === 0}
                >
                  {`Import (${importCount})`}
                </SvActionButton>
              </div>
            </div>
          </SvPanel>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm run test:unit -- SubtitleUploadModal`
Expected: `PASS` — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue/SubtitleUploadModal.tsx frontend/src/components/ReviewQueue/SubtitleUploadModal.test.tsx
git commit -m "feat: add SubtitleUploadModal component for bulk subtitle import"
```

---

### Task 8: Wire the modal into ReviewQueue

**Files:**
- Modify: `frontend/src/components/ReviewQueue.tsx:1,1026-1046`

- [ ] **Step 1: Add the import**

In `frontend/src/components/ReviewQueue.tsx`, add to the import block (near line 21, after the `DamagedTrackNotice` import):

```typescript
import { SubtitleUploadModal } from './ReviewQueue/SubtitleUploadModal';
```

- [ ] **Step 2: Render the trigger next to the missing-reference warning**

In the same file, the missing-reference warning block currently reads (lines 1026-1046):

```typescript
                        {missingRefCodes.length > 0 && (
                            <div
                                style={{
                                    marginBottom: 10,
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 7,
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    lineHeight: 1.4,
                                    color: sv.red,
                                }}
                            >
                                <IcoError size={13} color={sv.red} title="No reference subtitle" />
                                <span>
                                    {missingRefCodes.length === 1
                                        ? `${missingRefCodes[0]} has no reference subtitle — matching can't auto-identify it; assign manually.`
                                        : `${missingRefCodes.length} episodes have no reference subtitle (${missingRefCodes.join(', ')}) — matching can't auto-identify them; assign manually.`}
                                </span>
                            </div>
                        )}
```

Replace it with (adds the upload trigger and a `justify-content: space-between` layout so the button sits at the end of the warning row; wires `onImported` to the existing `reloadRoster`):

```typescript
                        {missingRefCodes.length > 0 && (
                            <div
                                style={{
                                    marginBottom: 10,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between',
                                    gap: 7,
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    lineHeight: 1.4,
                                    color: sv.red,
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                                    <IcoError size={13} color={sv.red} title="No reference subtitle" />
                                    <span>
                                        {missingRefCodes.length === 1
                                            ? `${missingRefCodes[0]} has no reference subtitle — matching can't auto-identify it; assign manually.`
                                            : `${missingRefCodes.length} episodes have no reference subtitle (${missingRefCodes.join(', ')}) — matching can't auto-identify them; assign manually.`}
                                    </span>
                                </div>
                                {jobId && (
                                    <SubtitleUploadModal jobId={Number(jobId)} onImported={reloadRoster} />
                                )}
                            </div>
                        )}
```

`jobId` is declared at `frontend/src/components/ReviewQueue.tsx:161` as `const { jobId } = useParams<{ jobId: string }>();` — i.e. `string | undefined`. The `jobId &&` guard above handles the `undefined`/empty-string case (the review page can't reach this code path without a route param anyway, since `roster` would never have loaded), and `Number(jobId)` converts the route string to the `number` the new component expects.

- [ ] **Step 3: Typecheck and lint**

Run: `cd frontend && npm run build`
Expected: compiles clean.

Run: `cd frontend && npm run lint`
Expected: no new errors.

- [ ] **Step 4: Manual verification in the browser**

Start both servers per this repo's parallel-session convention (`CLAUDE.md` — distinct DB/port if another session is running), with `DEBUG=true` on the backend so simulation endpoints are available:

```bash
# backend, from backend/
uv run uvicorn app.main:app --port 8000
```
```bash
# frontend, from frontend/
npm run dev
```

Then:
1. Simulate a TV disc insertion with ripping (`POST /api/simulate/insert-disc` per `CLAUDE.md`), advance it into `review_needed`.
2. Open the review page for that job. If the simulated show has any episode without a reference subtitle, the red warning banner should now show the "Upload Subtitles" button.
3. Click it, select a small real (or hand-written) `.srt` file, confirm it shows up as `ready` in the table, click Import, and confirm the warning banner updates (episode count drops or the banner disappears) without a full page reload.
4. Check the backend log (or the file on disk under the configured `subtitles_cache_path`) to confirm the file landed at `data/<tmdb_id>/<Show Name> - SxxExx.srt`.

If step 2's simulated disc doesn't naturally produce a missing-reference episode, seed one directly: after the job reaches `review_needed`, call `GET /api/jobs/{job_id}/season-roster` to find the job's `tmdb_id`, and note that any episode TMDB returns for that season with no cached reference will show `has_reference: false` — the exact episode TMDB happens to return depends on the live TMDB API response for the simulated show, so this step requires eyeballing the roster response rather than a fixed episode code.

Stop both servers when done, per `CLAUDE.md`'s "Important Rules".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue.tsx
git commit -m "feat: wire SubtitleUploadModal into the review page's reference warning"
```

---

## Post-plan self-review notes

- **Spec coverage:** every section of the design doc maps to a task — reuse of `LocalSubtitleProvider`'s existing scan (Tasks 2/3/5), preview/commit split (Task 4), no-multipart JSON upload (Tasks 6/7), season-roster hook point (Task 8), size/batch limits and duplicate/encoding handling (Task 2/3 tests), commit-time re-validation (Task 3 tests).
- **Not covered by this plan, deliberately (per spec's explicit non-goals):** auto-triggering re-match after import (user uses the existing advisory re-match action), non-`.srt` formats, a global upload screen decoupled from a job.
- **Known follow-up, not blocking:** `webkitdirectory` gives folder-picking UX only in Chromium-based browsers; Firefox/Safari fall back to ordinary multi-file selection. Acceptable per the spec (folder-vs-files was a UX nicety, not a hard requirement) but worth a one-line mention if a user reports Safari "won't let me pick a folder."

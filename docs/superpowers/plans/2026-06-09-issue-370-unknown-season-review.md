# Issue #370: Unknown-Season Disc Review Dead-End — Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four stacked bugs behind [issue #370](https://github.com/Jsakkos/engram/issues/370) — a disc labeled by disc number only (`Eureka D3`) never detects a season, which silently skipped subtitle download, fired a false "different same-named show" advisory, locked the review dropdown to Season 1, and poisoned the reference cache. **v2 design decision (owner):** instead of automatically brute-forcing all seasons downstream, an unknown-season TV disc parks in `REVIEW_NEEDED` at identification time and a dashboard modal asks the user to pick the season — stemming every downstream issue (N-season subtitle downloads, multi-hour cross-season ASR, garbage review states) before it starts.

**Architecture:** The season prompt rides the existing unreadable-label machinery end to end: `REVIEW_NEEDED` + `review_reason` marker → dashboard effect → modal → `POST /api/jobs/{id}/set-name` → `set_name_and_resume` → `RIPPING`. A new `_start_tv_subtitle_prefetch` helper (single-season when known, all-seasons fallback otherwise) is called from the normal disc path, the staging-import path, AND `set_name_and_resume` — the latter closes a pre-existing gap where user-named discs never started a subtitle download at all. The modal offers "match across all seasons" as the automation escape hatch (resumes with season unset → all-seasons prefetch, keyed by `tmdb_id`). Defense-in-depth fixes stay: the wrong-show advisory is gated on a delivered subtitle corpus, an honest "no reference subtitles" review reason replaces the generic one, the review page gets a season picker as a backstop, and empty reference lookups are no longer cached.

**Tech Stack:** Python/FastAPI + SQLModel (backend), pytest unit tests, React/TypeScript + vitest (frontend).

**Verified root-cause chain (job 3, Eureka D3, Engram 0.17.0):**

| # | Bug | Location |
|---|-----|----------|
| 1 | Subtitle download gated on `job.detected_season` truthy on the disc path — silently skipped, job proceeded to doomed matching | `backend/app/services/identification_coordinator.py:421-433` |
| 2 | `_detect_wrong_show` fires on all-unmatched + same-name twin even when zero reference subtitles ever existed → "did you mean Eureka! (2022)?" | `backend/app/services/finalization_coordinator.py:26-62` |
| 3 | `/season-roster` returns `available:false` when `detected_season is None`; Inspector falls back to `generateEpisodeOptions(detected_season \|\| 1, 24)` → S01-only dropdown | `backend/app/api/routes.py:671`, `frontend/src/components/ReviewQueue/Inspector.tsx:334-336` |
| 4 | `get_reference_files` caches an empty list and returns it as a hit forever after | `backend/app/matcher/episode_identification.py:1143` |

**Key facts for the implementer:**
- `job.subtitle_status` values written by the pipeline: `"downloading"`, `"completed"`, `"partial"` (single-season only), `"failed"`, or `None` (never started — job 3's case). `subtitles_downloaded`/`subtitles_total` on `DiscJob` are **never written** (vestigial, always 0) — do NOT gate on them.
- The unreadable-label prompt flow (the template for the season prompt): dashboard effect in `frontend/src/app/App.tsx:152-161` matches `review_reason` substrings → `NamePromptModal` → `setJobName` (`frontend/src/app/hooks/useJobManagement.ts:239`) → `POST /api/jobs/{id}/set-name` (`backend/app/api/routes.py:953`) → `set_name_and_resume` (`backend/app/services/identification_coordinator.py:893`, wrapper `job_manager.py:665` spawns `_run_ripping`). Modal exits must change job state (submit or cancel-job) — the effect re-runs on every jobs update, so a merely-dismissed modal would reappear.
- **Pre-existing gap closed here:** `set_name_and_resume` never starts a subtitle download (only `re_identify` restarts one) — user-named TV discs relied on whatever references happened to be cached locally.
- `set_name_and_resume` only assigns `detected_season` when `season is not None` — so the modal's "all seasons" choice (season omitted) flows through naturally.
- `testing_service.download_subtitles(show_name, season, *, tmdb_id=None, use_precomputed=True)` already accepts `tmdb_id`; the all-seasons wrapper just doesn't pass it yet.
- Unit tests use `_unit_session_factory` from `tests/unit/conftest.py` (isolated DB) — the worktree's 0-byte `backend/engram.db` stub does not matter for them.
- Frontend worktree needs `npm install` before vitest/build; afterwards run `git checkout package-lock.json` (the committed lock is stale and install rewrites it — do not commit that diff).
- All backend commands run from `backend/`, frontend from `frontend/`. Always `uv run …`, never bare python/pytest.
- Work on the current worktree branch (`claude/admiring-dhawan-55ceb1`); PR title should reference #370.

**Execution order matters:** Task 2 before Task 3 (the helper passes `tmdb_id` to the all-seasons starter); Task 5 before Task 6 (the modal reads `season_count` from the roster endpoint).

---

### Task 1: Stop caching empty reference-file lookups (Fix 4)

**Files:**
- Modify: `backend/app/matcher/episode_identification.py:1140-1144`
- Test: `backend/tests/unit/test_reference_files_cache.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_reference_files_cache.py`:

```python
"""get_reference_files must not cache an empty corpus (#370).

Job 3 (Eureka D3): the subtitle download never ran, the first lookup cached
[], and every later probe logged "Returning cached reference files" followed
by the no-references ERROR — even after subtitles could have been retried.
An empty result must stay a cache miss so late-arriving references become
visible to re-matches within the same process.
"""

import pytest

from app.matcher.episode_identification import EpisodeMatcher
from app.matcher.subtitle_utils import corpus_dir_name


def _matcher(tmp_path):
    """Minimal EpisodeMatcher carrying only what get_reference_files reads.

    __new__ skips the heavyweight __init__ (model registry, config, TMDB).
    """
    m = EpisodeMatcher.__new__(EpisodeMatcher)
    m.cache_dir = tmp_path
    m.show_name = "Eureka"
    m.expected_tmdb_id = 4620
    m.reference_files_cache = {}
    return m


def _add_reference(tmp_path, filename):
    ref_dir = tmp_path / "data" / corpus_dir_name(4620, "Eureka")
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / filename).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")


@pytest.mark.unit
class TestEmptyReferenceCacheNotPoisoned:
    def test_empty_result_is_not_cached(self, tmp_path):
        m = _matcher(tmp_path)
        assert m.get_reference_files(1) == []
        assert m.reference_files_cache == {}

    def test_late_arriving_references_are_picked_up(self, tmp_path):
        m = _matcher(tmp_path)
        assert m.get_reference_files(1) == []  # nothing yet — must not poison

        _add_reference(tmp_path, "Eureka - S01E01.srt")

        files = m.get_reference_files(1)
        assert [f.name for f in files] == ["Eureka - S01E01.srt"]

    def test_non_empty_result_is_cached(self, tmp_path):
        m = _matcher(tmp_path)
        _add_reference(tmp_path, "Eureka - S01E01.srt")

        first = m.get_reference_files(1)
        assert len(first) == 1
        assert m.reference_files_cache[("Eureka", 1)] == first
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `uv run pytest tests/unit/test_reference_files_cache.py -v`
Expected: `test_late_arriving_references_are_picked_up` and `test_empty_result_is_not_cached` FAIL (empty list currently cached); `test_non_empty_result_is_cached` PASSES.

- [ ] **Step 3: Implement — skip caching empty results**

In `backend/app/matcher/episode_identification.py`, replace (currently lines 1140-1144):

```python
        # Remove duplicates while preserving order
        reference_files = list(dict.fromkeys(reference_files))
        logger.debug(f"Found {len(reference_files)} reference files for season {season_number}")
        self.reference_files_cache[cache_key] = reference_files
        return reference_files
```

with:

```python
        # Remove duplicates while preserving order
        reference_files = list(dict.fromkeys(reference_files))
        logger.debug(f"Found {len(reference_files)} reference files for season {season_number}")
        # Never cache an EMPTY corpus: references can arrive later in this
        # process's lifetime (retry-subtitles, mid-job download), and a cached
        # empty hit would mask them for every subsequent match (#370).
        if reference_files:
            self.reference_files_cache[cache_key] = reference_files
        return reference_files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_reference_files_cache.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/episode_identification.py backend/tests/unit/test_reference_files_cache.py
git commit -m "fix(matcher): don't poison the reference-file cache with empty lookups (#370)"
```

---

### Task 2: Plumb `tmdb_id` through the all-seasons subtitle download (Fix 1a)

The all-seasons downloader currently resolves the show by name inside `testing_service.download_subtitles`, which can pick the wrong same-name twin (Eureka 2006 vs Eureka! 2022) and key the cache wrongly. Callers that know `job.tmdb_id` must pass it through.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py:241-248` (starter) and `:1706-1741` (downloader)
- Test: `backend/tests/unit/test_matching_coordinator.py` (extend `TestDownloadSubtitlesAllSeasons`)

- [ ] **Step 1: Write the failing test**

In `backend/tests/unit/test_matching_coordinator.py`, add to class `TestDownloadSubtitlesAllSeasons` (after `test_sets_subtitle_ready_event`, ~line 592):

```python
    async def test_passes_tmdb_id_to_each_season_download(self, monkeypatch):
        """Callers that know the show's tmdb_id must key every per-season download
        by it, or the name-resolver can pick a same-name twin (#370)."""
        coord = _make_coord()
        seen: list[tuple[int, int | None]] = []

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _noop)

        def fake_download(show, season, tmdb_id=None):
            seen.append((season, tmdb_id))
            return {"episodes": [{"status": "downloaded"}], "show_name": show}

        monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
        async with _unit_session_factory() as session:
            job, _t = await _seed(session)
            job_id = job.id
        coord._subtitle_ready[job_id] = asyncio.Event()

        await coord.download_subtitles_all_seasons(job_id, "Eureka", [1, 2], tmdb_id=4620)

        assert seen == [(1, 4620), (2, 4620)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_matching_coordinator.py::TestDownloadSubtitlesAllSeasons::test_passes_tmdb_id_to_each_season_download -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'tmdb_id'`.

- [ ] **Step 3: Implement — add the parameter to both methods**

In `backend/app/services/matching_coordinator.py`, replace the starter (lines 241-248):

```python
    def start_subtitle_download_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int]
    ) -> None:
        """Start a background download spanning multiple seasons (unknown-season import)."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles_all_seasons(job_id, show_name, seasons)
        )
```

with:

```python
    def start_subtitle_download_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int], tmdb_id: int | None = None
    ) -> None:
        """Start a background download spanning multiple seasons (unknown season)."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles_all_seasons(job_id, show_name, seasons, tmdb_id=tmdb_id)
        )
```

Then in `download_subtitles_all_seasons` (line 1706), change the signature:

```python
    async def download_subtitles_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int]
    ) -> None:
```

to:

```python
    async def download_subtitles_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int], tmdb_id: int | None = None
    ) -> None:
```

and the per-season call (line 1741):

```python
                    result = await asyncio.to_thread(download_subtitles, show_name, season)
```

to:

```python
                    result = await asyncio.to_thread(
                        download_subtitles, show_name, season, tmdb_id=tmdb_id
                    )
```

- [ ] **Step 4: Run the class to verify all pass (existing tests must not break)**

Run: `uv run pytest tests/unit/test_matching_coordinator.py::TestDownloadSubtitlesAllSeasons -v`
Expected: all PASSED (existing tests' `lambda show, season, tmdb_id=None: …` mocks already tolerate the kwarg).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "fix(subtitles): key all-seasons downloads by tmdb_id, not name re-resolution (#370)"
```

---

### Task 3: Season gate at identification + subtitle prefetch on every resume path (Fix 1b)

Three changes in `identification_coordinator.py`:
1. `_resolve_all_season_numbers` learns to use a known `tmdb_id` instead of name-resolving.
2. A new `_start_tv_subtitle_prefetch(job)` helper — single-season download when `detected_season` is set, all-seasons prefetch otherwise — used by the disc path, the staging-import path, and `set_name_and_resume`.
3. The disc path's unknown-season case now routes to `REVIEW_NEEDED` with a "select a season" reason (the modal trigger), auto-pinning season 1 first when the show only HAS one season. The staging-import path keeps its automatic all-seasons behavior (flat import folders genuinely span seasons; that flow already shipped).

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:511-528` (`_resolve_all_season_numbers`), `:417-433` (disc path), `:766-784` (staging path), `:893-934` (`set_name_and_resume`); add the helper after `_resolve_all_season_numbers`
- Test: `backend/tests/unit/test_identification_subtitle_prefetch.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_identification_subtitle_prefetch.py`:

```python
"""TV subtitle prefetch + unknown-season handling (#370).

A disc labeled by disc number only ("Eureka D3") identifies the show but not
the season. The disc path used to gate subtitle download on detected_season,
silently skipping it — zero reference subtitles, every title failed matching
at confidence 0, and the whole disc dead-ended in review. v2 design: the job
parks in REVIEW_NEEDED for a season pick; the shared _start_tv_subtitle_prefetch
helper covers the season-known (single download) and season-unknown ("match
across all seasons" escape hatch) resume paths, keyed by the job's tmdb_id.
"""

from unittest.mock import MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator
from tests.unit.conftest import _unit_session_factory


def _coord():
    """Bare coordinator: __new__ skips the heavyweight __init__ wiring."""
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
    coord._start_subtitle_download = Mock()
    coord._start_subtitle_download_all_seasons = Mock()
    return coord


def _tv_job(season):
    job = DiscJob(
        drive_id="D:",
        volume_label="EUREKA_D3",
        content_type=ContentType.TV,
        detected_title="Eureka",
        detected_season=season,
        tmdb_id=4620,
    )
    job.id = 7
    return job


@pytest.mark.unit
class TestStartTvSubtitlePrefetch:
    async def test_known_season_downloads_that_season_only(self):
        coord = _coord()

        await coord._start_tv_subtitle_prefetch(_tv_job(season=2))

        coord._start_subtitle_download.assert_called_once_with(7, "Eureka", 2, 4620)
        coord._start_subtitle_download_all_seasons.assert_not_called()

    async def test_unknown_season_prefetches_all_seasons_by_tmdb_id(self):
        coord = _coord()
        captured = {}

        async def fake_resolve(title, tmdb_id=None):
            captured["args"] = (title, tmdb_id)
            return [1, 2, 3, 4, 5]

        coord._resolve_all_season_numbers = fake_resolve

        await coord._start_tv_subtitle_prefetch(_tv_job(season=None))

        assert captured["args"] == ("Eureka", 4620)
        coord._start_subtitle_download_all_seasons.assert_called_once_with(
            7, "Eureka", [1, 2, 3, 4, 5], tmdb_id=4620
        )
        coord._start_subtitle_download.assert_not_called()

    async def test_unknown_season_unresolvable_show_starts_nothing(self):
        coord = _coord()

        async def fake_resolve(title, tmdb_id=None):
            return []

        coord._resolve_all_season_numbers = fake_resolve

        await coord._start_tv_subtitle_prefetch(_tv_job(season=None))

        coord._start_subtitle_download.assert_not_called()
        coord._start_subtitle_download_all_seasons.assert_not_called()


@pytest.mark.unit
class TestResolveAllSeasonNumbersTmdbId:
    async def test_uses_tmdb_id_directly_when_known(self, monkeypatch):
        """With the job's tmdb_id in hand, never re-resolve by name — that picks
        the dominant same-name twin (the Frasier-class bug)."""
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
        fetch_id = MagicMock(
            side_effect=AssertionError("must not name-resolve when tmdb_id is known")
        )
        seen = {}

        def fake_count(show_id):
            seen["show_id"] = show_id
            return 5

        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", fetch_id)
        monkeypatch.setattr("app.matcher.tmdb_client.get_number_of_seasons", fake_count)

        seasons = await coord._resolve_all_season_numbers("Eureka", tmdb_id=4620)

        assert seasons == [1, 2, 3, 4, 5]
        assert seen["show_id"] == "4620"
        fetch_id.assert_not_called()

    async def test_falls_back_to_name_resolution_without_tmdb_id(self, monkeypatch):
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda title: "4620")
        monkeypatch.setattr("app.matcher.tmdb_client.get_number_of_seasons", lambda sid: 3)

        seasons = await coord._resolve_all_season_numbers("Eureka")

        assert seasons == [1, 2, 3]


@pytest.mark.unit
class TestSetNameAndResumeStartsSubtitles:
    """set_name_and_resume never started a subtitle download (pre-existing gap,
    masked by locally-cached references). The season-prompt modal resumes through
    this path, so it must kick the prefetch — single-season for a picked season,
    all-seasons for the "match across all seasons" choice (season=None)."""

    @pytest.fixture(autouse=True)
    def _patch_session_and_ws(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.identification_coordinator.async_session", _unit_session_factory
        )

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)

    async def _seed_review_job(self):
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="D:",
                volume_label="EUREKA_D3",
                content_type=ContentType.TV,
                state=JobState.REVIEW_NEEDED,
                detected_title="Eureka",
                tmdb_id=4620,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id

    def _resumable_coord(self, prefetch_calls):
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)

        async def fake_resolve_tmdb(job):
            return None

        async def fake_prefetch(job):
            prefetch_calls.append((job.id, job.detected_season))

        coord._resolve_missing_tmdb_id = fake_resolve_tmdb
        coord._start_tv_subtitle_prefetch = fake_prefetch
        return coord

    async def test_picked_season_resumes_with_single_season_prefetch(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Eureka", "tv", season=3)

        assert prefetch_calls == [(job_id, 3)]
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.state == JobState.RIPPING
            assert job.detected_season == 3
            assert job.review_reason is None

    async def test_all_seasons_choice_resumes_with_unknown_season(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Eureka", "tv", season=None)

        # detected_season stays None -> the helper does the all-seasons prefetch.
        assert prefetch_calls == [(job_id, None)]

    async def test_movie_resume_does_not_prefetch(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Inception", "movie")

        assert prefetch_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_identification_subtitle_prefetch.py -v`
Expected: `TestStartTvSubtitlePrefetch` and `TestSetNameAndResumeStartsSubtitles` FAIL with `AttributeError: ... '_start_tv_subtitle_prefetch'` (helper missing / never called); `test_uses_tmdb_id_directly_when_known` FAILS with the AssertionError side effect. `test_falls_back_to_name_resolution_without_tmdb_id` may already pass.

- [ ] **Step 3: Implement — extend `_resolve_all_season_numbers` and add the helper**

In `backend/app/services/identification_coordinator.py`, replace `_resolve_all_season_numbers` (lines 511-528):

```python
    async def _resolve_all_season_numbers(self, title: str) -> list[int]:
        """Resolve 1..N season numbers for a show via TMDB (unknown-season import).

        Returns an empty list when the show can't be resolved; callers then rely on
        the precomputed cache / already-downloaded references during matching.
        """
        try:
            from app.matcher.tmdb_client import fetch_show_id, get_number_of_seasons

            show_id = await asyncio.to_thread(fetch_show_id, title)
            if not show_id:
                return []
            count = await asyncio.to_thread(get_number_of_seasons, show_id)
            if count and count > 0:
                return list(range(1, count + 1))
        except Exception as e:  # noqa: BLE001 — best-effort; fall back to cache at match time
            logger.debug(f"Could not resolve season count for '{title}': {e}")
        return []
```

with:

```python
    async def _resolve_all_season_numbers(
        self, title: str, tmdb_id: int | None = None
    ) -> list[int]:
        """Resolve 1..N season numbers for a show via TMDB (unknown season).

        Uses ``tmdb_id`` directly when the job already resolved it — re-resolving
        by name can pick the dominant same-name twin (#370). Returns an empty list
        when the show can't be resolved; callers then rely on the precomputed
        cache / already-downloaded references during matching.
        """
        try:
            from app.matcher.tmdb_client import fetch_show_id, get_number_of_seasons

            show_id = str(tmdb_id) if tmdb_id else await asyncio.to_thread(fetch_show_id, title)
            if not show_id:
                return []
            count = await asyncio.to_thread(get_number_of_seasons, show_id)
            if count and count > 0:
                return list(range(1, count + 1))
        except Exception as e:  # noqa: BLE001 — best-effort; fall back to cache at match time
            logger.debug(f"Could not resolve season count for '{title}': {e}")
        return []
```

Immediately after it, add the new helper:

```python
    async def _start_tv_subtitle_prefetch(self, job) -> None:
        """Kick off the background reference-subtitle download for a TV job.

        Known season → that season only. Unknown season (the user chose "match
        across all seasons" in the season prompt, or a flat import folder) →
        prefetch EVERY season so matching can search across all of them instead
        of silently skipping the download and dead-ending in review (#370).
        """
        if job.detected_season:
            self._start_subtitle_download(
                job.id, job.detected_title, job.detected_season, job.tmdb_id
            )
            logger.info(
                f"Job {job.id}: starting subtitle download for "
                f"{job.detected_title} S{job.detected_season}"
            )
        elif self._start_subtitle_download_all_seasons:
            all_seasons = await self._resolve_all_season_numbers(
                job.detected_title, tmdb_id=job.tmdb_id
            )
            if all_seasons:
                logger.info(
                    f"Job {job.id}: season unknown for '{job.detected_title}'; "
                    f"prefetching subtitles for seasons {all_seasons}"
                )
                self._start_subtitle_download_all_seasons(
                    job.id, job.detected_title, all_seasons, tmdb_id=job.tmdb_id
                )
```

- [ ] **Step 4: Implement — `set_name_and_resume` kicks the prefetch and clears the reason**

In `set_name_and_resume` (line 893), after the `await self._resolve_missing_tmdb_id(job)` call and BEFORE `job.state = JobState.RIPPING`, insert:

```python
            # The season prompt and the unreadable-label prompt both resume
            # through here — kick the reference-subtitle prefetch now that the
            # identity is final. (This path previously never started a download
            # at all; #370.) A season the user left unset ("match across all
            # seasons") falls through to the all-seasons prefetch.
            if job.content_type == ContentType.TV and job.detected_title:
                await self._start_tv_subtitle_prefetch(job)
            job.review_reason = None
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/unit/test_identification_subtitle_prefetch.py -v`
Expected: 8 PASSED.

- [ ] **Step 6: Rewire the disc path — season gate + review routing**

In `backend/app/services/identification_coordinator.py`, replace (lines 417-433):

```python
                # Start subtitle download for ALL TV content — except when identity is
                # ambiguous (same-name collision) or a no-year twin needs disambiguation.
                # Downloading by the tentative name would fetch the wrong show's subtitles
                # before the user disambiguates.
                if (
                    job.content_type == ContentType.TV
                    and job.detected_title
                    and job.detected_season
                    and not _collision
                ):
                    self._start_subtitle_download(
                        job_id, job.detected_title, job.detected_season, job.tmdb_id
                    )
                    logger.info(
                        f"Job {job_id}: starting subtitle download for "
                        f"{job.detected_title} S{job.detected_season}"
                    )
```

with:

```python
                # Start subtitle download for ALL TV content — except when identity is
                # ambiguous (same-name collision) or a no-year twin needs disambiguation.
                # Downloading by the tentative name would fetch the wrong show's subtitles
                # before the user disambiguates.
                if job.content_type == ContentType.TV and job.detected_title and not _collision:
                    if job.detected_season is None:
                        # Disc label carried no season (box-set labels like
                        # "Eureka D3"). A single-season show needs no prompt;
                        # otherwise park the job for a season pick BEFORE
                        # ripping — downstream, an unknown season used to skip
                        # subtitle download entirely and dead-end every title
                        # in review (#370). Resumes via set_name_and_resume.
                        seasons = await self._resolve_all_season_numbers(
                            job.detected_title, tmdb_id=job.tmdb_id
                        )
                        if len(seasons) == 1:
                            job.detected_season = 1
                            await session.commit()
                        else:
                            reason = (
                                f"Identified as '{job.detected_title}' but the season "
                                f"could not be detected from the disc label — select a "
                                f"season to continue."
                            )
                            await self._state_machine.transition_to_review(
                                job, session, reason=reason, broadcast=False
                            )
                            await ws_manager.broadcast_job_update(
                                job_id,
                                JobState.REVIEW_NEEDED.value,
                                content_type=job.content_type.value,
                                detected_title=job.detected_title,
                                detected_season=None,
                                total_titles=job.total_titles,
                                review_reason=reason,
                            )
                            logger.info(
                                f"Job {job_id}: season unknown for "
                                f"'{job.detected_title}', prompting user for season"
                            )
                            return
                    await self._start_tv_subtitle_prefetch(job)
```

(The frontend keys the modal on the stable substring `select a season` — keep it verbatim if the wording is edited.)

- [ ] **Step 7: Rewire the staging-import path to the helper**

In the same file, replace (lines 766-784):

```python
                # Skip ripping — files already exist. Proceed to matching/organization.
                if job.content_type == ContentType.TV:
                    # Start subtitle download
                    if job.detected_title and job.detected_season:
                        self._start_subtitle_download(
                            job_id, job.detected_title, job.detected_season, job.tmdb_id
                        )
                    elif job.detected_title and self._start_subtitle_download_all_seasons:
                        # Season unknown (flat import folder): prefetch references for
                        # every season so the curator can match across all of them.
                        all_seasons = await self._resolve_all_season_numbers(job.detected_title)
                        if all_seasons:
                            logger.info(
                                f"Job {job_id}: season unknown for '{job.detected_title}'; "
                                f"prefetching subtitles for seasons {all_seasons}"
                            )
                            self._start_subtitle_download_all_seasons(
                                job_id, job.detected_title, all_seasons
                            )
```

with:

```python
                # Skip ripping — files already exist. Proceed to matching/organization.
                # Imports keep automatic all-seasons prefetch (flat folders genuinely
                # span seasons); only physical discs get the season prompt.
                if job.content_type == ContentType.TV:
                    if job.detected_title:
                        await self._start_tv_subtitle_prefetch(job)
```

- [ ] **Step 8: Run unit + pipeline suites (catches wiring regressions)**

Run: `uv run pytest tests/unit/test_identification_subtitle_prefetch.py tests/pipeline/ -v`
Expected: all PASSED. Pipeline generic-label/import flows exercise the staging path; if a pipeline test inserts an unknown-season TV *disc* and expected it to proceed to ripping, it will now park in REVIEW_NEEDED — update that test's expectation to the new routing (state `review_needed`, reason containing `select a season`).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/identification_coordinator.py backend/tests/unit/test_identification_subtitle_prefetch.py
git commit -m "feat(identification): prompt for the season when a disc reveals none; prefetch subtitles on every resume path (#370)"
```

(Include any pipeline test files updated in Step 8.)

---

### Task 4: Gate the wrong-show advisory + honest no-references review reason (Fix 2)

`_detect_wrong_show` must not fire when the subtitle pipeline never delivered a reference corpus (zero matches is then expected for the RIGHT show too). And instead of the generic "N title(s) need manual episode assignment", the user should be told the real, actionable problem. Still needed in v2: downloads can fail outright, and the "match across all seasons" path can come up empty.

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py:26-62` (`_detect_wrong_show`), new module-level helper, `check_job_completion` (~line 570)
- Test: `backend/tests/unit/test_finalization_coordinator.py`

- [ ] **Step 1: Update the test seeder and write the failing tests**

In `backend/tests/unit/test_finalization_coordinator.py`:

(a) Add next to `FRASIER_CANDS` (line 26):

```python
EUREKA_CANDS = json.dumps(
    [
        {"tmdb_id": 4620, "name": "Eureka", "year": "2006", "popularity": 60.0},
        {"tmdb_id": 153312, "name": "Eureka!", "year": "2022", "popularity": 4.0},
    ]
)
```

(b) Update `_seed_job` (line 66) to accept a subtitle status, defaulting to `"completed"` — in production, matching only runs after the subtitle gate, so a completed status is the realistic baseline; the new no-refs tests opt out explicitly:

```python
async def _seed_job(
    titles,
    staging,
    *,
    content_type=ContentType.TV,
    state=JobState.MATCHING,
    match_details_by_idx=None,
    tmdb_id=None,
    candidates_json=None,
    duration=1380,
    subtitle_status="completed",
) -> int:
```

and add `subtitle_status=subtitle_status,` to the `DiscJob(...)` constructor inside it.

(c) In `TestDetectWrongShow._job` (line 560), add `subtitle_status="completed",` to the `base` dict.

(d) Add to `TestDetectWrongShow` (after `test_none_for_movie`, ~line 610):

```python
    def test_none_when_subtitles_never_delivered(self):
        # #370 (Eureka D3): the download never started -> status None. With no
        # reference corpus, all-unmatched is the expected outcome for the RIGHT
        # show too — naming the twin would mislead.
        titles = [self._ttl(0), self._ttl(1), self._ttl(2)]
        assert _detect_wrong_show(self._job(subtitle_status=None), titles) is None

    def test_none_when_subtitle_download_failed(self):
        titles = [self._ttl(0), self._ttl(1)]
        assert _detect_wrong_show(self._job(subtitle_status="failed"), titles) is None

    def test_partial_download_still_detects(self):
        # A partial corpus is still a corpus — the aggregate signal stands.
        titles = [self._ttl(0), self._ttl(1)]
        assert _detect_wrong_show(self._job(subtitle_status="partial"), titles) is not None
```

(e) Add a new routing test class after `TestWrongShowRoutingInCompletion`:

```python
@pytest.mark.unit
class TestNoReferenceSubtitlesRouting:
    """All-unmatched + the subtitle pipeline never delivered references (#370):
    route straight to review with an honest, actionable reason — no twin
    advisory, no deep re-match escalation against an empty corpus."""

    async def test_routes_to_honest_review_reason(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, None, None, TitleState.REVIEW),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
            tmdb_id=4620,
            candidates_json=EUREKA_CANDS,
            subtitle_status=None,
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "no reference subtitles" in job.review_reason.lower()
        # No misleading twin advisory…
        assert "2022" not in (job.review_reason or "")
        # …and no escalation pass was dispatched against the empty corpus.
        assert coord._review_passes.get(job_id) is None
        coord.finalize_disc_job.assert_not_called()

    async def test_failed_download_also_gets_honest_reason(self, tmp_path):
        job_id = await _seed_job(
            [(0, None, None, TitleState.REVIEW), (1, None, None, TitleState.REVIEW)],
            staging=str(tmp_path),
            subtitle_status="failed",
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "no reference subtitles" in job.review_reason.lower()

    async def test_completed_subtitles_keep_normal_review_routing(self, tmp_path):
        # With a delivered corpus and no twin, the generic review path is intact.
        job_id = await _seed_job(
            [(0, None, None, TitleState.REVIEW), (1, None, None, TitleState.REVIEW)],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "manual episode assignment" in job.review_reason
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/unit/test_finalization_coordinator.py -v`
Expected: the 2 new `TestDetectWrongShow` gate tests FAIL (detector currently ignores subtitle_status); `TestNoReferenceSubtitlesRouting::test_routes_to_honest_review_reason` and `::test_failed_download_also_gets_honest_reason` FAIL (generic reason / twin advisory today). `test_partial_download_still_detects`, `test_completed_subtitles_keep_normal_review_routing`, and all pre-existing tests PASS.

- [ ] **Step 3: Implement the gate and the honest-review branch**

In `backend/app/services/finalization_coordinator.py`:

(a) In `_detect_wrong_show`, immediately after the `content_type` check (lines 41-42), insert:

```python
    # A wholesale match failure only implicates the WRONG SHOW if matching had
    # a reference corpus to fail against. When the subtitle pipeline never
    # delivered anything (download never started, or found nothing), zero
    # matches is the expected outcome for the RIGHT show too (#370).
    if job.subtitle_status not in ("completed", "partial"):
        return None
```

Also append one line to its docstring (after the "…handled by the normal review path." sentence):

```
    Gated on a delivered subtitle corpus (``subtitle_status`` completed/partial):
    see ``_no_reference_subtitles`` for the no-corpus sibling branch (#370).
```

(b) Add a module-level helper directly after `_wrong_show_review_reason` (line 82):

```python
def _no_reference_subtitles(job, titles) -> bool:
    """True when a TV disc's wholesale match failure is explained by the subtitle
    pipeline never delivering references (#370: download failed outright, or the
    all-seasons escape hatch found nothing for any season).

    Requires ALL episode candidates unmatched: a disc with even one successful
    match clearly had a usable corpus, whatever the status field says. Pure — no
    DB/IO.
    """
    if job.content_type != ContentType.TV:
        return False
    if job.subtitle_status in ("completed", "partial"):
        return False
    episode_candidates = [t for t in titles if t.is_selected and not t.is_extra]
    return bool(episode_candidates) and all(
        t.matched_episode is None for t in episode_candidates
    )
```

(c) In `check_job_completion`, directly after `wrong_show = _detect_wrong_show(job, titles)` (line 570) and BEFORE the `_maybe_escalate_conflicts` call, insert:

```python
        # No reference subtitles ever arrived (#370): matching could not have
        # succeeded, and deep re-match escalation would just burn ASR passes
        # against an empty corpus. Route straight to review with an honest,
        # actionable reason (the wrong-show advisory above is already gated).
        if _no_reference_subtitles(job, titles):
            show = job.tmdb_name or job.detected_title or "this show"
            reason = (
                f"Episode matching couldn't run: no reference subtitles were "
                f"available for {show}. Retry the subtitle download (or add an "
                f"OpenSubtitles API key in Settings), then re-match — or assign "
                f"episodes manually below."
            )
            logger.warning(f"Job {job_id}: {reason}")
            await self._clear_review_state(session, job)
            await self._state_machine.transition_to_review(job, session, reason=reason)
            return
```

- [ ] **Step 4: Run the full file and the escalation suite**

Run: `uv run pytest tests/unit/test_finalization_coordinator.py tests/unit/test_auto_conflict_escalation.py -v`
Expected: all PASSED. If any `test_auto_conflict_escalation.py` test fails on the new branch, its seeded job has all-unmatched titles with a None `subtitle_status` — add `subtitle_status="completed"` to that test file's `DiscJob(...)` seeder (same rationale as `_seed_job`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/finalization_coordinator.py backend/tests/unit/test_finalization_coordinator.py
git commit -m "fix(review): suppress wrong-show advisory and explain missing reference subtitles honestly (#370)"
```

(Include `backend/tests/unit/test_auto_conflict_escalation.py` in the `git add` if Step 4 required touching it.)

---

### Task 5: Season-roster endpoint — `?season=` override + `season_count` (Fix 3, backend)

Feeds BOTH the season-prompt modal (Task 6: how many seasons to offer) and the review-page picker (Task 7: backstop for the all-seasons path).

**Files:**
- Modify: `backend/app/api/routes.py:34` (import), `:637-660` (`SeasonRosterResponse`), `:663-759` (`get_season_roster`)
- Test: `backend/tests/unit/test_season_roster.py`

- [ ] **Step 1: Write the failing tests**

Add to class `TestSeasonRoster` in `backend/tests/unit/test_season_roster.py` (after `test_roster_unavailable_without_tmdb_id`, ~line 159):

```python
    async def test_unknown_season_reports_season_count_for_picker(self, client):
        """detected_season=None → available:false but show_id + season_count are
        present so the season prompt / review picker can render options (#370)."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)

        with patch("app.api.routes.get_number_of_seasons", return_value=5):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["show_id"] == 12345
        assert data["season_count"] == 5

    async def test_unknown_season_count_failure_degrades_gracefully(self, client):
        """A TMDB hiccup on the count lookup must not 500 the roster."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)

        with patch(
            "app.api.routes.get_number_of_seasons", side_effect=RuntimeError("tmdb down")
        ):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["season_count"] is None

    async def test_season_override_loads_that_seasons_roster(self, client):
        """?season=2 on an unknown-season job loads season 2's episodes (#370)."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)
        await _seed_title(job.id, 0, "S02E01")

        seen: dict = {}

        def fake_fetch(show_id, season, api_key):
            seen["season"] = season
            return _FAKE_EPISODES

        with (
            patch("app.api.routes.fetch_season_episodes", side_effect=fake_fetch),
            patch("app.api.routes.get_number_of_seasons", return_value=5),
        ):
            response = await client.get(f"/api/jobs/{job.id}/season-roster?season=2")

        assert response.status_code == 200
        data = response.json()
        assert seen["season"] == 2
        assert data["available"] is True
        assert data["season_number"] == 2
        assert data["season_count"] == 5
        episodes = {ep["episode_code"]: ep for ep in data["episodes"]}
        assert episodes["S02E01"]["status"] == "assigned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_season_roster.py -v`
Expected: the 3 new tests FAIL — the first two with `AttributeError: <module 'app.api.routes'> does not have the attribute 'get_number_of_seasons'` (patch target missing), the third because `?season=2` is ignored and `available` is false. Pre-existing tests PASS.

- [ ] **Step 3: Implement the endpoint changes**

In `backend/app/api/routes.py`:

(a) Extend the import on line 34:

```python
from app.matcher.tmdb_client import fetch_season_episodes, get_number_of_seasons
```

(b) Add a field to `SeasonRosterResponse` (after `reason: str | None = None`, line 655):

```python
    # Season picker (#370): total seasons for the show, populated only while the
    # job's season is unknown (no extra TMDB call on the normal detected path).
    season_count: int | None = None
```

(c) Replace the function from the signature down to (and including) the `if not episodes_raw:` early return (lines 663-694) with:

```python
@router.get("/jobs/{job_id}/season-roster", response_model=SeasonRosterResponse)
async def get_season_roster(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
    season: int | None = Query(
        default=None, ge=0, description="Override season (unknown-season picker, #370)"
    ),
) -> SeasonRosterResponse:
    """Season episode list with per-episode coverage for the review UI.

    ``?season=N`` overrides the job's detected season so the review page can
    browse rosters for discs whose label carried no season (#370).
    """
    if job.content_type != ContentType.TV:
        return SeasonRosterResponse(available=False, reason="Not a TV disc")

    effective_season = season if season is not None else job.detected_season

    # Season-picker support (#370): while the job's season is unknown, report
    # how many seasons exist so the prompt/picker can render options. The
    # lookup is best-effort decoration — a TMDB failure must not break review.
    season_count: int | None = None
    if job.tmdb_id and job.detected_season is None:
        try:
            season_count = await asyncio.to_thread(get_number_of_seasons, str(job.tmdb_id))
        except Exception:  # noqa: BLE001 — picker is best-effort decoration
            season_count = None

    if not job.tmdb_id or effective_season is None:
        return SeasonRosterResponse(
            available=False,
            season_number=effective_season,
            show_id=job.tmdb_id,
            season_count=season_count,
            reason="Show or season not identified yet",
        )

    season_num = effective_season
    from app.services.config_service import get_config

    config = await get_config()
    # fetch_season_episodes does a synchronous requests.get; run it off the
    # event loop so a slow TMDB call doesn't stall other requests / WS pushes.
    episodes_raw = await asyncio.to_thread(
        fetch_season_episodes, str(job.tmdb_id), season_num, config.tmdb_api_key
    )
    if not episodes_raw:
        return SeasonRosterResponse(
            available=False,
            season_number=season_num,
            show_id=job.tmdb_id,
            season_count=season_count,
            reason="Could not load season episodes from TMDB",
        )
```

(d) In the remainder of the function, rename every other use of the old `season` local to `season_num` (the original assigned `season = job.detected_season`). Occurrences: the season-filter comparison in the assigned-episodes loop (`int(match.group(1)) != season`), the f-string `f"S{season:02d}E{ep['episode_number']:02d}"`, `roster_pairs = [(season, ...)]`, `matched_pairs = [(season, ...)]`, the `build_ordering_options` argument, and the final `season_number=season` — all become `season_num`. Also add `season_count=season_count,` to the final `SeasonRosterResponse(available=True, ...)` return.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_season_roster.py -v`
Expected: all PASSED (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_season_roster.py
git commit -m "feat(api): season-roster ?season override + season_count for the unknown-season picker (#370)"
```

---

### Task 6: SeasonPromptModal — pick the season at insert time (Fix 3, the v2 centerpiece)

Dashboard modal mirroring `NamePromptModal`, triggered by the `select a season` review reason from Task 3. Submits through the existing `setJobName` → `/set-name` → `set_name_and_resume` path (title/type already known); "Match across all seasons" submits without a season.

**Files:**
- Create: `frontend/src/components/SeasonPromptModal.tsx`
- Create: `frontend/src/components/SeasonPromptModal.test.tsx`
- Modify: `frontend/src/app/App.tsx` (state ~line 67, effect ~line 152, render after the NamePromptModal block ~line 640)

- [ ] **Step 0: Install deps (worktree gotcha)**

Run (from `frontend/`): `npm install`
Note: this rewrites `package-lock.json` — run `git checkout package-lock.json` before committing.

- [ ] **Step 1: Write the failing component tests**

Create `frontend/src/components/SeasonPromptModal.test.tsx`:

```tsx
import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SeasonPromptModal from './SeasonPromptModal';
import type { Job } from '../types';

const job: Job = {
    id: 7,
    drive_id: 'D:',
    volume_label: 'EUREKA_D3',
    content_type: 'tv',
    state: 'review_needed',
    current_speed: '',
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 11,
    error_message: null,
    detected_title: 'Eureka',
    detected_season: null,
};

function mockRosterFetch(seasonCount: number | null) {
    vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ available: false, season_count: seasonCount }),
        }),
    );
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('SeasonPromptModal (#370)', () => {
    it('offers one option per season from season_count', async () => {
        mockRosterFetch(5);
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 05' })).toBeInTheDocument(),
        );
        expect(screen.queryByRole('option', { name: 'Season 06' })).not.toBeInTheDocument();
    });

    it('submits the chosen season', async () => {
        mockRosterFetch(5);
        const onSubmit = vi.fn();
        render(<SeasonPromptModal job={job} onSubmit={onSubmit} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 03' })).toBeInTheDocument(),
        );
        fireEvent.change(screen.getByLabelText('Season'), { target: { value: '3' } });
        fireEvent.click(screen.getByRole('button', { name: /continue/i }));
        expect(onSubmit).toHaveBeenCalledWith(3);
    });

    it('submits undefined for "match across all seasons"', async () => {
        mockRosterFetch(5);
        const onSubmit = vi.fn();
        render(<SeasonPromptModal job={job} onSubmit={onSubmit} onCancel={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: /all seasons/i }));
        expect(onSubmit).toHaveBeenCalledWith(undefined);
    });

    it('falls back to 15 season options when the count is unavailable', async () => {
        mockRosterFetch(null);
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 15' })).toBeInTheDocument(),
        );
    });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:unit -- SeasonPromptModal`
Expected: FAIL — module `./SeasonPromptModal` does not exist.

- [ ] **Step 3: Implement the modal**

Create `frontend/src/components/SeasonPromptModal.tsx` (visual language mirrors `NamePromptModal.tsx` — same backdrop, panel, button styles):

```tsx
import { useState, useEffect, KeyboardEvent } from 'react';
import { motion } from 'motion/react';
import { IcoTv, IcoError } from '../app/components/icons';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';

interface SeasonPromptModalProps {
    job: Job;
    /** Called with the picked season, or undefined for "match across all seasons". */
    onSubmit: (season?: number) => void;
    onCancel: () => void;
}

const FALLBACK_SEASON_COUNT = 15;

/**
 * Insert-time season prompt (#370): a disc labeled by disc number only
 * ("Eureka D3") identifies the show but not the season. Asking up front stems
 * the downstream mess — N-season subtitle downloads, cross-season ASR, and a
 * review dropdown locked to S01. "All seasons" is the automation escape hatch.
 */
export default function SeasonPromptModal({ job, onSubmit, onCancel }: SeasonPromptModalProps) {
    const [season, setSeason] = useState<string>('1');
    const [seasonCount, setSeasonCount] = useState<number | null>(null);

    // season_count comes from the roster endpoint, which reports it whenever
    // the job's season is unknown — exactly this modal's trigger state.
    useEffect(() => {
        let cancelled = false;
        fetch(`/api/jobs/${job.id}/season-roster`)
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
                if (!cancelled && data && typeof data.season_count === 'number') {
                    setSeasonCount(data.season_count);
                }
            })
            .catch(() => {
                /* fall back to the generic option range */
            });
        return () => {
            cancelled = true;
        };
    }, [job.id]);

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter') onSubmit(parseInt(season, 10) || 1);
        if (e.key === 'Escape') onCancel();
    };

    const buttonStyle = (color: string, filled: boolean): React.CSSProperties => ({
        flex: 1,
        padding: '10px 16px',
        fontFamily: sv.mono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color,
        border: `1px solid ${color}${filled ? '' : '80'}`,
        background: filled ? `${color}1f` : 'transparent',
        boxShadow: filled ? `0 0 16px ${color}4d, inset 0 0 8px ${color}0d` : `0 0 8px ${color}26`,
        cursor: 'pointer',
    });

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onKeyDown={handleKeyDown}
            role="dialog"
            aria-modal="true"
            aria-labelledby="season-prompt-title"
            aria-describedby="season-prompt-description"
        >
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onCancel}
            />
            <motion.div
                className="relative w-full max-w-md"
                initial={{ opacity: 0, scale: 0.92, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 20 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            >
                <SvPanel
                    glow
                    pad={0}
                    style={{
                        background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                        boxShadow: `0 0 40px ${sv.cyan}33, 0 0 80px ${sv.cyan}11, inset 0 0 30px ${sv.cyan}0d`,
                    }}
                >
                    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
                        {/* Header */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <IcoTv
                                size={22}
                                color={sv.cyan}
                                style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}cc)` }}
                            />
                            <h2
                                id="season-prompt-title"
                                style={{
                                    fontFamily: sv.display,
                                    fontWeight: 700,
                                    fontSize: 18,
                                    letterSpacing: '0.2em',
                                    textTransform: 'uppercase',
                                    color: sv.cyanHi,
                                    textShadow: `0 0 10px ${sv.cyan}99`,
                                    margin: 0,
                                }}
                            >
                                Select Season
                            </h2>
                        </div>

                        {/* Notice */}
                        <div
                            style={{
                                display: 'flex',
                                gap: 12,
                                alignItems: 'flex-start',
                                padding: 12,
                                border: `1px solid ${sv.yellow}4d`,
                                background: `${sv.yellow}0d`,
                            }}
                        >
                            <IcoError size={16} color={sv.yellow} style={{ marginTop: 2, flexShrink: 0 }} />
                            <p
                                id="season-prompt-description"
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: `${sv.yellow}cc`,
                                    textTransform: 'uppercase',
                                    letterSpacing: '0.14em',
                                    margin: 0,
                                    lineHeight: 1.6,
                                }}
                            >
                                Identified as “{job.detected_title}” but the disc label (
                                {job.volume_label || 'NO_LABEL'}) does not reveal the season.
                            </p>
                        </div>

                        {/* Season select */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Season</SvLabel>
                            <select
                                value={season}
                                onChange={(e) => setSeason(e.target.value)}
                                aria-label="Season"
                                style={{
                                    width: 220,
                                    background: sv.bg0,
                                    border: `1px solid ${sv.lineMid}`,
                                    color: sv.cyanHi,
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    padding: '10px 12px',
                                    outline: 'none',
                                    cursor: 'pointer',
                                }}
                            >
                                {Array.from(
                                    { length: seasonCount ?? FALLBACK_SEASON_COUNT },
                                    (_, i) => i + 1,
                                ).map((s) => (
                                    <option key={s} value={s}>
                                        {`Season ${String(s).padStart(2, '0')}`}
                                    </option>
                                ))}
                            </select>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Actions */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <motion.button
                                type="button"
                                onClick={onCancel}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={buttonStyle(sv.red, false)}
                            >
                                Cancel
                            </motion.button>
                            <motion.button
                                type="button"
                                onClick={() => onSubmit(undefined)}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                title="Slower: matches every season's references"
                                style={buttonStyle(sv.magenta, false)}
                            >
                                All Seasons
                            </motion.button>
                            <motion.button
                                type="button"
                                onClick={() => onSubmit(parseInt(season, 10) || 1)}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={buttonStyle(sv.cyan, true)}
                            >
                                Continue →
                            </motion.button>
                        </div>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
```

- [ ] **Step 4: Run the component tests**

Run: `npm run test:unit -- SeasonPromptModal`
Expected: 4 PASSED.

- [ ] **Step 5: Wire it into the dashboard**

In `frontend/src/app/App.tsx`:

(a) Import next to the NamePromptModal import (line 12):

```tsx
import SeasonPromptModal from "../components/SeasonPromptModal";
```

(b) Add state next to `namePromptJob` (line 67):

```tsx
  const [seasonPromptJob, setSeasonPromptJob] = useState<Job | null>(null);
```

(c) Extend the prompt effect (lines 152-161) — append the season match before the closing brace:

```tsx
  // Show name prompt modal for unreadable labels or TV shows where TMDB lookup failed
  useEffect(() => {
    const needsName = jobs.find(
      (j) =>
        j.state === 'review_needed' &&
        ((j.review_reason?.includes('label unreadable') && !j.detected_title) ||
          (j.review_reason?.includes('merged without separators') && j.content_type === 'tv')),
    );
    setNamePromptJob(needsName ?? null);
    // Season prompt (#370): show identified but the disc label revealed no season.
    const needsSeason = jobs.find(
      (j) => j.state === 'review_needed' && j.review_reason?.includes('select a season'),
    );
    setSeasonPromptJob(needsSeason ?? null);
  }, [jobs]);
```

(d) Render after the NamePromptModal `</AnimatePresence>` block (line 640) — the `!namePromptJob` guard prevents stacked modals:

```tsx
      {/* Season Prompt Modal — show identified but the disc label has no season (#370) */}
      <AnimatePresence>
        {seasonPromptJob && !namePromptJob && (
          <SeasonPromptModal
            job={seasonPromptJob}
            onSubmit={(season) => {
              setJobName(
                seasonPromptJob.id,
                seasonPromptJob.detected_title ?? seasonPromptJob.volume_label,
                'tv',
                season,
              );
              setSeasonPromptJob(null);
            }}
            onCancel={() => {
              cancelJob(String(seasonPromptJob.id));
              setSeasonPromptJob(null);
            }}
          />
        )}
      </AnimatePresence>
```

- [ ] **Step 6: Run frontend checks**

Run: `npm run test:unit` then `npm run lint` then `npm run build`
Expected: all green (existing App routing tests mock `useJobManagement`, so the new wiring compiles against the mocks).

- [ ] **Step 7: Restore the lockfile and commit**

```bash
git checkout package-lock.json
git add frontend/src/components/SeasonPromptModal.tsx frontend/src/components/SeasonPromptModal.test.tsx frontend/src/app/App.tsx
git commit -m "feat(ui): season prompt modal for discs whose label reveals no season (#370)"
```

---

### Task 7: Review-page season picker + season-aware manual dropdown (Fix 3 backstop)

Still needed: the "All Seasons" escape hatch can land titles in review with `detected_season` still `None` (and legacy jobs exist). Without this, the manual dropdown stays locked to S01 for those jobs.

**Files:**
- Modify: `frontend/src/components/ReviewQueue/types.ts` (SeasonRoster), `frontend/src/hooks/useSeasonRoster.ts`, `frontend/src/components/ReviewQueue/Inspector.tsx`, `frontend/src/components/ReviewQueue.tsx`
- Test: `frontend/src/components/ReviewQueue/Inspector.test.tsx`

- [ ] **Step 1: Write the failing Inspector tests**

In `frontend/src/components/ReviewQueue/Inspector.test.tsx`:

(a) Add `season?: number;` to the `renderInspector` props type and pass `season={props.season ?? 1}` to `<Inspector …>` (after `episodes={[]}`).

(b) Add a new describe block at the end of the file:

```tsx
describe('Inspector — manual dropdown season (#370)', () => {
    it('generates fallback episode codes for the provided season, not S01', () => {
        renderInspector({ season: 3 });
        expect(screen.getByRole('option', { name: 'S03E01' })).toBeInTheDocument();
        expect(screen.queryByRole('option', { name: 'S01E01' })).not.toBeInTheDocument();
    });

    it('defaults to season 1 codes when season is 1', () => {
        renderInspector({ season: 1 });
        expect(screen.getByRole('option', { name: 'S01E01' })).toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:unit -- Inspector`
Expected: FAIL — the `season` prop doesn't exist yet, so TypeScript/props errors or the S03 assertion fails (dropdown still renders S01 codes).

- [ ] **Step 3: Implement — Inspector takes the effective season as a prop**

In `frontend/src/components/ReviewQueue/Inspector.tsx`:

(a) Add `season,` to the destructured props (next to `episodes,`) and to the props type (next to `episodes: RosterEpisode[];`):

```tsx
    /** Effective season for manual/LLM codes: detected, else picker choice, else 1 (#370). */
    season: number;
```

(b) Delete line 85 (`const season = job.detected_season ?? 1;`) — the prop replaces it; the LLM-suggestion display (line 183) keeps working unchanged.

(c) Change the fallback dropdown (lines 334-336) from `generateEpisodeOptions(job.detected_season || 1, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON)` to `generateEpisodeOptions(season, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON)`.

- [ ] **Step 4: Implement — roster hook accepts a season override**

In `frontend/src/hooks/useSeasonRoster.ts`, change the signature:

```typescript
export function useSeasonRoster(jobId: string | undefined, seasonOverride?: number | null) {
```

inside the effect, replace the `fetch(...)` line with:

```typescript
        const url =
            seasonOverride != null
                ? `/api/jobs/${jobId}/season-roster?season=${seasonOverride}`
                : `/api/jobs/${jobId}/season-roster`;
        fetch(url)
```

and extend the dependency array to `[jobId, reloadKey, seasonOverride]`. Update the doc comment's first line to: `Loads a season's episode list (code + name) plus persisted coverage for a job — the detected season by default, or an explicit override from the unknown-season picker (#370).`

- [ ] **Step 5: Implement — types + ReviewQueue picker state and wiring**

(a) In `frontend/src/components/ReviewQueue/types.ts`, add to `SeasonRoster`:

```typescript
    /** Total seasons for the show — populated while the season picker is in play (#370). */
    season_count?: number | null;
```

(b) In `frontend/src/components/ReviewQueue.tsx`:

Add state above the `useSeasonRoster` call (line 187) and thread it through:

```typescript
    // Review-page season picker (#370): backstop for jobs that reached review
    // with the season still unknown (the modal's "All Seasons" path, legacy jobs).
    const [seasonOverride, setSeasonOverride] = useState<number | null>(null);

    const { roster, error: rosterError, episodeName, reload: reloadRoster } = useSeasonRoster(
        jobId,
        seasonOverride,
    );
```

Add the effective season directly below (after the `job` state is declared — move below it if needed):

```typescript
    // Manual/LLM episode codes use: the detected season, else the picker
    // choice, else 1 (legacy fallback).
    const effectiveSeason = job?.detected_season ?? seasonOverride ?? 1;
```

In `handleAcceptLLMSuggestion` (line 448), replace:

```typescript
        const seasonNum = job?.detected_season ?? 1;
        const seasonStr = String(seasonNum).padStart(2, '0');
```

with:

```typescript
        const seasonStr = String(effectiveSeason).padStart(2, '0');
```

Pass the prop where `<Inspector` is rendered (lines 1108-1129): add `season={effectiveSeason}` after `episodes={rosterEpisodes}`.

(c) Render the picker in the TV review layout — insert between the notices and the ordering block (after `{orderingError && …}`, line 939):

```tsx
                {/* Season picker (#370) — only when the job's season is unknown. */}
                {job.detected_season == null && (
                    <div style={{ marginBottom: 24 }}>
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>
                                Season — not detected for this job; pick one to load its episode list
                            </SvLabel>
                        </div>
                        <SvPanel pad={14}>
                            <select
                                value={seasonOverride ?? ''}
                                onChange={(e) =>
                                    setSeasonOverride(e.target.value ? parseInt(e.target.value) : null)
                                }
                                aria-label="Season"
                                style={{
                                    background: sv.bg0,
                                    border: `1px solid ${sv.lineMid}`,
                                    color: sv.ink,
                                    fontFamily: sv.mono,
                                    fontSize: 12,
                                    padding: '7px 9px',
                                    outline: 'none',
                                    cursor: 'pointer',
                                    minWidth: 220,
                                }}
                            >
                                <option value="">Pick season…</option>
                                {Array.from({ length: roster?.season_count ?? 10 }, (_, i) => i + 1).map(
                                    (s) => (
                                        <option key={s} value={s}>
                                            {`Season ${String(s).padStart(2, '0')}`}
                                        </option>
                                    ),
                                )}
                            </select>
                        </SvPanel>
                    </div>
                )}
```

(The movie layout returns earlier in the component, so no `content_type` check is needed. The `?? 10` fallback keeps the picker usable when `season_count` couldn't be fetched.)

- [ ] **Step 6: Run frontend checks**

Run: `npm run test:unit` then `npm run lint` then `npm run build`
Expected: all green, including the two new Inspector tests.

- [ ] **Step 7: Restore the lockfile and commit**

```bash
git checkout package-lock.json
git add frontend/src/components/ReviewQueue/types.ts frontend/src/hooks/useSeasonRoster.ts frontend/src/components/ReviewQueue/Inspector.tsx frontend/src/components/ReviewQueue/Inspector.test.tsx frontend/src/components/ReviewQueue.tsx
git commit -m "feat(review): season picker backstop; manual dropdown no longer locked to S01 (#370)"
```

---

### Task 8: Changelog, full verification, live simulation, PR

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` section)

- [ ] **Step 1: Add changelog entries**

Under `## [Unreleased]` → `### Fixed` (create the subsection if absent), add:

```markdown
- Discs whose label reveals no season (e.g. box-set discs labeled "Show D3") no longer dead-end in review: Engram now asks for the season up front via a dashboard prompt (with a "match across all seasons" option), instead of silently skipping the reference-subtitle download and failing every episode match (#370)
- Naming a disc through the identify prompt now also starts the reference-subtitle download — previously this resume path never downloaded subtitles at all (#370)
- The "different same-named show — re-identify to fix" advisory no longer fires when no reference subtitles were ever available; the review reason now explains the real problem and how to fix it (#370)
- The review page gains a season picker when a job's season is unknown, so manual episode assignment is no longer locked to Season 1 (#370)
- An empty reference-subtitle lookup is no longer cached for the rest of the run, so retried subtitle downloads become visible to re-matches (#370)
```

- [ ] **Step 2: Full backend verification**

Run (from `backend/`):
```bash
uv run pytest tests/unit/ tests/pipeline/ -q
uv run ruff check .
uv run ruff format --check .
```
Expected: all tests pass, no lint/format diffs. If `ruff format --check` flags the new files, run `uv run ruff format .` and re-stage.

- [ ] **Step 3: Full frontend verification**

Run (from `frontend/`):
```bash
npm run test:unit
npm run lint
npm run build
```
Expected: all green. Run `git checkout package-lock.json` afterwards if it changed.

- [ ] **Step 4 (recommended): live simulation of the season-prompt flow**

Per CLAUDE.md worktree isolation — distinct port + per-worktree DB, DEBUG on (PowerShell, from `backend/`):

```powershell
$env:DEBUG = "true"
uv run uvicorn app.main:app --port 8100
```

Insert a season-less TV disc label:

```bash
curl -X POST localhost:8100/api/simulate/insert-disc -H "Content-Type: application/json" -d '{"volume_label":"EUREKA_D3","content_type":"tv","simulate_ripping":true}'
```

Expected: the job parks in `review_needed` with `review_reason` containing "select a season" (log: `season unknown for 'Eureka', prompting user for season`) — instead of ripping straight through. Then resume it:

```bash
curl -X POST localhost:8100/api/jobs/1/set-name -H "Content-Type: application/json" -d '{"name":"Eureka","content_type":"tv","season":3}'
```

Expected: log shows `starting subtitle download for Eureka S3` and the job transitions to `ripping`. Also `GET localhost:8100/api/jobs/1/season-roster` (while parked) returns `"season_count"` and `"available": false`. With the frontend running (`$env:VITE_PORT = "5273"; $env:VITE_BACKEND_PORT = "8100"; npm run dev`), the SeasonPromptModal should appear on the dashboard.

**Kill the servers when done** (CLAUDE.md rule — scoped to this session's ports):

```powershell
Get-NetTCPConnection -LocalPort 8100,5273 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

- [ ] **Step 5: Commit changelog and push**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entries for the unknown-season review fixes (#370)"
git push -u origin claude/admiring-dhawan-55ceb1
```

- [ ] **Step 6: Open the PR**

```bash
gh pr create --title "fix: prompt for the season when a disc reveals none, instead of dead-ending in review (#370)" --body "$(cat <<'EOF'
Fixes #370.

A disc labeled by disc number only ("Eureka D3") identifies the show but not the season. Four stacked bugs then made review a dead end. Design decision: ask the user up front instead of brute-forcing downstream.

1. **Season prompt at insert time.** Reference-subtitle download was silently skipped when no season was detected; the job then ran a doomed matching pass (every title at confidence 0). Now an unknown-season TV disc parks in REVIEW_NEEDED and a dashboard modal (mirroring the unreadable-label prompt) asks for the season before ripping — with "match across all seasons" as the automation escape hatch (all-seasons prefetch keyed by tmdb_id). Single-season shows auto-pin to S1, no prompt. Bonus: the name-prompt resume path never started a subtitle download at all — fixed by the shared prefetch helper.
2. **The wrong-show advisory misfired** ("did you mean Eureka! (2022)? Re-identify to fix") — a wholesale match failure with no reference corpus is expected for the RIGHT show too. Now gated on a delivered corpus; the review reason honestly explains missing subtitles, and deep re-match escalation no longer burns ASR passes against an empty corpus.
3. **The manual episode dropdown was locked to Season 1** (`detected_season || 1`). The season-roster endpoint accepts `?season=` and reports `season_count`; the review page shows a season picker as a backstop for jobs that reach review with the season still unknown.
4. **An empty reference-file lookup was cached** for the matcher's lifetime, masking late-arriving subtitle downloads. Empty results are no longer cached.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** Fix 4 → Task 1; Fix 1 → Tasks 2-3 (v2: prompt-first, all-seasons as opt-in); Fix 2 → Task 4; Fix 3 → Tasks 5-7 (endpoint, insert-time modal, review-page backstop); changelog/verification/PR → Task 8.
- **Ordering dependencies:** Task 2 before Task 3 (`tmdb_id` kwarg on the all-seasons starter); Task 5 before Task 6 (modal reads `season_count`).
- **Marker contract:** the backend review reason and the App.tsx effect both use the substring `select a season` — change one, change both (Task 3 Step 6 ↔ Task 6 Step 5c).
- **Modal exit contract:** both exits change job state (submit → RIPPING via set-name; cancel → cancel job), because the dashboard effect re-evaluates on every jobs update and would re-open a merely-dismissed modal — same contract as NamePromptModal.
- **Known risks:** (1) Task 4's new review branch changes routing for tests seeding all-unmatched TV titles with a None subtitle_status — handled by the `_seed_job` default flip and the Step 4 contingency. (2) Task 3's disc-path gate may change expectations in pipeline tests that insert unknown-season TV discs — Step 8 calls this out explicitly. The full-suite run in Task 8 Step 2 is the backstop. (3) The disc-path routing itself has no direct unit test (it sits mid-method behind heavy TMDB/analyst mocking) — covered by the resume-path unit tests, the pipeline suite, and the live simulation in Task 8 Step 4.
- **Deliberately out of scope:** ripping-while-prompting (the prompt parks the job before rip, matching the NamePromptModal precedent; rip-during-prompt would need a matching gate on user input and watchdog-starvation care — possible follow-up), the LLM matcher returning confidence 0 on obviously-correct transcripts, and first-track season-pinning.

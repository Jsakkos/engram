# Subtitle Cache Harvester Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the subtitle cache builder incapable of recording an infrastructure failure (quota exhaustion, provider outage) as a content fact, and incapable of exceeding the daily OpenSubtitles quota.

**Architecture:** Six focused changes across three files. Decision logic is extracted into pure functions (`_stop_reason`, `_is_degraded`) that are unit-tested directly, with thin wiring at the call sites. The coverage tracker's two lookup paths get independent staleness semantics: failures still expire (retry later), successes never do (the SRTs are on disk). A standalone maintenance script purges the coverage rows written during the known-bad window.

**Tech Stack:** Python 3.11+, pytest, SQLite via `tmdb_persistent_cache`, `uv` for all commands.

**Background:** Full diagnosis in `docs/superpowers/specs/2026-08-31-subtitle-cache-expansion-design.md`. Summary: a two-day OpenSubtitles quota failure on 2026-06-11/12 was recorded as genuine zero coverage, skip-listing 664 seasons. Healthy-window coverage was 94.3%, the outage window 14.5%.

**Scope:** This plan is Phase 1 only (harvester repair). Phase 2 (English-only curation) and Phase 3 (server deployment) get their own plans once Phase 1 has run and produced honest measurements.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/matcher/coverage_tracker.py` | Persistent per-season coverage records | Modify: `is_done` stops expiring |
| `backend/tests/unit/test_coverage_tracker.py` | Coverage tracker tests | Modify: invert `test_outside_window_not_done` |
| `backend/app/matcher/testing_service.py` | Subtitle download orchestration, OS client + quota state | Modify: add `_is_degraded`, `degraded` in result, fix `by_source` attribution |
| `backend/tests/unit/test_testing_service_degraded.py` | Degraded-detection tests | Create |
| `backend/scripts/build_subtitle_cache.py` | Cache build driver | Modify: `_stop_reason`, `--max-downloads`, `--quota-floor`, skip `record()` when degraded |
| `backend/tests/unit/test_build_subtitle_cache.py` | Build script tests | Modify: add stop-reason and degraded-suppression tests |
| `backend/scripts/purge_poisoned_coverage.py` | One-time maintenance: delete outage-window rows | Create |
| `backend/tests/unit/test_purge_poisoned_coverage.py` | Purge script tests | Create |

---

### Task 1: Stop `is_done` expiring on a timer

**Why:** `is_done()` and `should_skip()` both call `_fresh_record()` with the same `skip_window_days`. For a failure, 30-day expiry is a sensible retry. For a success, expiry disables the complete-on-disk fast path, so the next run re-enumerates the entire corpus. That oversized run is what hit the quota wall. `--refresh` already exists for the "providers may have added episodes" case, so the timer buys nothing.

**Files:**
- Modify: `backend/app/matcher/coverage_tracker.py:76-95`
- Test: `backend/tests/unit/test_coverage_tracker.py:91-100`

- [ ] **Step 1: Rewrite the test that asserts the old behavior**

Replace `test_outside_window_not_done` in `backend/tests/unit/test_coverage_tracker.py` (currently at line 91) with:

```python
    def test_outside_window_still_done(self, monkeypatch):
        """A covered season does NOT expire on a timer. The SRTs are on disk;
        age says nothing about whether they are still there. Expiry here used
        to disable the complete-on-disk fast path, forcing a full re-harvest
        every 30 days -- the oversized run that exhausted the daily quota.
        Use --refresh to deliberately re-harvest for newly-added episodes."""
        forty_days_ago = time.time() - 40 * 86400
        monkeypatch.setattr(coverage_tracker.time, "time", lambda: forty_days_ago)
        coverage_tracker.record(6, 1, total=10, covered=10)  # 100%
        monkeypatch.undo()
        done, prev = coverage_tracker.is_done(6, 1, min_ratio=0.6, window_days=30)
        assert done is True
        assert prev["coverage_ratio"] == pytest.approx(1.0)

    def test_below_threshold_outside_window_still_not_done(self, monkeypatch):
        """Age-independence must not promote a FAILED season to done."""
        forty_days_ago = time.time() - 40 * 86400
        monkeypatch.setattr(coverage_tracker.time, "time", lambda: forty_days_ago)
        coverage_tracker.record(7, 1, total=10, covered=1)  # 10%
        monkeypatch.undo()
        done, prev = coverage_tracker.is_done(7, 1, min_ratio=0.6, window_days=30)
        assert done is False
        assert prev is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_coverage_tracker.py::TestIsDone -v
```

Expected: `test_outside_window_still_done` FAILS with `assert False is True`. `test_below_threshold_outside_window_still_not_done` PASSES already (it is a guard against over-correcting).

- [ ] **Step 3: Add an age-independent lookup helper**

In `backend/app/matcher/coverage_tracker.py`, add immediately after `_fresh_record` (after line 53):

```python
def _any_age_record(tmdb_id: int, season: int) -> dict[str, Any] | None:
    """Return the coverage row for ``(tmdb_id, season)`` regardless of age.

    ``_fresh_record``'s age gate is correct for ``should_skip`` (a failed
    season deserves a retry eventually) and wrong for ``is_done`` (a season
    whose SRTs are on disk does not become un-harvested by the passage of
    time). Splitting the two lookups is what stops the periodic full
    re-sweep; see the 2026-08-31 harvester-repair spec.
    """
    conn = tmdb_persistent_cache.get_conn()
    row = conn.execute(
        "SELECT attempted_at, total_episodes, covered_episodes, coverage_ratio "
        "FROM subtitle_coverage WHERE tmdb_id = ? AND season = ?",
        (tmdb_id, season),
    ).fetchone()
    if row is None:
        return None

    attempted_at, total, covered, ratio = row
    return {
        "attempted_at": attempted_at,
        "total_episodes": total,
        "covered_episodes": covered,
        "coverage_ratio": ratio,
    }
```

- [ ] **Step 4: Point `is_done` at the new helper**

In `backend/app/matcher/coverage_tracker.py`, replace the body and docstring of `is_done` (lines 76-95) with:

```python
def is_done(
    tmdb_id: int,
    season: int,
    min_ratio: float,
    window_days: int = 30,
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(done, prior_row)``.

    ``done`` is True iff a recorded attempt reached ``min_ratio`` -- i.e. the
    season already hit the coverage threshold and can be shipped from the SRTs
    already on disk without re-hitting TMDB/OpenSubtitles/scrapers.

    Deliberately age-independent: ``window_days`` is accepted for call-site
    compatibility and ignored. Success does not decay. Expiring it forced a
    full re-harvest of the whole corpus every 30 days, which exhausted the
    daily OpenSubtitles quota and caused every subsequent season to be
    recorded as zero-coverage. Pass ``--refresh`` to the build script to
    re-harvest deliberately.

    The caller still verifies the SRTs are physically present
    (``discover_season_srts``) and falls through to a normal harvest if they
    are not, so a wiped cache directory self-heals.
    """
    row = _any_age_record(tmdb_id, season)
    if row is None or row["coverage_ratio"] < min_ratio:
        return False, None
    return True, row
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_coverage_tracker.py -v
```

Expected: all PASS, including the pre-existing `TestShouldSkip::test_outside_skip_window_not_skipped` (proving failures still expire).

- [ ] **Step 6: Verify the build script's callers still pass**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py -v
```

Expected: all PASS. If `tests/unit/test_build_subtitle_cache.py:214` fails, read it: it asserts `is_done()` gates the fast path, which is still true; only staleness changed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/matcher/coverage_tracker.py backend/tests/unit/test_coverage_tracker.py
git commit -m "fix(subtitle-cache): stop is_done expiring on a timer

A covered season does not become un-harvested by the passage of time.
Expiry disabled the complete-on-disk fast path every 30 days, forcing a
full corpus re-harvest that exhausted the daily OpenSubtitles quota.
should_skip keeps its window so failures are still retried."
```

---

### Task 2: Extract the run-stop decision as a pure function

**Why:** The quota is checked only at login (`_get_os_client`). `_snapshot_os_quota` keeps updating `user_downloads_remaining` as downloads proceed, but nothing reads it, so a run that starts at 1,000 sails past zero. Two independent limits are needed: an absolute floor (protects an account that started the day partially used) and a per-run budget (`--max-downloads`).

**Files:**
- Modify: `backend/scripts/build_subtitle_cache.py`
- Test: `backend/tests/unit/test_build_subtitle_cache.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_build_subtitle_cache.py`:

```python
@pytest.mark.unit
class TestStopReason:
    """``_stop_reason`` is the pure decision behind the run's quota guard.
    Returns None to continue, or a human-readable reason to stop."""

    def test_none_when_within_budget_and_above_floor(self):
        assert bsc._stop_reason(baseline=1000, remaining=800, max_downloads=900, floor=10) is None

    def test_stops_when_budget_spent(self):
        reason = bsc._stop_reason(baseline=1000, remaining=100, max_downloads=900, floor=10)
        assert reason is not None
        assert "budget" in reason.lower()

    def test_stops_when_at_floor_even_if_budget_remains(self):
        """An account that began the day partially used hits the floor long
        before the per-run budget. This is the case the login-only check missed."""
        reason = bsc._stop_reason(baseline=200, remaining=8, max_downloads=900, floor=10)
        assert reason is not None
        assert "quota" in reason.lower()

    def test_none_when_quota_unknown(self):
        """No OpenSubtitles credentials means no quota telemetry. Scrapers
        have no daily cap, so an unknown quota must not halt the run."""
        assert bsc._stop_reason(baseline=None, remaining=None, max_downloads=900, floor=10) is None

    def test_none_when_baseline_unknown_but_above_floor(self):
        assert bsc._stop_reason(baseline=None, remaining=500, max_downloads=900, floor=10) is None

    def test_stops_at_floor_when_baseline_unknown(self):
        reason = bsc._stop_reason(baseline=None, remaining=0, max_downloads=900, floor=10)
        assert reason is not None
```

If `bsc` is not already imported in that file, add near the top (match the existing import style used by the file's other tests):

```python
from scripts import build_subtitle_cache as bsc
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py::TestStopReason -v
```

Expected: FAIL with `AttributeError: module 'scripts.build_subtitle_cache' has no attribute '_stop_reason'`.

- [ ] **Step 3: Implement `_stop_reason`**

In `backend/scripts/build_subtitle_cache.py`, add immediately after the `RunTally` class (after its `cache_hit_rate` property, before `_ensure_db_schema`):

```python
class QuotaExhausted(Exception):
    """Raised to end a run cleanly when the download budget or quota floor is hit.

    Carries the human-readable reason so ``main`` can log it and exit non-zero
    without unwinding through a bare ``Exception`` handler.
    """


def _stop_reason(
    baseline: int | None,
    remaining: int | None,
    max_downloads: int,
    floor: int,
) -> str | None:
    """Return why the run must stop, or None to continue.

    Two independent limits:

    * ``floor`` -- an absolute guard on remaining daily quota. Catches the
      account that began the day partially spent, where the per-run budget
      would never trip.
    * ``max_downloads`` -- this run's own budget, measured as
      ``baseline - remaining``. Leaves headroom so a scheduled build cannot
      consume the entire daily allowance.

    ``remaining is None`` means no OpenSubtitles telemetry (no credentials, or
    the probe failed). Scrapers carry no daily cap, so an unknown quota must
    never halt the run -- returning a stop here would break credential-free
    builds entirely.
    """
    if remaining is None:
        return None

    if remaining <= floor:
        return (
            f"OpenSubtitles daily quota nearly exhausted ({remaining} remaining, "
            f"floor {floor}); stopping before failed downloads get recorded as "
            "zero coverage"
        )

    if baseline is not None and (baseline - remaining) >= max_downloads:
        return (
            f"run download budget spent ({baseline - remaining}/{max_downloads}); "
            "stopping cleanly"
        )

    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py::TestStopReason -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/build_subtitle_cache.py backend/tests/unit/test_build_subtitle_cache.py
git commit -m "feat(subtitle-cache): add _stop_reason quota guard decision function

Pure function behind the run-stop guard: an absolute quota floor plus a
per-run download budget. Unknown quota never halts the run so
credential-free scraper builds are unaffected."
```

---

### Task 3: Wire the stop guard into the harvest loop

**Why:** `_stop_reason` is inert until something calls it after each season.

**Files:**
- Modify: `backend/scripts/build_subtitle_cache.py` (argparse block at 405-467, `_harvest_show` at 240-402, `main` loop at 543-577)

- [ ] **Step 1: Add the CLI flags**

In `main()`'s argparse block, immediately after the `--skip-window-days` argument (ends line 467), add:

```python
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=900,
        help=(
            "Stop the run after this many OpenSubtitles downloads (default: 900). "
            "Leaves headroom under the 1000/day VIP cap so a scheduled build "
            "cannot exhaust the daily allowance and start recording false zeros."
        ),
    )
    parser.add_argument(
        "--quota-floor",
        type=int,
        default=10,
        help=(
            "Stop the run when remaining daily OpenSubtitles quota drops to this "
            "(default: 10). Independent of --max-downloads: catches an account "
            "that began the day partially spent."
        ),
    )
```

Then validate them immediately after `args = parser.parse_args()`, because `_stop_reason` treats a non-positive budget as "stop immediately" rather than "unlimited", and zero-as-unlimited is a common enough CLI idiom that someone will try it:

```python
    if args.max_downloads <= 0:
        parser.error("--max-downloads must be positive (0 does not mean unlimited)")
    if args.quota_floor < 0:
        parser.error("--quota-floor cannot be negative")
```

- [ ] **Step 2: Record the quota baseline**

`main()` probes the quota around line 494-499, but only inside the branch that runs when OpenSubtitles credentials are configured. The name must exist unconditionally, so initialise it first.

Immediately BEFORE the `remaining = probe_os_quota(config)` line, add:

```python
    quota_baseline: int | None = None
    halted_reason: str | None = None
```

Then immediately AFTER the existing `logger.info(f"OpenSubtitles quota: ...")` call inside that branch, add (matching that block's indentation):

```python
        quota_baseline = remaining
```

Declaring both names at function scope before the branch keeps them defined on every path, including credential-free scraper-only builds where the probe never runs.

- [ ] **Step 3: Check the guard after each show**

In `main()`'s show loop, immediately after `progress.advance(shows_task)` (line 576), add:

```python
            remaining_now = (get_last_quota() or {}).get("remaining")

            # Re-baseline on a mid-run quota refill. These runs span hours and
            # the OpenSubtitles bucket resets daily, so a run that crosses the
            # reset sees `remaining` rise above the baseline. Without this,
            # `baseline - remaining` goes negative, the budget branch stops
            # firing for the rest of the run, and only the floor is left --
            # a run started on a partially-spent account could then draw the
            # entire fresh allowance while the operator believes
            # --max-downloads capped it.
            if remaining_now is not None and (
                quota_baseline is None or remaining_now > quota_baseline
            ):
                quota_baseline = remaining_now

            stop = _stop_reason(
                quota_baseline,
                remaining_now,
                args.max_downloads,
                args.quota_floor,
            )
            if stop:
                console.log(f"[yellow]STOP[/] {stop}")
                logger.warning(f"Halting run: {stop}")
                # Raised from main()'s show loop, NOT from inside
                # _harvest_show: that function wraps download_subtitles in a
                # broad `except Exception` (around line 391) which would
                # swallow this halt and log it as a per-season warning. If the
                # guard is ever moved to a per-season check, narrow that
                # handler first.
                raise QuotaExhausted(stop)
```

- [ ] **Step 4: Catch it and package what was harvested**

Wrap the `for idx, show in enumerate(shows, 1):` loop body's enclosing `for` statement in a try/except inside the `with Progress(...)` block. The `except` must sit at the same indentation as the `for`:

```python
        try:
            for idx, show in enumerate(shows, 1):
                ...  # existing loop body, unchanged
        except QuotaExhausted as e:
            halted_reason = str(e)
```

`halted_reason` was already initialised at function scope in Step 2. Packaging continues normally after the loop, so a halted run still publishes everything it did harvest.

- [ ] **Step 5: Report the halt and exit non-zero**

At the end of `main()`, immediately before its final `return 0`, add:

```python
    if halted_reason:
        logger.warning(
            f"Run halted early: {halted_reason}. Harvested shows were still "
            "packaged; re-run after the daily quota resets to continue."
        )
        return 2
```

- [ ] **Step 6: Verify the script still starts and the flags are wired**

```bash
cd backend && uv run python scripts/build_subtitle_cache.py --help | grep -A3 "max-downloads\|quota-floor"
```

Expected: both flags appear with their help text.

- [ ] **Step 7: Run the full build-script test file**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/build_subtitle_cache.py
git commit -m "feat(subtitle-cache): halt the run on quota floor or download budget

Checks _stop_reason after every show. A halted run still packages what it
harvested and exits 2, so a scheduled build makes monotonic progress
instead of marching past zero quota recording false zeros."
```

---

### Task 4: Detect degraded seasons in `download_subtitles`

**Why:** `coverage_tracker.record()` cannot distinguish "the provider has nothing" from "we got nothing". The caller needs that signal, and only `download_subtitles` knows it.

**Files:**
- Modify: `backend/app/matcher/testing_service.py` (return dict at 800-806)
- Create: `backend/tests/unit/test_testing_service_degraded.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_testing_service_degraded.py`:

```python
"""Tests for degraded-season detection.

A "degraded" season is one where the measurement is not trustworthy: the
OpenSubtitles path was unavailable AND nothing was retrieved. Recording 0%
coverage for such a season skip-lists it for 30 days on the strength of an
infrastructure failure -- the defect that cost 664 seasons in June 2026.
"""

import pytest

from app.matcher.testing_service import _is_degraded


@pytest.mark.unit
class TestIsDegraded:
    def test_not_degraded_when_episodes_retrieved(self):
        episodes = [
            {"code": "S01E01", "status": "downloaded", "path": "/x.srt", "source": "addic7ed"},
            {"code": "S01E02", "status": "not_found", "path": None, "source": None},
        ]
        assert _is_degraded(os_failed=True, episodes=episodes) is False

    def test_not_degraded_when_os_healthy_and_nothing_found(self):
        """OpenSubtitles worked and genuinely had nothing. That is a real
        measurement and SHOULD be recorded, so the season gets skip-listed."""
        episodes = [{"code": "S01E01", "status": "not_found", "path": None, "source": None}]
        assert _is_degraded(os_failed=False, episodes=episodes) is False

    def test_degraded_when_os_failed_and_nothing_found(self):
        episodes = [
            {"code": "S01E01", "status": "not_found", "path": None, "source": None},
            {"code": "S01E02", "status": "not_found", "path": None, "source": None},
        ]
        assert _is_degraded(os_failed=True, episodes=episodes) is True

    def test_cached_episodes_count_as_retrieved(self):
        episodes = [{"code": "S01E01", "status": "cached", "path": "/x.srt", "source": "cache"}]
        assert _is_degraded(os_failed=True, episodes=episodes) is False

    def test_empty_episode_list_with_os_failed_is_degraded(self):
        assert _is_degraded(os_failed=True, episodes=[]) is True

    def test_empty_episode_list_with_os_healthy_is_not_degraded(self):
        assert _is_degraded(os_failed=False, episodes=[]) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_testing_service_degraded.py -v
```

Expected: FAIL with `ImportError: cannot import name '_is_degraded'`.

- [ ] **Step 3: Implement `_is_degraded`**

In `backend/app/matcher/testing_service.py`, add immediately after `get_last_quota` (after line 129):

```python
def _is_degraded(os_failed: bool, episodes: list[dict]) -> bool:
    """Return True when this season's result is NOT a trustworthy measurement.

    Degraded means the OpenSubtitles path was unavailable (exhausted quota,
    login failure, hard error) AND the scraper cascade retrieved nothing. In
    that state a 0% result says nothing about whether subtitles exist, so the
    caller must not persist it as coverage: ``coverage_tracker.record(0.0)``
    skip-lists the season for 30 days, converting a transient outage into a
    month-long blind spot.

    Conservative by design. If anything at all was retrieved, or if
    OpenSubtitles was healthy and simply had nothing, the measurement is real
    and SHOULD be recorded so genuinely dead seasons stop consuming quota.
    """
    if not os_failed:
        return False
    return not any(ep.get("status") in ("cached", "downloaded") for ep in episodes)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_testing_service_degraded.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Add `degraded` to the returned dict**

In `backend/app/matcher/testing_service.py`, replace the return statement at lines 800-806 with:

```python
    return {
        "show_name": canonical_show_name,
        "season": season,
        "total_episodes": episode_count,
        "episodes": episodes,
        "cache_dir": str(series_cache_dir),
        # True when this result is not a trustworthy measurement -- see
        # _is_degraded. The cache builder skips coverage recording for these.
        "degraded": _is_degraded(_OS.failed, episodes),
    }
```

- [ ] **Step 6: Run the broader subtitle test files for regressions**

```bash
cd backend && uv run pytest tests/unit/test_download_subtitles_tmdb_id.py tests/unit/test_subtitle_dedup.py tests/unit/test_testing_service_degraded.py -v
```

Expected: all PASS. The new key is additive, so existing consumers that index known keys are unaffected.

- [ ] **Step 7: Commit**

```bash
git add backend/app/matcher/testing_service.py backend/tests/unit/test_testing_service_degraded.py
git commit -m "feat(subtitle-cache): flag degraded seasons in download_subtitles

A season where OpenSubtitles was unavailable AND nothing was retrieved is
not a measurement. Surfacing it lets the builder decline to record coverage
rather than skip-listing the season for 30 days on an outage."
```

---

### Task 5: Suppress coverage recording for degraded seasons

**Why:** This is the defect that cost 664 seasons. `_harvest_show` records unconditionally.

**Files:**
- Modify: `backend/scripts/build_subtitle_cache.py:376-383`
- Test: `backend/tests/unit/test_build_subtitle_cache.py`

- [ ] **Step 1: Write the failing regression test**

Append to `backend/tests/unit/test_build_subtitle_cache.py`:

```python
@pytest.mark.unit
class TestDegradedSuppressesCoverageRecord:
    """The specific regression that cost 664 seasons in June 2026: a quota
    failure was written to subtitle_coverage as genuine zero coverage, which
    skip-listed those seasons for 30 days."""

    def test_degraded_season_is_not_recorded(self):
        assert bsc._should_record_coverage({"degraded": True, "episodes": []}) is False

    def test_degraded_season_with_partial_episodes_is_not_recorded(self):
        """Degraded is decided upstream by _is_degraded; the builder trusts the
        flag rather than re-deriving it from the episode list."""
        episodes = [{"code": "S01E01", "status": "not_found", "path": None, "source": None}]
        assert bsc._should_record_coverage({"degraded": True, "episodes": episodes}) is False

    def test_healthy_empty_season_still_records(self):
        """OpenSubtitles worked and had nothing: a real measurement. Recording
        it is what stops dead seasons consuming quota every single day."""
        assert bsc._should_record_coverage({"degraded": False, "episodes": []}) is True

    def test_missing_degraded_key_defaults_to_recording(self):
        """Backwards compatibility: a result dict without the key (e.g. the
        precomputed-skip fast path) must not silently stop being recorded."""
        assert bsc._should_record_coverage({"episodes": []}) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py::TestDegradedSuppressesCoverageRecord -v
```

Expected: FAIL with `AttributeError: module 'scripts.build_subtitle_cache' has no attribute '_should_record_coverage'`.

- [ ] **Step 3: Implement `_should_record_coverage`**

In `backend/scripts/build_subtitle_cache.py`, add immediately after `_stop_reason`:

```python
def _should_record_coverage(result: dict) -> bool:
    """Return whether this season's outcome is worth persisting as coverage.

    Defaults to True when the key is absent so paths that build their own
    result dict (e.g. the precomputed-cache skip) keep their existing
    behaviour rather than silently dropping out of the coverage record.
    """
    return not result.get("degraded", False)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py::TestDegradedSuppressesCoverageRecord -v
```

Expected: 3 passed.

- [ ] **Step 5: Gate the record call**

In `backend/scripts/build_subtitle_cache.py`, replace lines 379-383 (the comment block and the `coverage_tracker.record(...)` call) with:

```python
        # Record EVERY season we actually MEASURED (success or genuinely
        # below-threshold). This powers the skip-list on the next run -- without
        # it, low-coverage seasons get re-attempted every day, burning quota on
        # shows that simply don't have subtitles.
        #
        # A degraded season is NOT a measurement: OpenSubtitles was unavailable
        # and nothing was retrieved, so 0% says nothing about availability.
        # Recording it would skip-list the season for 30 days on the strength of
        # an outage. That defect cost 664 seasons on 2026-06-12; see the
        # harvester-repair spec.
        if _should_record_coverage(result):
            coverage_tracker.record(show["tmdb_id"], season, total, len(episodes))
        else:
            logger.warning(
                f"  {canonical} S{season:02d}: degraded result "
                f"(OpenSubtitles unavailable, nothing retrieved); "
                "NOT recording coverage so the season is re-measured next run"
            )
```

- [ ] **Step 6: Surface a degradation count in the run summary**

A misconfigured run (wrong OpenSubtitles credentials, or the `opensubtitlescom` package missing) sets `_OS.failed` process-wide, so every empty season becomes degraded and NOTHING gets recorded for the whole run. That is correct by the predicate's logic but silent, and a silently-empty skip-list looks identical to a healthy run in the logs.

Add a `seasons_degraded` counter to `RunTally`, increment it in the `else` branch added in Step 5, and include it in the final summary block alongside `seasons skipped`. If it is large relative to the seasons attempted, that is the signal that OpenSubtitles is misconfigured rather than that the content is missing.

- [ ] **Step 7: Share the retrieved-status allowlist**

`_is_degraded` uses `("cached", "downloaded")` and `_VALID_STATUSES` at the top of this file encodes the same concept. Producer and consumer defining "retrieved" separately means a future third status added to one and not the other silently changes coverage semantics without failing anything.

Import `_VALID_STATUSES`'s definition from one place. Since `testing_service` is the producer, export the set from there and have the build script use it, keeping the local name as an alias if that reads better at the existing call sites.

- [ ] **Step 8: Run the full build-script test file**

```bash
cd backend && uv run pytest tests/unit/test_build_subtitle_cache.py -v
```

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/scripts/build_subtitle_cache.py backend/tests/unit/test_build_subtitle_cache.py
git commit -m "fix(subtitle-cache): never record coverage for a degraded season

The defect that cost 664 seasons: a quota failure was persisted as genuine
zero coverage and skip-listed those seasons for 30 days. No record beats a
false zero -- absence means 'measure later', 0.0 means 'never retry'."
```

---

### Task 6: Credit OpenSubtitles in `by_source`

**Why:** The OS bulk path moves each SRT to its final target on disk, then the per-episode triage loop checks `find_existing_subtitle` FIRST and classifies the just-downloaded file as `cached`/`source: cache`. The `if episode in api_srt_map` branch is effectively dead for freshly-downloaded episodes. The ER diagnostic reported `new downloads: 1` while consuming 94 quota, hiding the June quota wall from the logs.

**Files:**
- Modify: `backend/app/matcher/testing_service.py:684-701` and `718-751`

- [ ] **Step 1: Track which episodes were downloaded this run**

In `backend/app/matcher/testing_service.py`, immediately after `api_srt_map: dict[int, Path] = {}` (line 598), add:

```python
    # Episodes DOWNLOADED from the API this run, as opposed to ones api_srt_map
    # merely points at because the file already existed. Without this split the
    # triage loop below sees the freshly-moved file on disk and misreports the
    # download as a cache hit, so by_source under-reports OpenSubtitles usage by
    # ~99% and a quota wall is invisible in the run summary.
    api_fresh_eps: set[int] = set()
```

- [ ] **Step 2: Populate it on actual download**

In the OS bulk block, immediately after `api_srt_map[ep_num] = srt_target` inside the `if srt_file and is_valid_srt_file(Path(srt_file)):` branch (line 697), add:

```python
                            api_fresh_eps.add(ep_num)
```

Do NOT add it to the `else:` branch at line 700 (`api_srt_map[ep_num] = srt_target` for a pre-existing file) -- that one is a genuine cache hit.

- [ ] **Step 3: Check freshly-downloaded episodes before the cache check**

In the per-episode triage loop, insert this block immediately after `episode_code = f"S{season:02d}E{episode:02d}"` and the `srt_path = ...` assignment, BEFORE the `existing_subtitle = find_existing_subtitle(...)` call (before line 727):

```python
        if episode in api_fresh_eps:
            pre_resolved[episode] = {
                "code": episode_code,
                "status": "downloaded",
                "path": str(api_srt_map[episode]),
                "source": "opensubtitles_api",
            }
            continue
```

- [ ] **Step 4: Verify no regressions**

```bash
cd backend && uv run pytest tests/unit/test_download_subtitles_tmdb_id.py tests/unit/test_subtitle_dedup.py tests/unit/test_testing_service_degraded.py -v
```

Expected: all PASS.

- [ ] **Step 5: Verify against a live single-season harvest**

```bash
cd backend && uv run python scripts/build_subtitle_cache.py --shows "Firefly" --sleep 0.5 --output /tmp/verify-cache.tar.gz 2>&1 | tail -25
```

Expected: the final summary's `by source:` line credits `opensubtitles_api=N` with N greater than zero if any episode was newly downloaded, and the drop in `OS quota left` matches `new downloads`. If Firefly is already fully cached locally, the run reports cache hits and zero new downloads, which is also correct: pick any show absent from `~/.engram/cache/data/` instead.

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/testing_service.py
git commit -m "fix(subtitle-cache): credit opensubtitles_api in by_source

The bulk path moves SRTs to their final target, then the triage loop saw
them on disk and called them cache hits. Run summaries under-reported
OpenSubtitles usage by ~99%, which is why the June quota wall was invisible."
```

---

### Task 7: One-time purge of the poisoned coverage window

**Why:** 978 season rows written from 2026-06-11 onward are known-bad (14.5% measured vs 94.3% in the healthy window). They are already inert by age, but they corrupt any outcome-based curation in Phase 2. Deleting converts known-bad data into no-data, which the repaired harvester refills honestly.

**Files:**
- Create: `backend/scripts/purge_poisoned_coverage.py`
- Create: `backend/tests/unit/test_purge_poisoned_coverage.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_purge_poisoned_coverage.py`:

```python
"""Tests for the one-time poisoned-coverage purge."""

import importlib.util
import time
from pathlib import Path

import pytest

from app.matcher import coverage_tracker

_spec = importlib.util.spec_from_file_location(
    "purge_poisoned_coverage",
    Path(__file__).parent.parent.parent / "scripts" / "purge_poisoned_coverage.py",
)
purge_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge_mod)


@pytest.mark.unit
class TestPurge:
    def test_dry_run_deletes_nothing(self):
        coverage_tracker.record(100, 1, total=20, covered=0)
        n = purge_mod.purge(since=0.0, min_ratio=0.6, apply=False)
        assert n == 1
        assert coverage_tracker.get_show_coverage(100) != []

    def test_apply_deletes_low_ratio_rows_after_cutoff(self):
        coverage_tracker.record(101, 1, total=20, covered=0)
        n = purge_mod.purge(since=0.0, min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(101) == []

    def test_high_coverage_rows_are_preserved(self):
        """A season that succeeded during the bad window is still a real
        success -- the outage suppressed results, it did not fabricate them."""
        coverage_tracker.record(102, 1, total=20, covered=20)
        purge_mod.purge(since=0.0, min_ratio=0.6, apply=True)
        assert coverage_tracker.get_show_coverage(102) != []

    def test_rows_before_cutoff_are_preserved(self):
        """Healthy-window data is the ground truth Phase 2 curation depends on."""
        coverage_tracker.record(103, 1, total=20, covered=0)
        future_cutoff = time.time() + 3600
        n = purge_mod.purge(since=future_cutoff, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(103) != []

    def test_idempotent(self):
        coverage_tracker.record(104, 1, total=20, covered=0)
        purge_mod.purge(since=0.0, min_ratio=0.6, apply=True)
        assert purge_mod.purge(since=0.0, min_ratio=0.6, apply=True) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_purge_poisoned_coverage.py -v
```

Expected: FAIL at import, `FileNotFoundError` for `purge_poisoned_coverage.py`.

- [ ] **Step 3: Write the purge script**

Create `backend/scripts/purge_poisoned_coverage.py`:

```python
"""One-time maintenance: delete coverage rows written during a known-bad window.

On 2026-06-11/12 an OpenSubtitles quota exhaustion caused the cache builder to
record 664 seasons as zero coverage. Measured rates either side of the cutoff:
94.3% before, 14.5% after. Those rows are already inert (expired by age), but
they corrupt outcome-based show curation, which reads historical coverage to
decide what to keep.

Deleting converts known-bad data into no-data, which the repaired harvester
refills honestly. Legitimately-zero seasons inside the window are re-measured;
that costs quota once and is the point.

Defaults to a dry run. Pass --apply to mutate.

Usage (from backend/):
    uv run python scripts/purge_poisoned_coverage.py
    uv run python scripts/purge_poisoned_coverage.py --apply
"""

import argparse
import datetime
import sys
from pathlib import Path

_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.matcher import tmdb_persistent_cache  # noqa: E402

# 2026-06-11 00:00 local. The first run whose success rate collapsed; see the
# 2026-08-31 harvester-repair spec for the per-day breakdown.
DEFAULT_CUTOFF = datetime.datetime(2026, 6, 11).timestamp()


def purge(since: float, min_ratio: float, apply: bool) -> int:
    """Delete (or count, when ``apply`` is False) poisoned coverage rows.

    A row is poisoned when it was attempted at or after ``since`` AND fell
    below ``min_ratio``. Rows that succeeded during the window are preserved:
    the outage suppressed results, it did not fabricate them.

    Returns the number of rows matched.
    """
    if not tmdb_persistent_cache.CACHE_DB_PATH.exists():
        return 0

    conn = tmdb_persistent_cache.get_conn()
    matched = conn.execute(
        "SELECT COUNT(*) FROM subtitle_coverage "
        "WHERE attempted_at >= ? AND coverage_ratio < ?",
        (since, min_ratio),
    ).fetchone()[0]

    if apply and matched:
        conn.execute(
            "DELETE FROM subtitle_coverage WHERE attempted_at >= ? AND coverage_ratio < ?",
            (since, min_ratio),
        )
        conn.commit()

    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=str,
        default="2026-06-11",
        help="Cutoff date YYYY-MM-DD; rows attempted on/after this are candidates",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.6,
        help="Rows below this coverage ratio are treated as poisoned (default: 0.6)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete (default: dry run)"
    )
    args = parser.parse_args()

    since = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    n = purge(since, args.min_ratio, args.apply)

    verb = "Deleted" if args.apply else "Would delete (dry run)"
    print(f"{verb} {n} coverage rows attempted on/after {args.since} below {args.min_ratio:.0%}")
    if not args.apply and n:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_purge_poisoned_coverage.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Dry-run against the real cache and confirm the count**

```bash
cd backend && uv run python scripts/purge_poisoned_coverage.py
```

Expected: reports roughly 900 to 980 rows. If it reports zero, the cache DB path differs from `~/.engram/cache/tmdb_cache.sqlite`; investigate before proceeding rather than passing `--apply`.

- [ ] **Step 6: Commit (do NOT run --apply yet)**

```bash
git add backend/scripts/purge_poisoned_coverage.py backend/tests/unit/test_purge_poisoned_coverage.py
git commit -m "feat(subtitle-cache): add one-time poisoned-coverage purge

Deletes coverage rows from the 2026-06-11 onward outage window that fell
below threshold. Known-bad data becomes no-data, which the repaired
harvester refills honestly. Dry-run by default."
```

The actual `--apply` run is a data-mutation step held for explicit user approval; it is listed in Verification below, not performed as part of implementation.

---

## Verification

- [ ] **Full unit suite**

```bash
cd backend && uv run pytest tests/unit/ -q
```

Expected: no failures. Per the project memory note, avoid `-p no:logging` here: it errors caplog-based tests.

- [ ] **Lint and format**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

Expected: clean.

- [ ] **End-to-end recovery proof on a known-poisoned show**

Full House (tmdb 4313) recorded 1/192 episodes across S01 to S08, all on 2026-06-12. After the repair it should recover the way ER did (330/331 on 2026-08-31).

```bash
cd backend && printf 'rank,tmdb_id,name\n1,4313,Full House\n' > /tmp/fh.csv && uv run python scripts/build_subtitle_cache.py --show-list /tmp/fh.csv --sleep 0.5 --max-downloads 250 --output /tmp/fh-cache.tar.gz 2>&1 | tail -25
```

Expected: seasons report substantial coverage rather than 0/22, and the summary's `by source` credits `opensubtitles_api`. The `--max-downloads 250` cap bounds the quota spend for this check.

- [ ] **User-approved data mutation: purge the poisoned window**

```bash
cd backend && uv run python scripts/purge_poisoned_coverage.py --apply
```

This is irreversible and returns ~978 seasons to the unmeasured pool, costing several days of quota to re-measure. Hold for explicit approval.

---

## Known gap, deliberately out of scope: the scrapers have no failure signal

`_is_degraded` covers the OpenSubtitles half of the cascade only. The scraper
scheduler (`app/matcher/provider_scheduler.py`) emits exactly two per-episode
statuses, `downloaded` and `not_found`, and its `_CircuitBreaker` trips after three
consecutive transport failures and thereafter skips the provider without changing
the result shape.

So if OpenSubtitles is healthy but BOTH Addic7ed and TVsubtitles are down, every
episode comes back `not_found`, `os_failed` is False, the season is recorded at 0%,
and it is skip-listed for 30 days. That is the same defect this plan repairs,
reachable through a different door.

Closing it requires `run_jobs` to surface transport-failure or breaker-open state,
which changes the scheduler's return contract and every consumer of it. That is a
separate task, not a widening of this one. Recorded here while the analysis is fresh
so it is not rediscovered later as a fresh mystery.

## Known gap: a persistently degraded corpus is re-measured forever

This is the deliberate tradeoff of Task 5 (no record beats a false zero), but the
specific runaway is worth naming because **the guard one would assume bounds it
provably does not**.

A degraded season writes no coverage row, so `should_skip` never suppresses it. If
OpenSubtitles is *misconfigured* rather than transiently down (wrong credentials, or
the `opensubtitlescom` package missing), every season degrades on every run, the
skip-list never forms, and each daily build re-attempts the entire corpus through the
scrapers.

Task 3's quota guard cannot help here. With bad credentials `_get_os_client` returns
None, so `probe_os_quota` returns None, so `get_last_quota()` returns None, so
`_stop_reason` short-circuits on its `remaining is None` rule and never fires. That
short-circuit is correct on its own terms (scrapers have no daily cap, so an unknown
quota must not halt a scraper-only run), but it means the budget is inert in exactly
the scenario that produces the runaway. `--max-downloads` also counts OpenSubtitles
downloads, not scraper fetches.

The only bound is wall-clock and scraper rate limits. Mitigation options (a
degraded-run circuit breaker, or recording a row after N consecutive degraded runs
for the same season) are design decisions rather than bug fixes, so they are left out
of this plan. The `seasons_degraded` counter added in Task 5 is the detection half:
it makes the condition visible so an operator can act before it runs for weeks.

---

## Notes for the implementer

- **Never delete `backend/engram.db`.** It holds API keys that must be re-entered by hand.
- **The dev DB has schema drift.** `init_db()` currently raises `table fingerprint_contributions already exists`, and an earlier probe hit `no such column: app_config.always_review`. The build script survives both, but do not "fix" them inside this plan: they are a separate defect and out of scope.
- **Quota is per-account and shared with any other machine.** Each live harvest consumes the same 1,000/day bucket. Do not run two builds concurrently.
- **`uv run` for every Python command.** Never bare `python` or `pytest`.
- **No em dashes in code comments or commit messages** per project convention; use colons, semicolons, or parentheses.

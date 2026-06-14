# Disc Identification Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make engram correctly identify abbreviated-label TV discs (e.g. `DS9` → "Star Trek: Deep Space Nine"), stop discarding feature-length episodes as Play-All/extras, and send genuinely unconfirmable identities to review instead of silently completing.

**Architecture:** Three changes in `backend/app/core/analyst.py` (a pure, synchronous analysis engine) plus a thin async caller change in `backend/app/services/identification_coordinator.py`. New data (expected episode runtimes) is *passed into* `analyze()` — the Analyst never does I/O. Review escalation reuses the existing `needs_review`/`review_reason` plumbing that already drives the review UI.

**Tech Stack:** Python 3.11, pytest (`uv run pytest`), FastAPI/SQLModel backend. TMDB via `app/matcher/tmdb_client.py` (persistent-cached). Spec: `docs/superpowers/specs/2026-06-14-disc-identification-robustness-design.md`.

**One-time setup (run before Task 1):**

```bash
cd C:/Github/engram/.claude/worktrees/disc-identification/backend
uv sync
uv run pytest tests/unit/test_disc_name_identification.py tests/pipeline/test_play_all_detection.py -q
```
Expected: existing tests PASS (clean baseline). If `uv sync` is heavy (ML deps), it only needs to run once.

---

### Task 1: Fix 1 — Abbreviation-aware name corroboration

Add an initialism/abbreviation acceptance path so an abbreviated disc label corroborates the TMDB name (`DS9` ↔ "Deep Space Nine", with `Nine`→`9`). Fold it into `_names_are_similar`, the single corroboration primitive used by both `analyze()` and `_apply_tmdb_signal`.

**Files:**
- Modify: `backend/app/core/analyst.py` (add helpers after `_names_are_similar` at line ~98; enhance `_names_are_similar` body)
- Test: `backend/tests/unit/test_disc_name_identification.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_disc_name_identification.py`:

```python
# ---------------------------------------------------------------------------
# Fix 1: abbreviation / initialism corroboration (DS9 ↔ Deep Space Nine)
# ---------------------------------------------------------------------------

from app.core.analyst import _abbreviation_matches, _names_are_similar


@pytest.mark.parametrize(
    "label,full_name",
    [
        ("DS9", "Star Trek: Deep Space Nine"),  # number-word Nine -> 9, drop "Star Trek:"
        ("Ds9", "Star Trek: Deep Space Nine"),  # case-insensitive
        ("TNG", "Star Trek: The Next Generation"),  # stopword "The" dropped -> TNG
    ],
)
def test_abbreviation_matches_positive(label, full_name):
    assert _abbreviation_matches(label, full_name) is True


@pytest.mark.parametrize(
    "label,full_name",
    [
        ("DS9", "Star Trek: The Next Generation"),  # ds9 != tng / stng
        ("HOUSE", "Star Trek: Deep Space Nine"),    # has vowels, no digit -> not abbrev-shaped
        ("STRANGENEWWORLDS", "Star Trek: Strange New Worlds"),  # too long (>5) -> not abbrev
        ("D", "Deep Space Nine"),                   # single char -> rejected
    ],
)
def test_abbreviation_matches_negative(label, full_name):
    assert _abbreviation_matches(label, full_name) is False


def test_names_are_similar_uses_abbreviation_path():
    assert _names_are_similar("Ds9", "Star Trek: Deep Space Nine") is True


def test_analyst_adopts_tmdb_name_for_abbreviated_label():
    """DS9S1D1 -> 'Ds9' must corroborate and adopt TMDB 'Star Trek: Deep Space Nine'."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "DS9S1D1", tmdb_signal=tmdb, disc_title="DS9S1D1")

    assert result.detected_name == "Star Trek: Deep Space Nine"
    assert result.tmdb_id == 580
    assert result.content_type == ContentType.TV
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -k "abbreviation or abbreviated" -q`
Expected: FAIL — `ImportError: cannot import name '_abbreviation_matches'`.

- [ ] **Step 3: Implement the helpers and enhance `_names_are_similar`**

In `backend/app/core/analyst.py`, add immediately after `_names_are_similar` (after line ~98):

```python
_NUMBER_WORDS: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_ACRONYM_STOPWORDS: frozenset[str] = frozenset({"the", "of", "and", "a", "an"})


def _abbreviation_matches(label: str, full_name: str) -> bool:
    """True if ``label`` is an initialism/abbreviation of ``full_name``.

    Handles fan-style abbreviations like "DS9" for "Deep Space Nine" (the
    number-word "Nine" maps to the digit "9"), including dropping a franchise
    prefix before a colon ("Star Trek: Deep Space Nine" -> also tries
    "Deep Space Nine").

    Conservative guards prevent false positives: ``label`` must be
    abbreviation-shaped (2-5 alphanumerics, and either contains a digit or has
    no vowels), and the acronym must derive from >= 2 significant words.
    """
    cand = _collapsed(label)
    if not (
        1 < len(cand) <= 5
        and (any(c.isdigit() for c in cand) or not any(v in cand for v in "aeiou"))
    ):
        return False

    variants = [full_name]
    if ":" in full_name:
        variants.append(full_name.split(":", 1)[1])

    for variant in variants:
        words = [
            w
            for w in re.sub(r"[^\w\s]", " ", variant).lower().split()
            if w and w not in _ACRONYM_STOPWORDS
        ]
        if len(words) < 2:
            continue
        letters = "".join(w[0] for w in words)
        digits = "".join(_NUMBER_WORDS.get(w, w[0]) for w in words)
        if cand in (letters, digits):
            return True
    return False
```

Then change the final two lines of `_names_are_similar` (currently):

```python
    a_collapsed, b_collapsed = _collapsed(a), _collapsed(b)
    return bool(a_collapsed) and a_collapsed == b_collapsed
```

to:

```python
    a_collapsed, b_collapsed = _collapsed(a), _collapsed(b)
    if a_collapsed and a_collapsed == b_collapsed:
        return True
    # Abbreviation / initialism path (e.g. "DS9" <-> "Deep Space Nine").
    return _abbreviation_matches(a, b) or _abbreviation_matches(b, a)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -q`
Expected: PASS (new tests pass; all pre-existing tests in the file still pass — the `len(cand) <= 5` guard keeps `Strangenewworlds`-style labels on the existing collapse path).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/unit/test_disc_name_identification.py
git commit -m "feat(analyst): abbreviation-aware TMDB name corroboration (DS9)"
```

---

### Task 2: Fix 3 — Escalate uncorroborated identity to review

When the TMDB name cannot be corroborated (even after Task 1's abbreviation path), flag the job `needs_review` with a candidate-confirming reason instead of silently keeping the disc name. `_apply_tmdb_signal` is the funnel for all heuristic return paths and already re-checks corroboration — the natural injection point. The TMDB-only direct-return path is handled too.

**Files:**
- Modify: `backend/app/core/analyst.py` (`_apply_tmdb_signal` name block at line ~584-590; TMDB-only path at line ~436-446; add a helper near the other module helpers)
- Test: `backend/tests/unit/test_disc_name_identification.py` (append + update two existing tests)

- [ ] **Step 1: Write the failing test and update existing ones**

Append to `backend/tests/unit/test_disc_name_identification.py`:

```python
# ---------------------------------------------------------------------------
# Fix 3: uncorroborated identity escalates to review
# ---------------------------------------------------------------------------


def test_analyst_escalates_review_when_tmdb_uncorroborated():
    """A TMDB name matching neither on-disc signal -> needs_review with a candidate."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=999,
        tmdb_name="Some Unrelated Show",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(), "BREAKINGBADS2", tmdb_signal=tmdb, disc_title="Breaking Bad"
    )

    assert result.needs_review is True
    assert result.review_reason is not None
    assert "Some Unrelated Show" in result.review_reason
    # The base name is kept as the suggestion; TMDB id still attached.
    assert result.detected_name == "Breaking Bad"


def test_analyst_no_review_when_corroborated():
    """A corroborated name (DS9 via abbreviation) must NOT trigger review."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "DS9S1D1", tmdb_signal=tmdb, disc_title="DS9S1D1")

    assert result.needs_review is False
```

Then UPDATE the two existing tests to assert the new escalation (intentional behavior change). In `test_analyst_keeps_base_name_when_tmdb_uncorroborated`, after the existing assertion, add:

```python
    # Fix 3: an uncorroborated TMDB name now escalates to review.
    assert result.needs_review is True
```

In `test_analyst_without_disc_title_keeps_garbled_label_name`, after the existing assertions, add:

```python
    # Fix 3: the concatenated label does not corroborate, so the disc goes to review.
    assert result.needs_review is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -k "uncorroborated or no_review or garbled" -q`
Expected: FAIL — new escalation test fails (`needs_review is False`), and the two updated tests fail on the new assertion.

- [ ] **Step 3: Implement the escalation**

In `backend/app/core/analyst.py`, add this helper next to `_abbreviation_matches`:

```python
def _uncorroborated_review_reason(detected_name: str | None, tmdb_signal) -> str:
    """Build a candidate-confirming review reason for an uncorroborated TMDB name."""
    tid = f" (TMDB #{tmdb_signal.tmdb_id})" if tmdb_signal.tmdb_id else ""
    return (
        f"Couldn't confirm disc '{detected_name}' is "
        f"'{tmdb_signal.tmdb_name}'{tid}. Confirm or correct the title."
    )
```

In `_apply_tmdb_signal`, replace the name-adoption block (currently):

```python
        # Use TMDB name if similar enough to the heuristic name (same guard as analyze())
        if tmdb_signal.tmdb_name:
            if result.detected_name is None or _names_are_similar(
                result.detected_name, tmdb_signal.tmdb_name
            ):
                result.detected_name = tmdb_signal.tmdb_name

        return result
```

with:

```python
        # Use TMDB name if similar enough to the heuristic name (same guard as analyze()).
        # Otherwise the identity is uncorroborated -> escalate to review (Fix 3).
        if tmdb_signal.tmdb_name:
            if result.detected_name is None or _names_are_similar(
                result.detected_name, tmdb_signal.tmdb_name
            ):
                result.detected_name = tmdb_signal.tmdb_name
            elif not result.needs_review:
                result.needs_review = True
                result.review_reason = _uncorroborated_review_reason(
                    result.detected_name, tmdb_signal
                )

        return result
```

Also cover the TMDB-only direct-return path. In `analyze()`, the block that returns when "Heuristics inconclusive, using TMDB signal" (currently ends with `return DiscAnalysisResult(... play_all_title_indices=tmdb_play_all,)`). Replace that `return DiscAnalysisResult(...)` with a built-then-escalated result:

```python
            tmdb_only = DiscAnalysisResult(
                content_type=tmdb_signal.content_type,
                titles=titles,
                detected_name=effective_name,
                detected_season=detected_season,
                confidence=tmdb_signal.confidence,
                tmdb_id=tmdb_signal.tmdb_id,
                tmdb_name=tmdb_signal.tmdb_name,
                classification_source="tmdb",
                play_all_title_indices=tmdb_play_all,
            )
            if tmdb_signal.tmdb_name and not _names_are_similar(
                effective_name or "", tmdb_signal.tmdb_name
            ):
                tmdb_only.needs_review = True
                tmdb_only.review_reason = _uncorroborated_review_reason(
                    effective_name, tmdb_signal
                )
            return tmdb_only
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -q`
Expected: PASS (all tests, including the two updated ones).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/unit/test_disc_name_identification.py
git commit -m "feat(analyst): escalate uncorroborated TMDB identity to review"
```

---

### Task 3: Fix 2 (core) — Runtime-aware Play-All / Extras in the Analyst

Teach the Play-All detectors to skip a feature-length title whose duration matches a legitimate expected episode runtime (a double-length pilot), via a new optional `expected_episode_runtimes` argument to `analyze()`.

**Files:**
- Modify: `backend/app/core/analyst.py` (`analyze` signature line ~267; `_detect_play_all` call site line ~357; `_detect_play_all` line ~691; `_detect_play_all_fallback` line ~734; add `_matches_expected_runtime` helper)
- Test: `backend/tests/pipeline/test_play_all_detection.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/pipeline/test_play_all_detection.py`:

```python
# ---------------------------------------------------------------------------
# Fix 2: runtime-aware Play-All (double-length pilot is NOT a Play-All)
# ---------------------------------------------------------------------------

from app.core.analyst import _matches_expected_runtime
from app.core.tmdb_classifier import TmdbSignal

# Note: ContentType is already imported at the top of this file.


def test_matches_expected_runtime_single_and_two_parter():
    # 90.5-min title matches a single 90-min expected episode (pilot)
    assert _matches_expected_runtime(5429, [90, 45, 45]) is True
    # Same title matches sum of two consecutive 45-min episodes (two-parter)
    assert _matches_expected_runtime(5429, [45, 45, 45]) is True
    # A real 157-min Play-All matches no single/two-parter runtime
    assert _matches_expected_runtime(9416, [45, 45, 45]) is False
    # Empty / zero runtimes -> no match (caller falls back to heuristic)
    assert _matches_expected_runtime(5429, []) is False
    assert _matches_expected_runtime(5429, [0, 0]) is False


@pytest.mark.pipeline
class TestDS9PilotNotPlayAll:
    """DS9 S1D1: t0 is the 90-min 'Emissary' pilot, not a Play-All of t1+t2."""

    def _titles(self):
        return [
            TitleInfo(index=0, duration_seconds=5429, size_bytes=2_000_000_000, chapter_count=18),
            TitleInfo(index=1, duration_seconds=2718, size_bytes=1_000_000_000, chapter_count=8),
            TitleInfo(index=2, duration_seconds=2715, size_bytes=1_000_000_000, chapter_count=8),
        ]

    def test_pilot_flagged_without_runtimes_regression(self):
        """Without runtimes, the old behavior stands (t0 ~ sum -> flagged)."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        result = analyst.analyze(self._titles(), "DS9S1D1")
        assert 0 in result.play_all_title_indices

    def test_pilot_not_flagged_with_runtimes(self):
        """With expected runtimes [90,45,45,...], t0 is recognized as a real episode."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        signal = TmdbSignal(
            content_type=ContentType.TV, confidence=0.70, tmdb_id=580,
            tmdb_name="Star Trek: Deep Space Nine",
        )
        result = analyst.analyze(
            self._titles(), "DS9S1D1", tmdb_signal=signal,
            expected_episode_runtimes=[90, 45, 45, 45, 45],
        )
        assert 0 not in result.play_all_title_indices
        # A runtime-confirmed pilot must keep the disc classified as TV, not flip
        # it to MOVIE (only the single long title would otherwise look movie-like).
        assert result.content_type == ContentType.TV
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/pipeline/test_play_all_detection.py -k "expected_runtime or DS9" -q`
Expected: FAIL — `ImportError: cannot import name '_matches_expected_runtime'`.

- [ ] **Step 3: Implement runtime awareness**

In `backend/app/core/analyst.py`, add this helper next to the other module helpers:

```python
def _matches_expected_runtime(duration_seconds: int, expected_runtimes_min: list[int]) -> bool:
    """True if a title duration matches a legitimate expected episode runtime.

    Matches a single expected runtime, or the sum of two consecutive expected
    runtimes (a two-parter carried as one title), within ±max(5 min, 15%).
    Zero / missing runtimes are ignored; an empty effective list returns False
    so callers fall back to the duration-sum heuristic.
    """
    runtimes = [r for r in expected_runtimes_min if r and r > 0]
    if not runtimes:
        return False
    actual_min = duration_seconds / 60.0

    def _close(expected: float) -> bool:
        return abs(actual_min - expected) <= max(5.0, 0.15 * expected)

    if any(_close(r) for r in runtimes):
        return True
    for i in range(len(runtimes) - 1):
        if _close(runtimes[i] + runtimes[i + 1]):
            return True
    return False
```

Change the `analyze` signature (line ~267) from:

```python
    def analyze(
        self,
        titles: list[TitleInfo],
        volume_label: str = "",
        tmdb_signal=None,
        disc_title: str | None = None,
    ) -> DiscAnalysisResult:
```

to:

```python
    def analyze(
        self,
        titles: list[TitleInfo],
        volume_label: str = "",
        tmdb_signal=None,
        disc_title: str | None = None,
        expected_episode_runtimes: list[int] | None = None,
    ) -> DiscAnalysisResult:
```

Change the Play-All call site (line ~357) from:

```python
        play_all = self._detect_play_all(titles, tv_result)
```

to:

```python
        play_all = self._detect_play_all(titles, tv_result, expected_episode_runtimes)
```

Add the movie-suppression guard. Keeping the pilot out of the Play-All bucket is not enough: with only two short episodes (< the 3-title TV cluster), the single feature-length title would otherwise make `_detect_movie` classify the whole disc as a MOVIE. In `analyze()`, immediately after `movie_result = self._detect_movie(titles)` and its `logger.info(f"Movie detection result: {movie_result}")` line (line ~345-346), insert:

```python
        # A feature-length title that matches an expected episode runtime on a
        # TV-labeled disc (e.g. a 90-min double-length pilot) is a TV episode, not
        # a movie feature — suppress the movie result so TV classification wins.
        if (
            movie_result
            and not movie_result.get("ambiguous")
            and is_likely_tv
            and expected_episode_runtimes
            and all(
                _matches_expected_runtime(t.duration_seconds, expected_episode_runtimes)
                for t in titles
                if t.duration_seconds >= self._get_config().analyst_movie_min_duration
            )
        ):
            logger.info(
                "Feature-length title(s) match expected episode runtimes on a "
                "TV-labeled disc — treating as TV episodes, not a movie."
            )
            movie_result = None
```

Change `_detect_play_all` signature and the per-title loop. Signature:

```python
    def _detect_play_all(
        self,
        titles: list[TitleInfo],
        tv_result: dict | None,
        expected_runtimes: list[int] | None = None,
    ) -> list[int]:
```

Inside, change the fallback delegation:

```python
        if not tv_result or "episode_indices" not in tv_result:
            # No episode cluster — try fallback using TV-range titles
            return self._detect_play_all_fallback(titles, expected_runtimes)
```

And in its per-title loop, add the runtime guard right after the `if t.duration_seconds < min_duration: continue` line:

```python
            # A title matching a real expected episode runtime (e.g. a 90-min
            # double-length pilot) is a legitimate episode, not a Play-All.
            if expected_runtimes and _matches_expected_runtime(t.duration_seconds, expected_runtimes):
                continue
```

Change `_detect_play_all_fallback` signature (line ~734):

```python
    def _detect_play_all_fallback(
        self, titles: list[TitleInfo], expected_runtimes: list[int] | None = None
    ) -> list[int]:
```

And in its per-title loop, add the same guard right after the `if t.duration_seconds < config.analyst_movie_min_duration: continue` line:

```python
            if expected_runtimes and _matches_expected_runtime(t.duration_seconds, expected_runtimes):
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/pipeline/test_play_all_detection.py -q`
Expected: PASS — new tests pass AND existing Picard/Arrested/edge tests still pass (they pass no runtimes, so the new guard is inert).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/pipeline/test_play_all_detection.py
git commit -m "feat(analyst): runtime-aware Play-All keeps double-length pilots"
```

---

### Task 4: Fix 2 (wiring) — Caller fetches expected runtimes

Fetch the season's expected episode runtimes (cached) and pass them into `analyze()` from `_run_classification`.

**Files:**
- Modify: `backend/app/services/identification_coordinator.py` (`_run_classification`, the `analyze()` call at line ~1647)
- Test: `backend/tests/unit/test_disc_name_identification.py` (append, mirroring the existing `_run_classification` tests)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_disc_name_identification.py`:

```python
@pytest.mark.asyncio
async def test_run_classification_fetches_runtimes_and_keeps_pilot(monkeypatch):
    """DS9 S1D1: caller fetches expected runtimes so the 90-min pilot is kept."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = [
        TitleInfo(index=0, duration_seconds=5429, size_bytes=int(2e9), chapter_count=18),
        TitleInfo(index=1, duration_seconds=2718, size_bytes=int(1e9), chapter_count=8),
        TitleInfo(index=2, duration_seconds=2715, size_bytes=int(1e9), chapter_count=8),
    ]

    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    ds9_signal = TmdbSignal(
        content_type=ContentType.TV, confidence=0.85, tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )

    runtime_calls: list[tuple] = []

    def fake_runtimes(show_id, season_number):
        runtime_calls.append((show_id, season_number))
        return [90, 45, 45, 45, 45]

    mock_job = MagicMock()
    mock_job.volume_label = "DS9S1D1"
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch(
            "app.core.tmdb_classifier.classify_from_tmdb",
            side_effect=lambda name, api_key: ds9_signal,
        ),
        patch(
            "app.matcher.tmdb_client.fetch_season_episode_runtimes",
            side_effect=fake_runtimes,
        ),
    ):
        analysis = await coordinator._run_classification(
            mock_job, job_id=1, titles=titles, session=mock_session, disc_name="DS9S1D1",
        )

    assert ("580", 1) in runtime_calls
    assert 0 not in analysis.play_all_title_indices
    assert analysis.detected_name == "Star Trek: Deep Space Nine"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -k "fetches_runtimes" -q`
Expected: FAIL — `("580", 1)` not in `runtime_calls` (the caller doesn't fetch runtimes yet), and/or `0 in play_all_title_indices`.

- [ ] **Step 3: Implement the caller change**

In `backend/app/services/identification_coordinator.py`, the `analyze()` call (line ~1647) is currently:

```python
        analysis = self._analyst.analyze(
            titles,
            job.volume_label,
            tmdb_signal=tmdb_signal,
            disc_title=disc_name_title,
        )
```

Immediately before it, add the runtime fetch, then pass it in:

```python
        # Expected episode runtimes let the analyst keep a double-length pilot
        # (e.g. DS9 "Emissary") instead of mistaking it for a Play-All concatenation.
        expected_runtimes: list[int] | None = None
        if (
            tmdb_signal
            and tmdb_signal.tmdb_id
            and tmdb_signal.content_type == ContentType.TV
        ):
            season_for_runtimes = label_season or disc_name_season
            if season_for_runtimes:
                try:
                    from app.matcher.tmdb_client import fetch_season_episode_runtimes

                    expected_runtimes = await asyncio.to_thread(
                        fetch_season_episode_runtimes,
                        str(tmdb_signal.tmdb_id),
                        season_for_runtimes,
                    )
                except Exception as e:  # network/runtime data is best-effort
                    logger.warning(
                        f"Job {job_id}: expected-runtime fetch failed: {e}", exc_info=True
                    )

        analysis = self._analyst.analyze(
            titles,
            job.volume_label,
            tmdb_signal=tmdb_signal,
            disc_title=disc_name_title,
            expected_episode_runtimes=expected_runtimes,
        )
```

Confirm `ContentType` and `asyncio` are already imported in this file (they are — used elsewhere). `label_season` is defined at line ~1537 and `disc_name_season` at line ~1556, both before this point.

- [ ] **Step 3b: Keep the existing `_run_classification` tests network-free**

This new fetch runs inside the four pre-existing `_run_classification` tests, which set a TV `tmdb_id` + season and would now make a real (retrying) TMDB HTTP call. Add this exact line to the `with (...)` patch block of each of these four tests in `backend/tests/unit/test_disc_name_identification.py`:

```python
        patch("app.matcher.tmdb_client.fetch_season_episode_runtimes", return_value=[]),
```

The four tests:
- `test_run_classification_uses_disc_name_when_label_fails`
- `test_run_classification_uses_disc_name_when_label_resolves`
- `test_run_classification_reresolves_tv_when_label_matches_movie`
- `test_run_classification_skips_redundant_reresolve_after_disc_name_fallback`

(Returning `[]` means "no runtimes" — the analyst falls back to its existing heuristic, preserving each test's original behavior.)

- [ ] **Step 4: Run test to verify it passes (and existing ones stay green/fast)**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py -q`
Expected: PASS — the new test passes and all four existing `_run_classification` tests stay green without network calls.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/identification_coordinator.py backend/tests/unit/test_disc_name_identification.py
git commit -m "feat(identify): fetch expected episode runtimes for Play-All check"
```

---

### Task 5: DS9 end-to-end reproduction (all three fixes together)

A single analyst-level reproduction of Job 153 asserting the combined outcome: correct name, pilot kept, no spurious review.

**Files:**
- Test: `backend/tests/pipeline/test_play_all_detection.py` (append)

- [ ] **Step 1: Write the test**

Append to `backend/tests/pipeline/test_play_all_detection.py`:

```python
@pytest.mark.pipeline
class TestDS9Job153Reproduction:
    """End-to-end analyst reproduction of the real DS9 S1D1 rip (Job 153)."""

    def test_ds9_resolves_correctly(self):
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        titles = [
            TitleInfo(index=0, duration_seconds=5429, size_bytes=int(2e9), chapter_count=18),
            TitleInfo(index=1, duration_seconds=2718, size_bytes=int(1e9), chapter_count=8),
            TitleInfo(index=2, duration_seconds=2715, size_bytes=int(1e9), chapter_count=8),
        ]
        signal = TmdbSignal(
            content_type=ContentType.TV, confidence=0.70, tmdb_id=580,
            tmdb_name="Star Trek: Deep Space Nine",
        )
        result = analyst.analyze(
            titles, "DS9S1D1", tmdb_signal=signal, disc_title="DS9S1D1",
            expected_episode_runtimes=[90, 45, 45, 45, 45],
        )

        # Fix 1: abbreviation corroboration adopts the TMDB name
        assert result.detected_name == "Star Trek: Deep Space Nine"
        # Fix 2: the 90-min pilot is NOT dropped as a Play-All/extra
        assert 0 not in result.play_all_title_indices
        # Fix 2 corollary: disc stays TV (pilot is an episode, not a movie)
        assert result.content_type == ContentType.TV
        # Fix 3: identity is corroborated, so no spurious review
        assert result.needs_review is False
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/pipeline/test_play_all_detection.py::TestDS9Job153Reproduction -q`
Expected: PASS (all three fixes already implemented in Tasks 1-3).

- [ ] **Step 3: Run the full affected suites**

Run: `cd backend && uv run pytest tests/unit/test_disc_name_identification.py tests/pipeline/test_play_all_detection.py -q`
Expected: PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/pipeline/test_play_all_detection.py
git commit -m "test(analyst): DS9 Job 153 end-to-end reproduction"
```

---

### Task 6: Changelog

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` section)

- [ ] **Step 1: Add the entry**

Under the `## [Unreleased]` heading, add a `### Fixed` block (create it if absent):

```markdown
### Fixed
- Abbreviated TV disc labels (e.g. `DS9` → "Star Trek: Deep Space Nine") now
  resolve to the correct TMDB show name instead of keeping the raw disc label.
- Feature-length episodes (e.g. a 90-minute double-length pilot) are no longer
  discarded as a "Play All" concatenation or extra — the analyst checks expected
  TMDB episode runtimes before flagging.
- Discs whose identity cannot be corroborated now go to review instead of
  silently completing under a guessed name.
```

If a `## [Unreleased]` section does not exist, add it directly under the changelog's top title. Append the PR reference (`(#NNN)`) to each bullet when the PR is opened.

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): disc identification robustness fixes"
```

---

### Task 7: E2E verification before merge (manual gate — no commit)

Verify end-to-end behavior before opening/merging the PR. Requires the backend running with `DEBUG=true`.

- [ ] **Step 1: Full backend test suite is green**

Run: `cd backend && uv run pytest -q`
Expected: PASS (full suite; confirms no cross-suite regressions).

- [ ] **Step 2: DEBUG simulation of the DS9 disc**

Start the backend (`DEBUG=true`), then:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"DS9S1D1","content_type":"tv","simulate_ripping":true}'
```

Verify in the dashboard / `GET /api/jobs/{id}/detail`: the job resolves to "Star Trek: Deep Space Nine", the 90-min title is **not** marked extra/Play-All, and the job completes (or reviews) per the balanced posture.

- [ ] **Step 3: Frontend E2E suite (no regressions)**

Run: `cd frontend && npm install && npm run test:e2e`
Expected: PASS.

- [ ] **Step 4: Real-disc confidence check (recommended)**

Re-rip the physical *Star Trek: Deep Space Nine* S1D1 disc and confirm: name = "Star Trek: Deep Space Nine", all three episode titles organized (pilot included), correct `[tmdbid-580]` folder.

- [ ] **Step 5: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to open the PR (append `(#NNN)` to the changelog bullets) or merge.

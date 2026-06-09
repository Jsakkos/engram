# DINFO-corroborated naming + poster-by-tmdb_id — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop garbled separator-less volume labels (e.g. `BREAKINGBADS2`) from being organized under a concatenated name (`Breakingbad`) and showing no poster, by making the authoritative TMDB name win when corroborated by any on-disc signal and by fetching posters with the stored `tmdb_id`.

**Architecture:** TMDB is the authoritative display name. The volume-label parse and the MakeMKV DINFO disc name are *corroboration* signals: the TMDB name is adopted when it matches either of them under whitespace/punctuation-insensitive comparison. DINFO is parsed unconditionally (no longer gated behind a TMDB miss). The poster endpoint uses the job's `tmdb_id` directly, falling back to a name search only when no id exists.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel, pytest (`uv run pytest`), ruff. All commands run from `backend/`.

**Spec:** `docs/superpowers/specs/2026-06-09-dinfo-corroborated-naming-and-poster-design.md`

---

## File structure

- `backend/app/core/analyst.py` — `_names_are_similar` (whitespace-insensitive), new `_collapsed` + `_is_generic_disc_name` helpers, `_parse_disc_name` (colon separators), `analyze()` (`name_hint`→`disc_title`, corroboration model).
- `backend/app/services/identification_coordinator.py` — `_run_classification`: parse DINFO unconditionally, pass `disc_title`.
- `backend/app/api/routes.py` — `get_job_poster`: use `tmdb_id` directly.
- `backend/tests/unit/test_analyst.py` — `_names_are_similar` unit tests.
- `backend/tests/unit/test_disc_name_identification.py` — colon parse case, `analyze()` regression + corroboration-safety tests, rename existing `name_hint` tests, coordinator integration test.
- `backend/tests/unit/test_poster_endpoint.py` — new; poster-by-tmdb_id + fallback.

---

## Task 1: Whitespace/punctuation-insensitive `_names_are_similar`

**Files:**
- Modify: `backend/app/core/analyst.py:59-76`
- Test: `backend/tests/unit/test_analyst.py`

- [ ] **Step 1: Write the failing test**

Add at the end of `backend/tests/unit/test_analyst.py` (the module already imports from `app.core.analyst`; add `_names_are_similar` to that import or import inline as shown):

```python
class TestNamesAreSimilar:
    """Whitespace/punctuation-insensitive title similarity (BREAKINGBADS2 bug)."""

    def test_concatenated_label_matches_spaced_title(self):
        from app.core.analyst import _names_are_similar

        assert _names_are_similar("Breakingbad", "Breaking Bad") is True

    def test_concatenated_multiword_label_matches(self):
        from app.core.analyst import _names_are_similar

        assert _names_are_similar("Strangenewworlds", "Strange New Worlds") is True

    def test_punctuation_difference_still_matches(self):
        from app.core.analyst import _names_are_similar

        assert _names_are_similar("Star Trek Picard", "Star Trek: Picard") is True

    def test_unrelated_concatenated_name_rejected(self):
        from app.core.analyst import _names_are_similar

        assert _names_are_similar("Breakingbad", "Friends") is False

    def test_unrelated_spaced_names_rejected(self):
        from app.core.analyst import _names_are_similar

        assert _names_are_similar("The Italian Job", "Idioms Origins Volume 1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_analyst.py::TestNamesAreSimilar -v`
Expected: `test_concatenated_label_matches_spaced_title` and `test_concatenated_multiword_label_matches` FAIL (return False today); the three others PASS.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/core/analyst.py`, replace the existing `_names_are_similar` (lines 64-76) and add a `_collapsed` helper just above it. The final block (keeping `_title_tokens` unchanged at lines 59-61) reads:

```python
def _title_tokens(s: str) -> set[str]:
    """Tokenize a title into lowercased words, dropping punctuation and 1-char tokens."""
    return {w.lower() for w in re.sub(r"[^\w\s]", "", s).split() if len(w) > 1}


def _collapsed(s: str) -> str:
    """Lowercase a title with all non-alphanumeric characters removed.

    Lets us recognize that a separator-less volume label and a spaced title are
    the same name: "Breakingbad" and "Breaking Bad" both collapse to "breakingbad".
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _names_are_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Return True if two title strings refer to the same title.

    Two acceptance paths:
      1. Word-token Jaccard >= threshold (handles punctuation / word-order noise).
      2. Whitespace/punctuation-insensitive equality (handles a separator-less
         volume label vs. a spaced title, e.g. "Breakingbad" == "Breaking Bad").

    Path 2 is conservative: it only matches names that are identical once spacing
    and punctuation are removed, so it never makes unrelated titles match
    ("breakingbad" != "friends").
    """
    a_tok, b_tok = _title_tokens(a), _title_tokens(b)
    if not a_tok or not b_tok:
        return True  # Can't compare — allow override
    if len(a_tok & b_tok) / len(a_tok | b_tok) >= threshold:
        return True
    a_collapsed, b_collapsed = _collapsed(a), _collapsed(b)
    return bool(a_collapsed) and a_collapsed == b_collapsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_analyst.py::TestNamesAreSimilar -v`
Expected: all 5 PASS.

- [ ] **Step 5: Verify no regression in the existing similarity guard tests**

Run: `uv run pytest tests/unit/test_analyst.py::TestTmdbNameSimilarityGuard -v`
Expected: all PASS (dissimilar names still rejected, similar still accepted).

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/unit/test_analyst.py
git commit -m "fix(analyst): make _names_are_similar whitespace/punctuation-insensitive"
```

---

## Task 2: `_parse_disc_name` handles colon separators + generic reject

**Files:**
- Modify: `backend/app/core/analyst.py:836-880` and add `_is_generic_disc_name` helper near the other module helpers
- Test: `backend/tests/unit/test_disc_name_identification.py:78-105`

- [ ] **Step 1: Write the failing test**

In `backend/tests/unit/test_disc_name_identification.py`, add two cases to the `@pytest.mark.parametrize` list of `test_parse_disc_name` (after the existing `("Firefly Disc 1", "Firefly", None),` entry, before `("", None, None),`):

```python
        # Colon-separated "Title: Season N: Disc M" (some Blu-ray DINFO names).
        ("Breaking Bad: Season 2: Disc 1", "Breaking Bad", 2),
        # Generic placeholder disc names carry no title.
        ("Blu-ray disc", None, None),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest "tests/unit/test_disc_name_identification.py::test_parse_disc_name" -v`
Expected: the `Breaking Bad: Season 2: Disc 1` case FAILS (currently returns `('Breaking Bad: Season 2:', None)`); the `Blu-ray disc` case FAILS (currently returns `('Blu-ray disc', None)`).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/core/analyst.py`, add this helper directly below `_collapsed` (from Task 1):

```python
def _is_generic_disc_name(name: str) -> bool:
    """True if a DINFO disc name is a generic placeholder (e.g. 'Blu-ray disc')."""
    return re.sub(r"[^A-Z0-9]", "", name.upper()) in _GENERIC_VOLUME_LABELS
```

Then replace the body of `_parse_disc_name` from `if not disc_name:` through `return name, season` (lines 851-880) with:

```python
        if not disc_name:
            return None, None

        name = disc_name.strip()
        season: int | None = None

        # Strip a trailing disc indicator with OR without parentheses/dash/colon:
        # "(Disc 1)", "Disc 1", "- Disc 1", ": Disc 1", "Disk 1", "Disc1".
        name = re.sub(
            r"\s*[-–:]?\s*\(?\s*Dis[ck]\s*\d+\s*\)?\s*$", "", name, flags=re.IGNORECASE
        ).strip()

        # Extract a trailing "- Season N" / ": Season N" / "Season N" suffix. The
        # dash/colon variant is tried first so the separator is consumed cleanly
        # (e.g. "Breaking Bad: Season 2" -> "Breaking Bad"). Colons *inside* a title
        # are untouched because these patterns are anchored to the end of the string.
        m = re.search(r"\s*[-–:]\s*Season\s+(\d+)\s*$", name, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            name = name[: m.start()].strip()
        else:
            m = re.search(r"\s+Season\s+(\d+)\s*$", name, re.IGNORECASE)
            if m:
                season = int(m.group(1))
                name = name[: m.start()].strip()

        # Trim any leftover trailing separator punctuation (e.g. a dangling colon).
        name = name.rstrip(" :-–").strip()

        # Reject empty, too-short, or generic placeholder results.
        if not name or len(name) < 2 or _is_generic_disc_name(name):
            return None, None

        return name, season
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest "tests/unit/test_disc_name_identification.py::test_parse_disc_name" -v`
Expected: all parametrized cases PASS, including the two new ones and the pre-existing colon-in-title case (`Star Trek: Strange New Worlds - Season 3 (Disc 1)` → `('Star Trek: Strange New Worlds', 3)`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/unit/test_disc_name_identification.py
git commit -m "fix(analyst): parse colon-separated DINFO disc names + reject generic names"
```

---

## Task 3: `analyze()` — TMDB-authoritative, DINFO-corroborated naming

**Files:**
- Modify: `backend/app/core/analyst.py:245-307` (signature `name_hint`→`disc_title`, base name, corroboration)
- Test: `backend/tests/unit/test_disc_name_identification.py`

Note: the second guard inside `_apply_tmdb_signal` (`analyst.py:538-543`) needs **no** structural change — it inherits the whitespace-insensitive `_names_are_similar` from Task 1, and `analyze()` will already have set `detected_name` to the canonical TMDB name before it runs.

- [ ] **Step 1: Write the failing tests + update renamed ones**

In `backend/tests/unit/test_disc_name_identification.py`:

(a) Update the section comment at line 108-110 to:

```python
# ---------------------------------------------------------------------------
# Analyst: TMDB name adopted when corroborated by label OR DINFO disc title
# ---------------------------------------------------------------------------
```

(b) In `test_analyst_with_name_hint_uses_correct_name`, rename the keyword argument `name_hint=` to `disc_title=` (line 150). In `test_analyst_name_hint_still_propagates_tmdb_id_on_type_conflict`, rename `name_hint=` to `disc_title=` (line 172). Leave their assertions unchanged.

(c) Add three new tests after `test_analyst_name_hint_still_propagates_tmdb_id_on_type_conflict` (before the `_run_classification` section comment):

```python
def test_analyst_adopts_tmdb_name_for_concatenated_label():
    """BREAKINGBADS2 -> 'Breakingbad' must be corrected to TMDB 'Breaking Bad'."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "BREAKINGBADS2", tmdb_signal=tmdb)

    assert result.detected_name == "Breaking Bad"
    assert result.detected_season == 2
    assert result.tmdb_id == 1396


def test_analyst_adopts_tmdb_name_when_disc_title_corroborates():
    """A clean DINFO disc title corroborates the TMDB name as well."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "BREAKINGBADS2",
        tmdb_signal=tmdb,
        disc_title="Breaking Bad",
    )

    assert result.detected_name == "Breaking Bad"
    assert result.tmdb_id == 1396


def test_analyst_keeps_base_name_when_tmdb_uncorroborated():
    """A spurious TMDB name matching neither on-disc signal must not override."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=999,
        tmdb_name="Some Unrelated Show",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "BREAKINGBADS2",
        tmdb_signal=tmdb,
        disc_title="Breaking Bad",
    )

    # Neither "Breakingbad" nor "Breaking Bad" matches "Some Unrelated Show",
    # so the DINFO-preferred base name is kept rather than the TMDB name.
    assert result.detected_name == "Breaking Bad"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_disc_name_identification.py -v -k "adopts_tmdb_name or keeps_base_name or with_name_hint or type_conflict"`
Expected: `test_analyst_adopts_tmdb_name_for_concatenated_label` FAILS (`detected_name == "Breakingbad"` today). The renamed `disc_title=` tests FAIL with `TypeError: analyze() got an unexpected keyword argument 'disc_title'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/analyst.py`, change the `analyze` signature parameter (line 250) from:

```python
        name_hint: str | None = None,
```

to:

```python
        disc_title: str | None = None,
```

Update the docstring for that parameter (lines 258-260) to:

```python
            disc_title: Parsed MakeMKV DINFO disc title, when available. Used as the
                base display name (preferred over the volume-label parse) and as an
                additional signal that corroborates the authoritative TMDB name.
```

Replace the name-selection block (lines 283-307) with:

```python
        # Try to extract show name, season, and disc from volume label
        label_name, detected_season, detected_disc = self._parse_volume_label(volume_label)

        # Base display name: prefer the MakeMKV DINFO disc title (human-readable,
        # properly spaced) over the filesystem-constrained volume-label parse.
        detected_name = disc_title or label_name

        # If we found a season pattern (S01D02), it's very likely a TV show
        is_likely_tv = detected_season is not None
        if is_likely_tv:
            logger.info(f"Volume label indicates TV (season {detected_season})")

        # TMDB is the authoritative name source, but only adopt it when an on-disc
        # signal corroborates it: the volume-label name OR the DINFO disc title,
        # compared whitespace-insensitively so a separator-less label like
        # "Breakingbad" still corroborates "Breaking Bad". This keeps a wrong TMDB
        # match from silently overriding while letting a right one through.
        effective_name = detected_name
        if tmdb_signal and tmdb_signal.tmdb_name:
            tmdb_name = tmdb_signal.tmdb_name
            corroborated = (
                detected_name is None
                or (label_name is not None and _names_are_similar(label_name, tmdb_name))
                or (disc_title is not None and _names_are_similar(disc_title, tmdb_name))
            )
            if corroborated:
                effective_name = tmdb_name
            else:
                logger.warning(
                    f"TMDB name '{tmdb_name}' corroborated by neither the volume-label "
                    f"name '{label_name}' nor the disc title '{disc_title}' — "
                    f"keeping '{detected_name}'"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disc_name_identification.py -v`
Expected: all PASS, including `test_analyst_without_name_hint_gives_garbled_name` (still `"Strangenewworlds"` — that is the deferred extra-leading-words case) and the new adoption tests.

- [ ] **Step 5: Run the full analyst suite for regressions**

Run: `uv run pytest tests/unit/test_analyst.py -v`
Expected: all PASS (the similarity-guard, studio-prefix, and TMDB-name tests are unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/analyst.py backend/tests/unit/test_disc_name_identification.py
git commit -m "fix(analyst): adopt authoritative TMDB name when corroborated by label or DINFO"
```

---

## Task 4: Parse DINFO unconditionally in `_run_classification`

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:1135-1196`
- Test: `backend/tests/unit/test_disc_name_identification.py`

- [ ] **Step 1: Write the failing test**

In `backend/tests/unit/test_disc_name_identification.py`, add after `test_run_classification_uses_disc_name_when_label_fails`:

```python
@pytest.mark.asyncio
async def test_run_classification_uses_disc_name_when_label_resolves(monkeypatch):
    """DINFO corrects a garbled-but-resolved label (BREAKINGBADS2 -> Breaking Bad)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = _tv_titles()

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

    bb_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )

    call_count = {"n": 0}

    def fake_classify_from_tmdb(name: str, api_key: str):
        call_count["n"] += 1
        if name == "Breakingbad":
            return bb_signal  # label-derived name resolves (via TMDB variation)
        return None

    mock_job = MagicMock()
    mock_job.volume_label = "BREAKINGBADS2"
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
        patch("app.core.tmdb_classifier.classify_from_tmdb", side_effect=fake_classify_from_tmdb),
    ):
        analysis = await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="Breaking Bad: Season 2: Disc 1",
        )

    assert analysis.detected_name == "Breaking Bad"
    assert analysis.detected_season == 2
    assert analysis.tmdb_id == 1396
    assert call_count["n"] == 1  # only the label query; no disc-name fallback call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest "tests/unit/test_disc_name_identification.py::test_run_classification_uses_disc_name_when_label_resolves" -v`
Expected: FAILS with `analysis.detected_name == "Breakingbad"` (DINFO is still gated behind the TMDB miss, so it is never parsed/passed).

- [ ] **Step 3: Write the implementation**

In `backend/app/services/identification_coordinator.py`, replace the DINFO fallback block (lines 1135-1150) with:

```python
        # Parse the MakeMKV DINFO disc name unconditionally (when present). Its
        # clean, human-readable title is used to corroborate the TMDB name and as a
        # better base name than the volume label — even when the volume label
        # already resolved on TMDB (the BREAKINGBADS2 -> "Breaking Bad" case).
        disc_name_title: str | None = None
        disc_name_season: int | None = None
        if disc_name:
            parsed_title, parsed_season = DiscAnalyst._parse_disc_name(disc_name)
            if parsed_title:
                disc_name_title = parsed_title
                disc_name_season = parsed_season

        # DINFO disc-name TMDB fallback — when the volume label gave no TMDB signal,
        # resolve identity from the disc name instead.
        if not tmdb_signal and disc_name_title and config.tmdb_api_key:
            disc_tmdb_signal = _try_tmdb(disc_name_title, "TMDB disc-name fallback failed")
            if disc_tmdb_signal:
                tmdb_signal = disc_tmdb_signal
                logger.info(
                    f"Job {job_id}: TMDB fallback via disc name '{disc_name_title}' succeeded "
                    f"(label '{job.volume_label}' gave garbled name)"
                )
```

Then update the `analyze()` call (lines 1189-1196). Replace it with:

```python
        # Analyze disc content — pass disc_name_title so the analyst uses the clean
        # DINFO title as the base name and as a corroboration signal for the
        # authoritative TMDB name (instead of the garbled volume-label parse).
        analysis = self._analyst.analyze(
            titles,
            job.volume_label,
            tmdb_signal=tmdb_signal,
            disc_title=disc_name_title or None,
        )
```

(The season backfill that follows — `if disc_name_title and disc_name_season and not analysis.detected_season:` — stays unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest "tests/unit/test_disc_name_identification.py::test_run_classification_uses_disc_name_when_label_resolves" -v`
Expected: PASS.

- [ ] **Step 5: Verify the label-fails path still works**

Run: `uv run pytest "tests/unit/test_disc_name_identification.py::test_run_classification_uses_disc_name_when_label_fails" -v`
Expected: PASS (`call_count == 2`, `detected_name == "Star Trek: Strange New Worlds"`, `detected_season == 3`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/identification_coordinator.py backend/tests/unit/test_disc_name_identification.py
git commit -m "fix(identification): parse DINFO disc name unconditionally for name corroboration"
```

---

## Task 5: Poster endpoint uses `tmdb_id` directly

**Files:**
- Modify: `backend/app/api/routes.py:1329-1367`
- Test: `backend/tests/unit/test_poster_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_poster_endpoint.py`:

```python
"""Unit tests for the job poster endpoint (poster-by-tmdb_id)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes import get_job_poster
from app.models.disc_job import ContentType, DiscJob


@pytest.mark.asyncio
async def test_poster_uses_tmdb_id_directly():
    """With tmdb_id set, fetch /tv/{id} directly and ignore a garbled detected_title."""
    job = DiscJob(volume_label="BREAKINGBADS2", content_type=ContentType.TV)
    job.tmdb_id = 1396
    job.detected_title = "Breakingbad"  # garbled — must NOT be used

    cfg = MagicMock()
    cfg.tmdb_api_key = "fake-key"

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"poster_path": "/poster.jpg"}
        return resp

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=cfg)),
        patch("requests.get", side_effect=fake_get),
    ):
        result = await get_job_poster(job=job)

    assert captured["url"] == "https://api.themoviedb.org/3/tv/1396"
    assert result["poster_url"] == "https://image.tmdb.org/t/p/original/poster.jpg"


@pytest.mark.asyncio
async def test_poster_falls_back_to_name_search_without_tmdb_id():
    """Without a tmdb_id, fall back to the name search on detected_title."""
    job = DiscJob(volume_label="SOME_MOVIE", content_type=ContentType.MOVIE)
    job.tmdb_id = None
    job.detected_title = "Some Movie"

    cfg = MagicMock()
    cfg.tmdb_api_key = "fake-key"

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [{"poster_path": "/m.jpg"}]}
        return resp

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=cfg)),
        patch("requests.get", side_effect=fake_get),
    ):
        result = await get_job_poster(job=job)

    assert captured["url"] == "https://api.themoviedb.org/3/search/movie"
    assert captured["params"]["query"] == "Some Movie"
    assert result["poster_url"] == "https://image.tmdb.org/t/p/original/m.jpg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_poster_endpoint.py -v`
Expected: `test_poster_uses_tmdb_id_directly` FAILS — today's endpoint calls `/search/tv` with `query="Breakingbad"`, so `captured["url"]` is the search URL, not `/tv/1396`.

- [ ] **Step 3: Write the implementation**

In `backend/app/api/routes.py`, replace the body of `get_job_poster` (lines 1330-1367) with:

```python
async def get_job_poster(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Get the TMDB poster URL for a job.

    Prefer the authoritative ``job.tmdb_id`` (exact, immune to a garbled
    detected_title); fall back to a name search only when no id is set.
    """
    import requests

    from app.core.tmdb_classifier import _build_auth
    from app.matcher.tmdb_client import BASE_IMAGE_URL
    from app.services.config_service import get_config as get_db_config

    config = await get_db_config()
    api_key = config.tmdb_api_key
    if not api_key:
        return {"poster_url": None}

    media = "movie" if job.content_type == "movie" else "tv"
    headers, params = _build_auth(api_key)

    try:
        if job.tmdb_id:
            detail_url = f"https://api.themoviedb.org/3/{media}/{job.tmdb_id}"
            response = requests.get(detail_url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                poster_path = response.json().get("poster_path")
                if poster_path:
                    return {"poster_url": f"{BASE_IMAGE_URL}{poster_path}"}
            return {"poster_url": None}

        if not job.detected_title:
            return {"poster_url": None}
        search_url = f"https://api.themoviedb.org/3/search/{media}"
        params["query"] = job.detected_title
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                return {"poster_url": f"{BASE_IMAGE_URL}{results[0]['poster_path']}"}
    except Exception as e:
        logger.warning(f"Error fetching poster: {e}")

    return {"poster_url": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_poster_endpoint.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_poster_endpoint.py
git commit -m "fix(api): fetch job poster by tmdb_id instead of garbled name search"
```

---

## Task 6: Full verification + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the touched-area test suites**

Run:
```bash
uv run pytest tests/unit/test_analyst.py tests/unit/test_disc_name_identification.py tests/unit/test_poster_endpoint.py tests/unit/test_coverage_improvements.py tests/pipeline/test_classification.py -q
```
Expected: all PASS, no errors.

- [ ] **Step 2: Lint and format the changed files**

Run:
```bash
uv run ruff check app/core/analyst.py app/services/identification_coordinator.py app/api/routes.py tests/unit/test_analyst.py tests/unit/test_disc_name_identification.py tests/unit/test_poster_endpoint.py
uv run ruff format app/core/analyst.py app/services/identification_coordinator.py app/api/routes.py tests/unit/test_analyst.py tests/unit/test_disc_name_identification.py tests/unit/test_poster_endpoint.py
```
Expected: ruff check reports no errors; ruff format makes no or only trivial changes.

- [ ] **Step 3: Commit any formatting changes**

```bash
git add -A backend
git commit -m "style: ruff format for DINFO-naming + poster fix" || echo "nothing to format"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** Change 1 → Task 1; Change 2 → Task 2; Change 3 → Task 3 (second guard auto-covered via Task 1); Change 4 → Task 4; Change 5 → Task 5. All five spec changes have a task.
- **Method name:** the production method is `_run_classification` (not `_compute_classification`, which the spec narrative used loosely).
- **Signature change blast radius:** only one production caller (`identification_coordinator.py`, updated in Task 4) and three references in `test_disc_name_identification.py` (renamed in Task 3) use `name_hint`; every other `analyze()` caller is positional and unaffected.
- **Deferred (out of scope, per spec):** containment/substring corroboration for "TMDB name has extra leading words" — `test_analyst_without_name_hint_gives_garbled_name` documents that `STRANGENEWWORLDS` (no DINFO) still yields `"Strangenewworlds"`. Not a regression.

# TMDB-ID Show-Identity Spine + Same-Name Collision Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tmdb_id` a first-class identity that flows through subtitle download → matcher reference selection → corpus lookup, detect genuinely-ambiguous same-name TMDB collisions and route them into the existing re-identify review workflow, and add a corpus guard that refuses a precomputed corpus whose `tmdb_id` contradicts the job's resolved id.

**Architecture:** `tmdb_id` is threaded as an **optional** companion to the existing show-name string. Where present it is authoritative (corpus guard, download id); where absent, behavior falls back to today's name-based logic — backward-compatible, no cache migration. Collision detection lives in `tmdb_classifier`; ambiguity propagates through `analyst._apply_tmdb_signal` into the existing `needs_review`/`review_reason` path, which the coordinator already routes to `transition_to_review` and the `ReIdentifyModal` already surfaces.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/aiosqlite, pytest (async), `uv` for all commands. Backend lives in `backend/`; run everything from there with `uv run …`.

**Spec:** `docs/superpowers/specs/2026-05-30-tmdb-id-show-identity-spine-design.md`

**Conventions for every task:**
- All commands run from `backend/` unless stated. Use `uv run pytest …`, `uv run ruff check .`, `uv run ruff format .`.
- Ruff: line length 100, double quotes. Run `uv run ruff format .` before each commit.
- Worktree note: the worktree's `backend/engram.db` may be a 0-byte stub. Unit tests below do **not** need the DB. If a test errors with `no such table: app_config`, that's the empty-DB env gap, not your code.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/core/tmdb_classifier.py` | Name→TMDB classification | Add `TmdbSignal.ambiguous_identity`/`candidates`; materiality constants; `_search_tmdb` returns raw results; collision detection + gate in `classify_from_tmdb` |
| `backend/app/core/analyst.py` | Disc analysis result assembly | `_apply_tmdb_signal` propagates ambiguity → clear `tmdb_id`, set `needs_review`/`review_reason` |
| `backend/app/services/identification_coordinator.py` | Identification orchestration | Skip subtitle download when ambiguous identity; thread `tmdb_id` into subtitle-download call sites + re-identify restart |
| `backend/app/matcher/episode_identification.py` | Corpus lookup + matcher | `precomputed_covers_season(expected_tmdb_id=…)`; `EpisodeMatcher.expected_tmdb_id`; guard in `_load_precomputed_season` before stale-prune |
| `backend/app/matcher/testing_service.py` | Subtitle download entrypoint | `download_subtitles(tmdb_id=…)` bypasses `fetch_show_id`; `_precomputed_skip_result(expected_tmdb_id=…)` |
| `backend/app/services/matching_coordinator.py` | Match + subtitle orchestration | `tmdb_id` params on `download_subtitles`/`start_subtitle_download`/`restart_subtitle_download`; `_match_single_file_inner` passes `job.tmdb_id` |
| `backend/app/core/curator.py` | Matcher integration | `match_single_file(tmdb_id=…)`; `_ensure_initialized` uses known id (skips `fetch_show_id`) + passes `expected_tmdb_id` to `EpisodeMatcher` |
| `backend/tests/unit/…`, `backend/tests/integration/…` | Tests | New unit + integration tests |

**Out of scope (later items):** on-disk cache re-key/migration (item 2); the noise-floor "wrong show" UX signal (item 3); the chromaprint/LLM `fetch_show_id(series_name)` calls in `curator._chromaprint_prepass`/`_match_via_llm` (documented follow-up — Frasier uses the ASR path, which this plan covers).

---

## Task 1: TmdbSignal carries ambiguity + materiality constants

**Files:**
- Modify: `backend/app/core/tmdb_classifier.py`
- Test: `backend/tests/unit/test_tmdb_classifier_collision.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_tmdb_classifier_collision.py`:

```python
from app.core.tmdb_classifier import (
    AMBIGUOUS_POPULARITY_FLOOR,
    AMBIGUOUS_POPULARITY_RATIO,
    TmdbSignal,
)
from app.models.disc_job import ContentType


def test_tmdb_signal_defaults_not_ambiguous():
    sig = TmdbSignal(content_type=ContentType.TV, confidence=0.7, tmdb_id=3452, tmdb_name="Frasier")
    assert sig.ambiguous_identity is False
    assert sig.candidates is None


def test_tmdb_signal_can_carry_candidates():
    cands = [{"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6}]
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=None,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=cands,
    )
    assert sig.ambiguous_identity is True
    assert sig.candidates == cands


def test_materiality_constants_have_sane_defaults():
    assert AMBIGUOUS_POPULARITY_FLOOR == 10.0
    assert AMBIGUOUS_POPULARITY_RATIO == 4.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -v`
Expected: FAIL — `ImportError` for `AMBIGUOUS_POPULARITY_FLOOR` / `TypeError` for unexpected `ambiguous_identity` kwarg.

- [ ] **Step 3: Add constants and extend TmdbSignal**

In `backend/app/core/tmdb_classifier.py`, add the constants near the top (below `HIGH_POPULARITY_THRESHOLD = 50`):

```python
# Same-name collision detection (item 1). Flag a job for review only when two
# distinct same-name TMDB shows are BOTH plausibly real: the runner-up clears
# this popularity floor AND the top/second popularity ratio is small enough that
# popularity is not a confident pick. Dominant-twin cases (e.g. Frasier 1993 vs
# 2023 revival) intentionally fall through — they have no identify-time signal
# and are handled downstream (item 3). Tunable.
AMBIGUOUS_POPULARITY_FLOOR = 10.0
AMBIGUOUS_POPULARITY_RATIO = 4.0
```

Replace the `TmdbSignal` class with the extended version (add two `__slots__` + ctor args):

```python
class TmdbSignal:
    """Signal from TMDB about content type."""

    __slots__ = (
        "content_type",
        "confidence",
        "tmdb_id",
        "tmdb_name",
        "ambiguous_identity",
        "candidates",
    )

    def __init__(
        self,
        content_type: ContentType,
        confidence: float,
        tmdb_id: int | None = None,
        tmdb_name: str | None = None,
        ambiguous_identity: bool = False,
        candidates: list[dict] | None = None,
    ):
        self.content_type = content_type
        self.confidence = confidence
        self.tmdb_id = tmdb_id
        self.tmdb_name = tmdb_name
        self.ambiguous_identity = ambiguous_identity
        self.candidates = candidates

    def __repr__(self) -> str:
        return (
            f"TmdbSignal(content_type={self.content_type.value}, "
            f"confidence={self.confidence:.0%}, tmdb_id={self.tmdb_id}, "
            f"tmdb_name={self.tmdb_name!r}, ambiguous_identity={self.ambiguous_identity})"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Format + commit**

```bash
uv run ruff format app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git add app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git commit -m "feat(tmdb): TmdbSignal carries ambiguous_identity + candidates"
```

---

## Task 2: `_search_tmdb` returns raw results alongside the best match

The collision gate needs the full TV result list, which `_search_tmdb` currently discards. Change its return to `(best, results)` and update the three call sites in `classify_from_tmdb`. `_search_tmdb` is module-private (only called inside `classify_from_tmdb`), so this is a safe local contract change.

**Files:**
- Modify: `backend/app/core/tmdb_classifier.py`
- Test: `backend/tests/unit/test_tmdb_classifier_collision.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_tmdb_classifier_collision.py`:

```python
from unittest.mock import MagicMock, patch

import app.core.tmdb_classifier as tc


def _resp(results):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"results": results}
    return r


def test_search_tmdb_returns_best_and_results():
    results = [
        {"id": 1, "name": "Frasier", "popularity": 75.6},
        {"id": 2, "name": "Frasier", "popularity": 5.7},
    ]
    with patch.object(tc.requests, "get", return_value=_resp(results)):
        best, raw = tc._search_tmdb(tc.TMDB_SEARCH_TV_URL, "Frasier", {}, {}, 5.0)
    assert best is not None and best["id"] == 1
    assert raw == results


def test_search_tmdb_empty_returns_none_and_empty_list():
    with patch.object(tc.requests, "get", return_value=_resp([])):
        best, raw = tc._search_tmdb(tc.TMDB_SEARCH_TV_URL, "Nothing", {}, {}, 5.0)
    assert best is None
    assert raw == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -k search_tmdb -v`
Expected: FAIL — `_search_tmdb` returns a single dict/None, so tuple-unpacking raises `TypeError` / `cannot unpack non-iterable`.

- [ ] **Step 3: Change `_search_tmdb` to return a tuple and update callers**

Replace the body of `_search_tmdb` so every return path yields `(best_or_None, results_list)`:

```python
def _search_tmdb(
    url: str,
    query: str,
    headers: dict,
    base_params: dict,
    timeout: float,
) -> tuple[dict | None, list[dict]]:
    """Search a TMDB endpoint; return (best-matching result, all raw results).

    Prefers results whose name closely matches the query over raw popularity.
    The raw list lets callers detect same-name collisions.
    """
    params = {**base_params, "query": query}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if not results:
                return None, []
            if len(results) == 1:
                return results[0], results
            best = results[0]
            best_name = best.get("name", best.get("title", ""))
            best_sim = _name_similarity(query, best_name)
            for r in results[1:5]:
                r_name = r.get("name", r.get("title", ""))
                r_sim = _name_similarity(query, r_name)
                if r_sim > best_sim:
                    best, best_sim = r, r_sim
            return best, results
    except (requests.RequestException, ConnectionError, TimeoutError):
        pass
    return None, []
```

In `classify_from_tmdb`, update the three search call sites to unpack the tuple. Replace:

```python
    tv_result = _search_tmdb(TMDB_SEARCH_TV_URL, name, headers, base_params, timeout)
    movie_result = _search_tmdb(TMDB_SEARCH_MOVIE_URL, name, headers, base_params, timeout)
```

with:

```python
    tv_result, tv_results = _search_tmdb(TMDB_SEARCH_TV_URL, name, headers, base_params, timeout)
    movie_result, _ = _search_tmdb(TMDB_SEARCH_MOVIE_URL, name, headers, base_params, timeout)
```

And in the variation-retry loop, replace:

```python
            tv_result = _search_tmdb(TMDB_SEARCH_TV_URL, variation, headers, base_params, timeout)
            movie_result = _search_tmdb(
                TMDB_SEARCH_MOVIE_URL, variation, headers, base_params, timeout
            )
```

with:

```python
            tv_result, tv_results = _search_tmdb(
                TMDB_SEARCH_TV_URL, variation, headers, base_params, timeout
            )
            movie_result, _ = _search_tmdb(
                TMDB_SEARCH_MOVIE_URL, variation, headers, base_params, timeout
            )
```

Initialize `tv_results: list[dict] = []` before the first search so the variable always exists for Task 3.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Format + commit**

```bash
uv run ruff format app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git add app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git commit -m "refactor(tmdb): _search_tmdb returns raw results for collision detection"
```

---

## Task 3: Materiality-gated collision detection in `classify_from_tmdb`

**Files:**
- Modify: `backend/app/core/tmdb_classifier.py`
- Test: `backend/tests/unit/test_tmdb_classifier_collision.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_tmdb_classifier_collision.py`:

```python
def _patch_searches(tv_results, movie_results=None):
    """Patch _search_tmdb to return canned TV/movie results regardless of URL."""
    movie_results = movie_results or []

    def fake(url, query, headers, params, timeout):
        if url == tc.TMDB_SEARCH_TV_URL:
            return (tv_results[0] if tv_results else None), tv_results
        return (movie_results[0] if movie_results else None), movie_results

    return patch.object(tc, "_search_tmdb", side_effect=fake)


def test_collision_flagged_when_both_substantial_and_close():
    # One Piece: anime 1999 p60 vs live-action 2023 p38.3 -> ratio 1.57, both >= 10
    tv = [
        {"id": 37854, "name": "One Piece", "popularity": 60.0, "first_air_date": "1999-10-20"},
        {"id": 111110, "name": "One Piece", "popularity": 38.3, "first_air_date": "2023-08-31"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("One Piece", "k" * 41)
    assert sig is not None
    assert sig.ambiguous_identity is True
    assert sig.tmdb_id is not None  # tentative best still reported
    ids = {c["tmdb_id"] for c in sig.candidates}
    assert ids == {37854, 111110}


def test_dominant_twin_not_flagged():
    # Frasier: 1993 p75.6 vs 2023 p5.7 -> ratio 13.3 AND runner-up below floor.
    tv = [
        {"id": 3452, "name": "Frasier", "popularity": 75.6, "first_air_date": "1993-09-16"},
        {"id": 195241, "name": "Frasier", "popularity": 5.7, "first_air_date": "2023-10-12"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Frasier", "k" * 41)
    assert sig is not None
    assert sig.ambiguous_identity is False


def test_noise_twin_not_flagged():
    # Yellowstone 2018 p159 vs 2009 p1.2 -> runner-up below floor.
    tv = [
        {"id": 73586, "name": "Yellowstone", "popularity": 159.7, "first_air_date": "2018-06-20"},
        {"id": 19355, "name": "Yellowstone", "popularity": 1.2, "first_air_date": "2009-01-01"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Yellowstone", "k" * 41)
    assert sig.ambiguous_identity is False


def test_unique_name_not_flagged():
    tv = [{"id": 1396, "name": "Breaking Bad", "popularity": 300.0, "first_air_date": "2008-01-20"}]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Breaking Bad", "k" * 41)
    assert sig.ambiguous_identity is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -k "collision or twin or unique" -v`
Expected: FAIL — `ambiguous_identity` stays `False` (detection not implemented).

- [ ] **Step 3: Add the detection helper and apply it to the TV signal**

In `backend/app/core/tmdb_classifier.py`, add a helper above `classify_from_tmdb`:

```python
def _detect_same_name_candidates(query: str, results: list[dict]) -> list[dict] | None:
    """Return same-name collision candidates when the materiality gate fires, else None.

    "Same-name" = normalized name equals the query's (>= 0.95 similarity). The gate
    fires only when the top two distinct same-name shows are BOTH plausibly real:
    runner-up popularity >= AMBIGUOUS_POPULARITY_FLOOR AND
    top/second popularity ratio <= AMBIGUOUS_POPULARITY_RATIO.
    """
    same = []
    seen_ids = set()
    for r in results:
        rid = r.get("id")
        if rid in seen_ids:
            continue
        name = r.get("name", r.get("original_name", ""))
        if _name_similarity(query, name) >= 0.95:
            seen_ids.add(rid)
            same.append(r)
    if len(same) < 2:
        return None
    same.sort(key=lambda r: r.get("popularity", 0.0), reverse=True)
    top, second = same[0].get("popularity", 0.0), same[1].get("popularity", 0.0)
    if second < AMBIGUOUS_POPULARITY_FLOOR:
        return None
    if second <= 0 or (top / second) > AMBIGUOUS_POPULARITY_RATIO:
        return None
    return [
        {
            "tmdb_id": r["id"],
            "name": r.get("name", r.get("original_name", "")),
            "year": (r.get("first_air_date") or "")[:4],
            "popularity": round(r.get("popularity", 0.0), 1),
        }
        for r in same
    ]
```

Then, in `classify_from_tmdb`, immediately before **each** path that returns a TV signal via `_make_tv_signal(...)`, the cleanest single insertion point is to wrap the final return. Replace the tail of the function (from the `if tv_result and movie_result:` block's TV returns down to the bottom) is error-prone; instead capture the signal once. Restructure the function's return points so TV signals pass through a helper. Add this helper:

```python
def _maybe_flag_tv_ambiguity(signal: TmdbSignal, query: str, tv_results: list[dict]) -> TmdbSignal:
    """Attach same-name collision info to a TV signal when the gate fires."""
    if signal.content_type != ContentType.TV:
        return signal
    candidates = _detect_same_name_candidates(query, tv_results)
    if candidates:
        signal.ambiguous_identity = True
        signal.candidates = candidates
        logger.info(
            f"TMDB: same-name collision for '{query}' — candidates "
            + ", ".join(f"{c['name']} ({c['year']}, id={c['tmdb_id']})" for c in candidates)
        )
    return signal
```

Now wrap every `return _make_tv_signal(...)` in `classify_from_tmdb` with `_maybe_flag_tv_ambiguity(..., name, tv_results)`. There are four such returns (lines for `sim_diff` branch, the `ratio < 2` ambiguous branch, the `tv_pop >= movie_pop` branch, and the `if tv_result:` tail). Example — change:

```python
            if tv_sim > movie_sim:
                return _make_tv_signal(tv_result)
```
to:
```python
            if tv_sim > movie_sim:
                return _maybe_flag_tv_ambiguity(_make_tv_signal(tv_result), name, tv_results)
```

Apply the same wrap to:
- `return _make_tv_signal(tv_result, ambiguous=True)` → `return _maybe_flag_tv_ambiguity(_make_tv_signal(tv_result, ambiguous=True), name, tv_results)`
- the `if tv_pop >= movie_pop: return _make_tv_signal(tv_result)` → wrapped
- the final `if tv_result: return _make_tv_signal(tv_result)` → wrapped

Leave all `_make_movie_signal(...)` returns unchanged (movies have no corpus collision).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Format + commit**

```bash
uv run ruff format app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git add app/core/tmdb_classifier.py tests/unit/test_tmdb_classifier_collision.py
git commit -m "feat(tmdb): materiality-gated same-name collision detection"
```

---

## Task 4: Analyst propagates ambiguity into review fields

`analyst._apply_tmdb_signal` is the single point where a `TmdbSignal` becomes part of `DiscAnalysisResult`. When the signal is ambiguous, it must NOT commit a `tmdb_id`, and must force review with a candidate-naming reason.

**Files:**
- Modify: `backend/app/core/analyst.py`
- Test: `backend/tests/unit/test_analyst_ambiguity.py` (create)

- [ ] **Step 1: Read the current method**

Read `backend/app/core/analyst.py` around `_apply_tmdb_signal` (≈ lines 443–476) to confirm the exact field assignments before editing.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/unit/test_analyst_ambiguity.py`:

```python
from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.models.disc_job import ContentType


def test_ambiguous_signal_clears_id_and_forces_review():
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=3452,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
        ],
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.tmdb_id is None
    assert out.needs_review is True
    assert out.review_reason and "Frasier" in out.review_reason
    assert "1993" in out.review_reason and "2023" in out.review_reason


def test_non_ambiguous_signal_sets_id_normally():
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(content_type=ContentType.TV, confidence=0.85, tmdb_id=1396, tmdb_name="Breaking Bad")
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.tmdb_id == 1396
    assert out.needs_review is False
```

(If `DiscAnalysisResult`'s constructor requires more fields, check its dataclass definition near the top of `analyst.py` and add the minimal required kwargs — it uses defaults for `needs_review=False`, `review_reason=None`, `tmdb_id=None`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_analyst_ambiguity.py -v`
Expected: FAIL — ambiguous case still sets `tmdb_id=3452`, `needs_review=False`.

- [ ] **Step 4: Implement ambiguity propagation**

In `_apply_tmdb_signal`, after the early `if tmdb_signal is None or tmdb_signal.content_type == ContentType.UNKNOWN: return result` guard and BEFORE `result.tmdb_id = tmdb_signal.tmdb_id`, insert:

```python
        # Same-name collision (item 1): two real same-name TMDB shows. Don't commit
        # an id — force review and let the user pick via the existing re-identify UI.
        if getattr(tmdb_signal, "ambiguous_identity", False):
            result.tmdb_id = None
            result.tmdb_name = tmdb_signal.tmdb_name
            result.content_type = tmdb_signal.content_type
            result.needs_review = True
            cands = tmdb_signal.candidates or []
            listed = "; ".join(f"{c['name']} ({c['year']}, #{c['tmdb_id']})" for c in cands)
            result.review_reason = (
                f'Multiple shows match "{tmdb_signal.tmdb_name}" on TMDB: {listed}. '
                f"Pick the correct one."
            )
            return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_analyst_ambiguity.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Format + commit**

```bash
uv run ruff format app/core/analyst.py tests/unit/test_analyst_ambiguity.py
git add app/core/analyst.py tests/unit/test_analyst_ambiguity.py
git commit -m "feat(analyst): ambiguous TMDB signal forces review, withholds tmdb_id"
```

---

## Task 5: Skip subtitle download for ambiguous-identity jobs

`_run_classification` stashes the signal on `analysis._tmdb_signal`. In the identify flow, subtitle download starts (for TV with title+season) *before* the `needs_review` branch — for an ambiguous job that would download the wrong show's subtitles. Guard it.

**Files:**
- Modify: `backend/app/services/identification_coordinator.py`

- [ ] **Step 1: Locate the subtitle-download trigger**

In `identification_coordinator.py`, find the block (≈ lines 306–316):

```python
                # Start subtitle download for ALL TV content
                if (
                    job.content_type == ContentType.TV
                    and job.detected_title
                    and job.detected_season
                ):
                    self._start_subtitle_download(job_id, job.detected_title, job.detected_season)
```

- [ ] **Step 2: Add the ambiguity guard**

Replace the condition with one that also checks the stashed signal:

```python
                # Start subtitle download for ALL TV content — except when identity is
                # ambiguous (same-name collision). Downloading by the tentative name would
                # fetch the wrong show's subtitles before the user disambiguates.
                _amb = bool(
                    getattr(analysis, "_tmdb_signal", None)
                    and getattr(analysis._tmdb_signal, "ambiguous_identity", False)
                )
                if (
                    job.content_type == ContentType.TV
                    and job.detected_title
                    and job.detected_season
                    and not _amb
                ):
                    self._start_subtitle_download(job_id, job.detected_title, job.detected_season)
```

(The `tmdb_id` argument is added to this call in Task 7; leave it 3-arg for now.)

- [ ] **Step 3: Verify the existing test suite still imports/loads**

Run: `uv run pytest tests/unit/ -q`
Expected: PASS (no import errors; existing unit tests unaffected).

- [ ] **Step 4: Format + commit**

```bash
uv run ruff format app/services/identification_coordinator.py
git add app/services/identification_coordinator.py
git commit -m "feat(identify): skip subtitle download for ambiguous-identity jobs"
```

---

## Task 6: Corpus guard — refuse a precomputed corpus whose tmdb_id contradicts the job

**Files:**
- Modify: `backend/app/matcher/episode_identification.py`
- Test: `backend/tests/unit/test_precomputed_guard.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_precomputed_guard.py`:

```python
from app.matcher.episode_identification import precomputed_covers_season


def _manifest(tmdb_id):
    return {"shows": {"Frasier": {"tmdb_id": tmdb_id, "seasons": [1], "episode_counts": {"1": 24}}}}


def test_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Manifest says Frasier == 3452; job expects 195241 -> no coverage, regardless of files.
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=195241
        )
        is False
    )


def test_guard_skipped_when_no_expected_id(tmp_path):
    # No expected id -> guard does not apply; falls through to file existence (absent -> False).
    assert (
        precomputed_covers_season(tmp_path, "Frasier", 1, manifest=_manifest("3452"))
        is False
    )


def test_guard_passes_on_matching_id_then_checks_files(tmp_path):
    # Matching id -> guard passes; files absent so coverage is still False (file gate).
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=3452
        )
        is False
    )
    # Create the on-disk files so the file gate passes too.
    show_dir = tmp_path / "precomputed" / "Frasier"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text("[]")
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=3452
        )
        is True
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_precomputed_guard.py -v`
Expected: FAIL — `precomputed_covers_season` has no `expected_tmdb_id` kwarg (`TypeError`).

- [ ] **Step 3: Add `expected_tmdb_id` to `precomputed_covers_season`**

Change the signature and add the guard right after `show_entry` is fetched:

```python
def precomputed_covers_season(
    cache_dir, show_name: str, season: int, manifest=None, expected_tmdb_id=None
) -> bool:
```

After:

```python
    show_entry = manifest.get("shows", {}).get(show_name)
    if not show_entry or season not in show_entry.get("seasons", []):
        return False
```

insert:

```python
    # Corpus guard (item 1): if the job knows its TMDB id and it contradicts the
    # manifest entry's id, this precomputed corpus is for a DIFFERENT same-named
    # show — refuse it so we never match e.g. the Frasier 2023 revival against the
    # 1993 corpus. Skipped when either id is unknown (backward-compatible).
    entry_id = show_entry.get("tmdb_id")
    if expected_tmdb_id is not None and entry_id is not None and str(entry_id) != str(
        expected_tmdb_id
    ):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_precomputed_guard.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
uv run ruff format app/matcher/episode_identification.py tests/unit/test_precomputed_guard.py
git add app/matcher/episode_identification.py tests/unit/test_precomputed_guard.py
git commit -m "feat(matcher): precomputed_covers_season honors expected_tmdb_id guard"
```

---

## Task 7: EpisodeMatcher carries `expected_tmdb_id`; guard before stale-prune

The matcher must pass its known id into the guard, and `_load_precomputed_season`'s stale-prune branch must NOT wrongly prune a valid entry when the guard rejects on id mismatch (the files exist; it's just the wrong show).

**Files:**
- Modify: `backend/app/matcher/episode_identification.py`
- Test: `backend/tests/unit/test_precomputed_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_precomputed_guard.py`:

```python
from app.matcher.episode_identification import EpisodeMatcher


def test_matcher_stores_expected_tmdb_id(tmp_path):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    assert m.expected_tmdb_id == 195241


def test_load_precomputed_returns_none_on_id_mismatch_without_pruning(tmp_path, monkeypatch):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    manifest = {"shows": {"Frasier": {"tmdb_id": "3452", "seasons": [1], "episode_counts": {"1": 24}}}}
    monkeypatch.setattr(m, "_load_precomputed_manifest", lambda: manifest)
    assert m._load_precomputed_season(1) is None
    # The valid 3452 entry must survive (not pruned as "files missing").
    assert manifest["shows"]["Frasier"]["seasons"] == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_precomputed_guard.py -k "matcher or mismatch" -v`
Expected: FAIL — `EpisodeMatcher.__init__` has no `expected_tmdb_id` (`TypeError`).

- [ ] **Step 3: Add the constructor param + guard logic**

In `EpisodeMatcher.__init__`, add the parameter (after `show_name`) and store it:

```python
    def __init__(
        self,
        cache_dir,
        show_name,
        min_confidence=0.6,
        device=None,
        use_ranked_voting=True,
        min_vote_count=2,
        match_threshold=0.10,
        model_name="small",
        expected_tmdb_id=None,
    ):
        self.cache_dir = Path(cache_dir)
        self.min_confidence = min_confidence
        self.show_name = show_name
        self.expected_tmdb_id = expected_tmdb_id
```

(Keep the rest of `__init__` unchanged.)

In `_load_precomputed_season`, pass the id into the gate and special-case the mismatch so the prune branch is skipped. Replace:

```python
        manifest = self._load_precomputed_manifest()
        if not precomputed_covers_season(
            self.cache_dir, self.show_name, season_number, manifest=manifest
        ):
            # Prune the stale season in-memory so the warning fires at most once per matcher.
            show_entry = (manifest or {}).get("shows", {}).get(self.show_name)
            if show_entry and season_number in show_entry.get("seasons", []):
                logger.warning(
                    f"Precomputed cache lists {self.show_name} S{season_number:02d} "
                    f"but its files are missing; using scraping"
                )
                show_entry["seasons"] = [s for s in show_entry["seasons"] if s != season_number]
                if not show_entry["seasons"]:
                    manifest["shows"].pop(self.show_name, None)
            return None
```

with:

```python
        manifest = self._load_precomputed_manifest()
        # Corpus guard: a positive tmdb_id mismatch means the manifest entry is a
        # different same-named show. Bail BEFORE the stale-prune branch so we don't
        # wrongly drop a valid entry whose files are present.
        show_entry = (manifest or {}).get("shows", {}).get(self.show_name)
        entry_id = show_entry.get("tmdb_id") if show_entry else None
        if (
            self.expected_tmdb_id is not None
            and entry_id is not None
            and str(entry_id) != str(self.expected_tmdb_id)
        ):
            logger.warning(
                f"Precomputed corpus for '{self.show_name}' is tmdb_id {entry_id} but this "
                f"job resolved tmdb_id {self.expected_tmdb_id}; skipping precomputed (wrong show)"
            )
            return None
        if not precomputed_covers_season(
            self.cache_dir,
            self.show_name,
            season_number,
            manifest=manifest,
            expected_tmdb_id=self.expected_tmdb_id,
        ):
            # Prune the stale season in-memory so the warning fires at most once per matcher.
            if show_entry and season_number in show_entry.get("seasons", []):
                logger.warning(
                    f"Precomputed cache lists {self.show_name} S{season_number:02d} "
                    f"but its files are missing; using scraping"
                )
                show_entry["seasons"] = [s for s in show_entry["seasons"] if s != season_number]
                if not show_entry["seasons"]:
                    manifest["shows"].pop(self.show_name, None)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_precomputed_guard.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
uv run ruff format app/matcher/episode_identification.py tests/unit/test_precomputed_guard.py
git add app/matcher/episode_identification.py tests/unit/test_precomputed_guard.py
git commit -m "feat(matcher): EpisodeMatcher.expected_tmdb_id drives corpus guard"
```

---

## Task 8: Subtitle download bypasses `fetch_show_id` when tmdb_id is known

**Files:**
- Modify: `backend/app/matcher/testing_service.py`
- Test: `backend/tests/unit/test_download_subtitles_tmdb_id.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_download_subtitles_tmdb_id.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

import app.matcher.testing_service as ts


def test_download_subtitles_uses_known_id_and_skips_fetch_show_id():
    """When tmdb_id is supplied, fetch_show_id is never called; the id is used directly."""
    fake_fetch_show_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))
    with (
        patch.object(ts, "fetch_show_id", fake_fetch_show_id),
        patch.object(ts, "fetch_show_details", return_value={"name": "Frasier"}),
        patch.object(ts, "fetch_season_details", return_value=0) as season,
        patch.object(ts, "_precomputed_skip_result", return_value=None),
    ):
        # season count 0 -> raises ValueError AFTER id resolution, which is fine:
        # we only assert that fetch_show_id was bypassed and the known id was used.
        with pytest.raises(ValueError):
            ts.download_subtitles("Frasier", 1, tmdb_id=195241)
    fake_fetch_show_id.assert_not_called()
    season.assert_called_once_with("195241", 1)


def test_download_subtitles_without_id_still_resolves_by_name():
    with (
        patch.object(ts, "fetch_show_id", return_value="3452") as fid,
        patch.object(ts, "fetch_show_details", return_value={"name": "Frasier"}),
        patch.object(ts, "fetch_season_details", return_value=0),
        patch.object(ts, "_precomputed_skip_result", return_value=None),
    ):
        with pytest.raises(ValueError):
            ts.download_subtitles("Frasier", 1)
    fid.assert_called_once_with("Frasier")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_download_subtitles_tmdb_id.py -v`
Expected: FAIL — `download_subtitles` has no `tmdb_id` kwarg (`TypeError`).

- [ ] **Step 3: Add `tmdb_id` and the bypass**

Change the signature:

```python
def download_subtitles(
    show_name: str, season: int, *, tmdb_id: int | None = None, use_precomputed: bool = True
) -> dict:
```

Thread `expected_tmdb_id` into the two precomputed fast-path checks. Replace:

```python
    if use_precomputed:
        skip = _precomputed_skip_result(cache_path, show_name, season)
        if skip is not None:
            return skip

    # Get TMDB show ID to determine episode count
    show_id = fetch_show_id(show_name)
    if not show_id:
        raise ValueError(f"Could not find show '{show_name}' on TMDB")
```

with:

```python
    if use_precomputed:
        skip = _precomputed_skip_result(cache_path, show_name, season, expected_tmdb_id=tmdb_id)
        if skip is not None:
            return skip

    # Resolve the TMDB show id. When the caller already knows it (e.g. after the
    # user disambiguated a same-name collision), use it directly — fetch_show_id
    # resolves by NAME and cannot tell two same-named shows apart.
    if tmdb_id is not None:
        show_id = str(tmdb_id)
    else:
        show_id = fetch_show_id(show_name)
        if not show_id:
            raise ValueError(f"Could not find show '{show_name}' on TMDB")
```

And the canonical-name retry fast path — replace:

```python
        if use_precomputed:
            skip = _precomputed_skip_result(cache_path, canonical_show_name, season)
            if skip is not None:
                return skip
```

with:

```python
        if use_precomputed:
            skip = _precomputed_skip_result(
                cache_path, canonical_show_name, season, expected_tmdb_id=tmdb_id
            )
            if skip is not None:
                return skip
```

- [ ] **Step 4: Update `_precomputed_skip_result` to honor the id guard**

`_precomputed_skip_result` (≈ line 229) currently calls `precomputed_episode_codes(cache_path, show_name, season)`, which internally calls `precomputed_covers_season` **without** the id — so it would still return a (wrong-show) skip result on an id mismatch. Add an explicit guard check first. Change the signature and the top of the body. Replace:

```python
def _precomputed_skip_result(cache_path: Path, show_name: str, season: int) -> dict | None:
    """Build a 'skip download' result when the precomputed cache covers the season.

    Returns None when the cache doesn't cover ``show_name`` S``season``. The result
    is sized from the cache's own episode index (no TMDB call), so it works even
    when TMDB is unreachable — the whole point of the precomputed cache.
    """
    from app.matcher.episode_identification import precomputed_episode_codes

    codes = precomputed_episode_codes(cache_path, show_name, season)
    if not codes:
        return None
```

with:

```python
def _precomputed_skip_result(
    cache_path: Path, show_name: str, season: int, expected_tmdb_id: int | None = None
) -> dict | None:
    """Build a 'skip download' result when the precomputed cache covers the season.

    Returns None when the cache doesn't cover ``show_name`` S``season``. The result
    is sized from the cache's own episode index (no TMDB call), so it works even
    when TMDB is unreachable — the whole point of the precomputed cache.

    ``expected_tmdb_id`` applies the corpus guard: a precomputed corpus whose
    manifest id contradicts the job's id is for a different same-named show, so
    we must NOT skip the download against it.
    """
    from app.matcher.episode_identification import (
        precomputed_covers_season,
        precomputed_episode_codes,
    )

    # Corpus guard first — returns False on an id mismatch (different same-named show).
    if not precomputed_covers_season(
        cache_path, show_name, season, expected_tmdb_id=expected_tmdb_id
    ):
        return None

    codes = precomputed_episode_codes(cache_path, show_name, season)
    if not codes:
        return None
```

(The rest of the function — the `logger.info`, `series_cache_dir`, and return dict — is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_download_subtitles_tmdb_id.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
uv run ruff format app/matcher/testing_service.py tests/unit/test_download_subtitles_tmdb_id.py
git add app/matcher/testing_service.py tests/unit/test_download_subtitles_tmdb_id.py
git commit -m "feat(subtitles): download_subtitles bypasses fetch_show_id with known tmdb_id"
```

---

## Task 9: Thread tmdb_id through the subtitle-download coordinator chain

`MatchingCoordinator.download_subtitles` → `to_thread(testing_service.download_subtitles, …)` and its `start_subtitle_download` / `restart_subtitle_download` wrappers must carry `tmdb_id`. The identify + re-identify call sites pass `job.tmdb_id`.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`
- Modify: `backend/app/services/identification_coordinator.py`

- [ ] **Step 1: Add `tmdb_id` to the coordinator methods**

In `matching_coordinator.py`, change `download_subtitles`:

```python
    async def download_subtitles(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
```

and its `to_thread` call:

```python
            result = await asyncio.to_thread(
                download_subtitles, show_name, season, tmdb_id=tmdb_id
            )
```

Change `start_subtitle_download`:

```python
    def start_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Start background subtitle download with tracking."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles(job_id, show_name, season, tmdb_id)
        )
```

Change `restart_subtitle_download`:

```python
    async def restart_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
```

and its final line:

```python
        self.start_subtitle_download(job_id, show_name, season, tmdb_id)
```

- [ ] **Step 2: Pass `job.tmdb_id` at the identify call site**

In `identification_coordinator.py`, the trigger from Task 5 — add the id argument:

```python
                    self._start_subtitle_download(
                        job_id, job.detected_title, job.detected_season, job.tmdb_id
                    )
```

The other `self._start_subtitle_download(` call is the post-identify "files already exist" path (≈ lines 525–527), where `job` is in scope. Replace:

```python
                    if job.detected_title and job.detected_season:
                        self._start_subtitle_download(
                            job_id, job.detected_title, job.detected_season
                        )
```

with:

```python
                    if job.detected_title and job.detected_season:
                        self._start_subtitle_download(
                            job_id, job.detected_title, job.detected_season, job.tmdb_id
                        )
```

- [ ] **Step 3: Pass `job.tmdb_id` in the re-identify restart**

In `re_identify` (≈ lines 739–743), change `restart_args` to include the id:

```python
            restart_args = (
                (job_id, job.detected_title, job.detected_season, job.tmdb_id)
                if should_restart_subtitles
                else None
            )
```

`self._restart_subtitle_download(*restart_args)` then forwards the 4th positional arg.

- [ ] **Step 4: Verify imports + suite load**

Run: `uv run pytest tests/unit/ -q`
Expected: PASS (no import/signature errors).

- [ ] **Step 5: Commit**

```bash
uv run ruff format app/services/matching_coordinator.py app/services/identification_coordinator.py
git add app/services/matching_coordinator.py app/services/identification_coordinator.py
git commit -m "feat(subtitles): thread tmdb_id through download coordinator + call sites"
```

---

## Task 10: Curator + match call thread tmdb_id into the matcher

`curator.match_single_file` gains `tmdb_id`; `_ensure_initialized` uses the known id (skipping `fetch_show_id`) and passes `expected_tmdb_id` to `EpisodeMatcher`. `_match_single_file_inner` passes `job.tmdb_id`.

**Files:**
- Modify: `backend/app/core/curator.py`
- Modify: `backend/app/services/matching_coordinator.py`
- Test: `backend/tests/unit/test_curator_tmdb_id.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_curator_tmdb_id.py`:

```python
from unittest.mock import MagicMock, patch

from app.core.curator import EpisodeCurator


def test_ensure_initialized_uses_known_id_and_skips_fetch_show_id():
    cur = EpisodeCurator()
    captured = {}

    class FakeMatcher:
        def __init__(self, cache_dir, show_name, min_confidence, expected_tmdb_id=None):
            captured["expected_tmdb_id"] = expected_tmdb_id
            captured["show_name"] = show_name

    fake_fetch_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))
    cfg = MagicMock()
    cfg.subtitles_cache_path = None
    with (
        patch("app.matcher.episode_identification.EpisodeMatcher", FakeMatcher),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.matcher.tmdb_client.fetch_show_details", return_value={"name": "Frasier"}),
        patch("app.services.config_service.get_config_sync", return_value=cfg),
    ):
        ok = cur._ensure_initialized("Frasier", tmdb_id=195241)
    assert ok is True
    assert captured["expected_tmdb_id"] == 195241
    fake_fetch_id.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_curator_tmdb_id.py -v`
Expected: FAIL — `_ensure_initialized` has no `tmdb_id` parameter (`TypeError`).

- [ ] **Step 3: Update `_ensure_initialized` and `match_single_file`**

In `curator.py`, add `_current_tmdb_id` to `__init__`:

```python
        self._current_show_id: str | None = None
        self._current_tmdb_id: int | None = None
```

Change `_ensure_initialized` signature + re-init condition + id resolution:

```python
    def _ensure_initialized(self, show_name: str, tmdb_id: int | None = None) -> bool:
        """Lazily initialize the matcher library for a specific show.

        When ``tmdb_id`` is known (e.g. after the user disambiguated a same-name
        collision), it is used directly instead of resolving by name — and it is
        passed to EpisodeMatcher as the corpus guard's expected id.
        """
        # Re-initialize if show name OR known id changed.
        if (
            self._initialized
            and self._current_show == show_name
            and self._current_tmdb_id == tmdb_id
        ):
            return self._matcher is not None

        self._current_show = show_name
        self._current_tmdb_id = tmdb_id
```

Inside the `try:` block, replace the canonical-name resolution so it uses the known id when present:

```python
            canonical_name = show_name
            try:
                if tmdb_id is not None:
                    resolved_id = tmdb_id
                    self._current_show_id = str(tmdb_id)
                else:
                    resolved_id = fetch_show_id(show_name)
                    self._current_show_id = str(resolved_id) if resolved_id else None
                if resolved_id:
                    details = fetch_show_details(resolved_id)
                    if details and "name" in details:
                        canonical_name = details["name"]
                        logger.info(
                            f"Resolved '{show_name}' to canonical '{canonical_name}' for matching"
                        )
            except Exception as e:
                logger.warning(f"Failed to resolve canonical name for '{show_name}': {e}")
```

Update the `EpisodeMatcher(...)` construction to pass the guard id:

```python
            self._matcher = EpisodeMatcher(
                cache_dir=self._cache_dir,
                show_name=canonical_name,
                min_confidence=self.LOW_CONFIDENCE_THRESHOLD,
                expected_tmdb_id=tmdb_id,
            )
```

Change `match_single_file` signature + the `_ensure_initialized` call inside it:

```python
    async def match_single_file(
        self,
        file_path: Path,
        series_name: str | None,
        season: int | None,
        progress_callback: Callable[..., None] | None = None,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        tmdb_id: int | None = None,
    ) -> MatchResult:
```

and:

```python
        if series_name:
            initialized = self._ensure_initialized(series_name, tmdb_id)
```

- [ ] **Step 4: Pass `job.tmdb_id` from the matching coordinator**

In `matching_coordinator.py` `_match_single_file_inner`, change the `match_single_file` call (≈ line 706) to forward the id:

```python
                result = await episode_curator.match_single_file(
                    file_path,
                    series_name=job.detected_title,
                    season=job.detected_season,
                    progress_callback=on_progress,
                    num_points=num_points,
                    min_vote_count=min_vote_count,
                    tmdb_id=job.tmdb_id,
                )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_curator_tmdb_id.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
uv run ruff format app/core/curator.py app/services/matching_coordinator.py tests/unit/test_curator_tmdb_id.py
git add app/core/curator.py app/services/matching_coordinator.py tests/unit/test_curator_tmdb_id.py
git commit -m "feat(curator): thread tmdb_id into matcher init + corpus guard"
```

---

## Task 11: Integration test — ambiguous collision routes to review; re-identify re-keys

Validates the end-to-end seams using the app's real async session. Per project guidance, integration tests run against the real app DB — set up state **directly in the DB**, never let the test organize real files.

**Files:**
- Test: `backend/tests/integration/test_show_identity_collision.py` (create)

- [ ] **Step 1: Write the test**

Create `backend/tests/integration/test_show_identity_collision.py`:

```python
import pytest
from sqlalchemy import text

from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.database import async_session, init_db
from app.models.disc_job import ContentType


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


def test_ambiguous_signal_produces_review_result_without_id():
    """The analyst seam: an ambiguous TV signal yields needs_review + no tmdb_id."""
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=37854,
        tmdb_name="One Piece",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 37854, "name": "One Piece", "year": "1999", "popularity": 60.0},
            {"tmdb_id": 111110, "name": "One Piece", "year": "2023", "popularity": 38.3},
        ],
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.needs_review is True
    assert out.tmdb_id is None
    assert "One Piece" in out.review_reason


async def test_match_single_file_forwards_tmdb_id(monkeypatch):
    """The curator seam: a known tmdb_id reaches _ensure_initialized."""
    from app.core.curator import EpisodeCurator

    cur = EpisodeCurator()
    seen = {}

    def fake_ensure(show_name, tmdb_id=None):
        seen["show_name"] = show_name
        seen["tmdb_id"] = tmdb_id
        return False  # matcher unavailable -> fallback path, no real matching

    monkeypatch.setattr(cur, "_ensure_initialized", fake_ensure)
    from pathlib import Path

    await cur.match_single_file(Path("nonexistent.mkv"), "Frasier", 1, tmdb_id=195241)
    assert seen == {"show_name": "Frasier", "tmdb_id": 195241}
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_show_identity_collision.py -v`
Expected: PASS (2 passed). If it errors with `no such table: app_config`, run once more after `init_db()` populates the worktree DB; the autouse fixture calls it.

- [ ] **Step 3: Commit**

```bash
uv run ruff format tests/integration/test_show_identity_collision.py
git add tests/integration/test_show_identity_collision.py
git commit -m "test(identity): integration coverage for collision review + tmdb_id threading"
```

---

## Task 12: Full regression + lint sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the unit suite**

Run: `uv run pytest tests/unit/ -q`
Expected: PASS. Note: `test_movie_ambiguous_rip_first_workflow` is a known pre-existing flaky failure (staging cleanup race) — unrelated to this change.

- [ ] **Step 2: Run the targeted new tests together**

Run: `uv run pytest tests/unit/test_tmdb_classifier_collision.py tests/unit/test_analyst_ambiguity.py tests/unit/test_precomputed_guard.py tests/unit/test_download_subtitles_tmdb_id.py tests/unit/test_curator_tmdb_id.py tests/integration/test_show_identity_collision.py -v`
Expected: PASS (all).

- [ ] **Step 3: Lint + format check**

Run: `uv run ruff check app/ tests/ && uv run ruff format --check app/ tests/`
Expected: no errors. Fix any reported issues, re-run.

- [ ] **Step 4: Final commit if lint produced changes**

```bash
git add -A
git commit -m "chore(identity): lint/format sweep for show-identity spine"
```

---

## Manual Verification (real disc / simulation — optional, after merge readiness)

Per CLAUDE.md "Real-disc testing setup": run exactly ONE backend against the real config DB (`DATABASE_URL` → the populated `backend/engram.db`), observe via WebSocket + `rip.log`. With the Frasier 2023 disc:
1. Insert → expect job identifies as Frasier 1993 (dominant), matching abstains → `REVIEW_NEEDED` (this is correct for item 1; item 3 will add the explanatory reason).
2. Open Re-Identify, pick the 2023 entry → confirm `job.tmdb_id` updates, subtitle download re-runs keyed by id (check `rip.log` / `~/.engram/engram.log` for the id), and the corpus guard logs "skipping precomputed (wrong show)".
3. Confirm matching then proceeds against the freshly-downloaded 2023 subtitles.

**Cleanup before PR (scoped to this session's ports):** stop the uvicorn/makemkvcon processes this session started (see CLAUDE.md "Parallel sessions / worktree isolation").

---

## Notes / Known Follow-ups (still item 1 scope, low priority)

- `curator._chromaprint_prepass` and the LLM fallback call `fetch_show_id(series_name)` independently (curator ≈ lines 501, 563). For a same-name collision these would also mis-resolve. The Frasier case uses the ASR path (chromaprint gated off), which this plan fixes. A follow-up can thread `tmdb_id` into those two calls the same way (`if tmdb_id: show_id = str(tmdb_id)`). Out of the critical path; do not block this plan on it.
- `start_subtitle_download_all_seasons` (multi-season import) is left name-keyed; the unknown-season import flow is a separate path and not part of the reported bug.

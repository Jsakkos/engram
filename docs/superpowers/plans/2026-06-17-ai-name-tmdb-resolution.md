# AI Over-Specified Title → TMDB Resolution Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an AI-guessed disc title that includes a season/box-set subtitle (e.g. `"Avatar: The Last Airbender Book One: Water"`) from failing every TMDB lookup, which leaves `tmdb_id` null and cascades into an unnecessary identity prompt and a total subtitle-download failure.

**Architecture:** A single new name-variation rule in `generate_name_variations` (the function shared by `fetch_show_id` and `classify_from_tmdb`) strips a trailing `Book/Volume/Part/Season/Chapter <number|ordinal>` subtitle so the series title resolves on TMDB. Because the identify-time AI re-query, the resume-time `_resolve_missing_tmdb_id`, and the subtitle `fetch_show_id` all funnel through that one function, the fix lands once and covers all three paths. A secondary prompt tweak stops the over-specification at the source as defense-in-depth.

**Tech Stack:** Python 3.11+/3.13, FastAPI, SQLModel, pytest (`uv run pytest`), `requests` (mocked in tests), TMDB `/search/tv` API.

---

## Background — root cause (verified 2026-06-17)

Live job 206 (`F:`, `Avatar_Book_1_Disc_1`) on the running **v0.21.3** frozen build:

| Field | Value |
|---|---|
| `detected_title` | `"Avatar: The Last Airbender Book One: Water"` (AI/Gemini guess) |
| `tmdb_id` / `tmdb_name` | `null` / `null` |
| `subtitle_status` | `"failed"` (0 downloaded, 0 total) |
| `state` | `"ripping"` (rip-first worked — disc was **not** blocked) |

Backend error log:
```
Subtitle download ValueError for Avatar: The Last Airbender Book One: Water S1:
Could not find show 'Avatar: The Last Airbender Book One: Water' on TMDB
```

Live TMDB query with the user's key (single source of all three symptoms):

| Query | Results |
|---|---|
| `"Avatar: The Last Airbender Book One: Water"` (AI output) | **0** |
| `"Avatar: The Last Airbender"` (subtitle stripped) | **2 → id 246** ✅ |
| `"Avatar The Last Airbender Book One Water"` (existing punctuation variation) | **0** |

**Why it fails today:** `"Book One: Water"` is the *season* subtitle, not part of the series title TMDB indexes. The AI (`ai_provider: "gemini"`) appended it to the show name. `generate_name_variations` (`app/matcher/tmdb_client.py:163`) strips `S1`/`Season 1`/`Disc 1`/dash-subtitles, but has **no rule for a `Book/Volume/Part …` subtitle**, so neither `fetch_show_id` nor `classify_from_tmdb` recovers.

**Cascade:**
1. Identify-time AI re-query (`identification_coordinator.py:1632` → `_try_tmdb` → `classify_from_tmdb`) returns no signal → `analysis.detected_name = ai_name`, `tmdb_id` stays null (`identification_coordinator.py:1681-1683`).
2. Null `tmdb_id` + TV + detected_title → **Gate B** (`identification_coordinator.py:484`) ships to RIPPING with a `kind="name"` prompt → the modal you saw (rip continued behind it).
3. Subtitle download (`fetch_show_id` fallback, no tmdb_id) chokes on the same string → `ValueError` → `subtitle_status="failed"`.
4. Resume path (`_resolve_missing_tmdb_id`, `identification_coordinator.py:778`) re-runs `classify_from_tmdb` on the **same** uncleaned string → still null, so confirming the name in the prompt doesn't fix it either.

**Expected behavior after Fix A:** at identify time, `classify_from_tmdb("Avatar: The Last Airbender Book One: Water")` tries the new stripped variation `"Avatar: The Last Airbender"` → TMDB id 246. The analyst corroborates (token Jaccard of the two names = 0.5, meeting the 0.5 threshold), collapses `detected_title` to `"Avatar: The Last Airbender"`, auto-rips with **no prompt**, and subtitles download via the existing tmdb_id path. No new tmdb_id search path is needed — the existing ones work once an id resolves.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/matcher/tmdb_client.py` | Name-variation generation shared by all TMDB name lookups | **Modify** — add trailing-set-subtitle rule + module-level compiled regex |
| `backend/tests/unit/test_tmdb_client.py` | Unit tests for `generate_name_variations` / `fetch_show_id` | **Modify** — add variation + guard tests |
| `backend/tests/unit/test_tmdb_classifier.py` | Unit tests for `classify_from_tmdb` | **Modify** — add recovery-via-variation test |
| `backend/app/core/ai_identifier.py` | The disc-identification LLM prompt | **Modify** — instruct series-title-only for TV (Fix B) |
| `backend/tests/unit/test_ai_identifier.py` | Tests for the AI identifier | **Modify** — prompt-contract assertion |
| `CHANGELOG.md` | Release notes (`[Unreleased]`) | **Modify** — one `Fixed` bullet |

No new files. Follows the existing flat matcher layout and colocated unit-test convention.

---

## Task 1: Strip trailing season/box-set subtitle in `generate_name_variations` (Fix A — root cause)

**Files:**
- Modify: `backend/app/matcher/tmdb_client.py` (add module constant near other module-level regexes; add a block in `generate_name_variations` after the "Remove subtitle after dash" block, currently `:218-222`)
- Test: `backend/tests/unit/test_tmdb_client.py` (in `class TestFetchShowIdVariations` or a new `class TestSetSubtitleStripping`)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/test_tmdb_client.py`. `generate_name_variations` is pure string logic — no mocks needed. Confirm the import at the top of the file includes it (add `generate_name_variations` to the existing `from app.matcher.tmdb_client import ...` if absent):

```python
@pytest.mark.unit
class TestSetSubtitleStripping:
    """A box-set / AI-guessed title can append a season subtitle
    ("Book One: Water", "Volume 2", "Part Two") to the series name. TMDB
    indexes the series name only, so we must offer the stripped form as a
    search variation. Regression for the Avatar: The Last Airbender disc
    (label 'Avatar_Book_1_Disc_1') whose AI guess resolved no tmdb_id."""

    def test_strips_trailing_book_subtitle(self):
        variations = generate_name_variations(
            "Avatar: The Last Airbender Book One: Water"
        )
        assert "Avatar: The Last Airbender" in variations

    def test_strips_trailing_volume_number(self):
        variations = generate_name_variations("Trigun Volume 2")
        assert "Trigun" in variations

    def test_strips_trailing_part_ordinal(self):
        variations = generate_name_variations("Fargo Part Two")
        assert "Fargo" in variations

    def test_does_not_strip_marker_without_a_count(self):
        # "of Me" is not a number/ordinal/roman numeral, so "Part of Me"
        # must never be truncated to "Part".
        variations = generate_name_variations("Part of Me")
        assert "Part" not in variations

    def test_clean_series_name_unaffected(self):
        # No trailing set-subtitle: the stripped form equals the input and is
        # not added as a redundant variation.
        variations = generate_name_variations("The Wire")
        assert "The Wire" not in variations  # original is excluded by dedup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_tmdb_client.py::TestSetSubtitleStripping -v`
Expected: `test_strips_trailing_book_subtitle`, `test_strips_trailing_volume_number`, `test_strips_trailing_part_ordinal` FAIL (assertion: stripped form not present). The two guard tests should already PASS (the rule doesn't exist yet, so nothing is wrongly stripped).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/matcher/tmdb_client.py`, add a module-level compiled regex near the top (with the other module constants, after the imports):

```python
# A box-set or AI-guessed title sometimes appends a season/set subtitle to the
# series name (e.g. "Avatar: The Last Airbender Book One: Water", "Trigun
# Volume 2", "Fargo Part Two"). TMDB indexes the series title alone, so the
# over-specified string returns zero results. The marker MUST be followed by a
# number / ordinal word / roman numeral, so a real title like "Part of Me" or
# "Band of Brothers" is never truncated.
_TRAILING_SET_SUBTITLE_RE = re.compile(
    r"\s+(?:Book|Volume|Vol\.?|Part|Pt\.?|Season|Series|Chapter)\s+"
    r"(?:\d+|[IVXLCDM]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|"
    r"Eleven|Twelve)\b.*$",
    re.IGNORECASE,
)
```

Then, inside `generate_name_variations`, immediately after the existing "Remove subtitle after dash" block (currently ending at line 222), add:

```python
    # Remove a trailing season/box-set subtitle (see _TRAILING_SET_SUBTITLE_RE).
    set_subtitle_cleaned = _TRAILING_SET_SUBTITLE_RE.sub("", current).strip()
    if set_subtitle_cleaned and set_subtitle_cleaned != current:
        variations.append(set_subtitle_cleaned)
```

(`current` already tracks the progressively cleaned name; the trailing dedup at `:251-259` removes duplicates and the original.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_tmdb_client.py::TestSetSubtitleStripping -v`
Expected: all 5 PASS.

- [ ] **Step 5: Run the full tmdb_client suite for regressions**

Run: `cd backend && uv run pytest tests/unit/test_tmdb_client.py -v`
Expected: all PASS (existing variation tests unaffected — the new rule only *adds* a candidate).

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/tmdb_client.py backend/tests/unit/test_tmdb_client.py
git commit -m "fix(tmdb): strip trailing season/box-set subtitle as a name variation

An AI-guessed or box-set disc title that appends a season subtitle to the
series name (e.g. 'Avatar: The Last Airbender Book One: Water') resolved no
tmdb_id, which forced an identity prompt and failed subtitle download. Add a
variation that strips a trailing Book/Volume/Part/Season/Chapter <count>
subtitle so the series title resolves on TMDB.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Verify `classify_from_tmdb` recovers via the new variation

**Why:** `classify_from_tmdb` (`app/core/tmdb_classifier.py:293`) is what the identify-time AI re-query and `_resolve_missing_tmdb_id` both call. It tries `generate_name_variations` only when the original name returns nothing (`:318-321`). This task proves the end-to-end recovery without a live network or DB.

**Files:**
- Test: `backend/tests/unit/test_tmdb_classifier.py` (new test; reuse the file's existing mock style for `_search_tmdb`)

- [ ] **Step 1: Write the failing test**

`classify_from_tmdb` calls `_search_tmdb(url, name, headers, base_params, timeout) -> (result, results)`. Mock it so only the stripped name returns a TV hit:

```python
@pytest.mark.unit
def test_classify_recovers_show_via_set_subtitle_variation():
    """The over-specified AI name returns nothing on TMDB, but the stripped
    series-name variation resolves. Regression for Avatar: The Last Airbender
    (job 206, label 'Avatar_Book_1_Disc_1')."""
    from app.core import tmdb_classifier

    tv_hit = {
        "id": 246,
        "name": "Avatar: The Last Airbender",
        "first_air_date": "2005-02-21",
        "popularity": 100.0,
        "original_name": "Avatar: The Last Airbender",
    }

    def fake_search(url, name, headers, base_params, timeout):
        if url == tmdb_classifier.TMDB_SEARCH_TV_URL and name == "Avatar: The Last Airbender":
            return tv_hit, [tv_hit]
        return None, []

    with patch.object(tmdb_classifier, "_search_tmdb", side_effect=fake_search):
        signal = tmdb_classifier.classify_from_tmdb(
            "Avatar: The Last Airbender Book One: Water", "test_key"
        )

    assert signal is not None
    assert signal.tmdb_id == 246
    assert signal.content_type == ContentType.TV
```

Ensure the test file imports `patch` (`from unittest.mock import patch`) and `ContentType` (`from app.models.disc_job import ContentType`) — match the existing imports in the file; add only what's missing.

- [ ] **Step 2: Run test to verify it fails BEFORE Task 1 is present / passes after**

Run: `cd backend && uv run pytest tests/unit/test_tmdb_classifier.py::test_classify_recovers_show_via_set_subtitle_variation -v`
Expected with Task 1 applied: PASS. (To confirm it's a real regression test, temporarily comment out the Task 1 block → it should FAIL with `signal is None` because no variation produces `"Avatar: The Last Airbender"`. Restore the block.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_tmdb_classifier.py
git commit -m "test(tmdb): classify_from_tmdb recovers series via subtitle-stripped variation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Tighten the AI identification prompt (Fix B — defense in depth)

**Why:** Stop the over-specification at the source. Fix A makes the lookup resilient; Fix B reduces how often the AI emits a season subtitle at all, which also keeps `detected_title` clean for display/organize when corroboration is borderline.

**Files:**
- Modify: `backend/app/core/ai_identifier.py:14-26` (`IDENTIFICATION_PROMPT`)
- Test: `backend/tests/unit/test_ai_identifier.py` (prompt-contract assertion)

- [ ] **Step 1: Write the failing test**

The LLM output isn't deterministic, so test the prompt *contract* (the instruction is present), not model behavior:

```python
def test_prompt_instructs_series_title_only_for_tv():
    from app.core.ai_identifier import IDENTIFICATION_PROMPT

    text = IDENTIFICATION_PROMPT.lower()
    # Must tell the model to exclude season/book/volume/part subtitles for TV.
    assert "series title only" in text
    assert "book" in text and "volume" in text and "part" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_ai_identifier.py::test_prompt_instructs_series_title_only_for_tv -v`
Expected: FAIL (`assert "series title only" in text`).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/core/ai_identifier.py`, add a rule line to `IDENTIFICATION_PROMPT` after the existing `- "title" must be the official English title ...` rule:

```python
- For TV shows, "title" must be the SERIES title only — the official English series name as it appears on TMDB. Do NOT append a season, book, volume, part, or chapter subtitle (e.g. return "Avatar: The Last Airbender", NOT "Avatar: The Last Airbender Book One: Water"). The season is captured separately.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_ai_identifier.py -v`
Expected: all PASS (existing tests in the file plus the new one).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_identifier.py backend/tests/unit/test_ai_identifier.py
git commit -m "fix(ai): instruct series-title-only for TV disc identification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Changelog entry

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Fixed`)

- [ ] **Step 1: Add the bullet**

Under `## [Unreleased]` → `### Fixed` (create the heading if absent), add:

```markdown
- Disc titles that include a season/box-set subtitle (e.g. an AI-guessed "Avatar: The Last Airbender Book One: Water") now resolve their TMDB id by stripping the trailing Book/Volume/Part/Season subtitle. Previously the over-specified name matched nothing on TMDB, which left the job without a tmdb_id — forcing an identity prompt and failing subtitle download. (#NNN)
```

(Replace `#NNN` with the PR number once opened.)

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note AI/box-set subtitle TMDB resolution fix

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Verification (manual, no code)

- [ ] **Step 1: Full backend test sweep**

Run: `cd backend && uv run pytest tests/unit/test_tmdb_client.py tests/unit/test_tmdb_classifier.py tests/unit/test_ai_identifier.py -v`
Expected: all PASS.

- [ ] **Step 2: Lint/format**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: clean (or `uv run ruff format .` then re-check).

- [ ] **Step 3: Live TMDB confirmation (optional, uses real key)**

Re-run the diagnostic from the investigation: search TMDB for `"Avatar: The Last Airbender Book One: Water"` (expect 0) and confirm `generate_name_variations(...)` now yields `"Avatar: The Last Airbender"` which searches to id 246. (Earlier worktree env note: `uv sync` first if the `.venv` is fresh.)

- [ ] **Step 4: Simulated end-to-end (DEBUG backend)**

This fix's seam is identify-time TMDB resolution. `POST /api/simulate/insert-disc` *bypasses* identify (`project_sim_identification_bypass`), so it can't exercise the AI→TMDB path. Verify instead via the unit + classifier tests above, and (if a real disc is available) by re-inserting `Avatar_Book_1_Disc_1` on a build containing this fix and confirming the job auto-rips with `tmdb_id=246`, no prompt, and `subtitle_status != "failed"`.

---

## Appendix: immediate remediation for the in-flight job 206 (deferred per user)

Not part of the code change, but the running rip will hit the missing-`tmdb_id` wall at matching. To rescue it without re-ripping, re-identify the live job to the clean series name so `tmdb_id 246` backfills (`_resolve_missing_tmdb_id`) and matching + subtitles unblock:

- Use the dashboard **"Confirm title" / Re-Identify** affordance on the Avatar card and enter `Avatar: The Last Airbender` (no subtitle), **or**
- Call the re-identify REST endpoint with the corrected title (confirm the exact route in `backend/app/api/routes.py` before use).

Because the live build is **v0.21.3** (already has the rip-first gates), this is a data fix for one job, independent of shipping Fixes A/B.

---

## Self-Review

- **Spec coverage:** Q1 "why prompted" → root cause + Fix A (Task 1) removes the null-tmdb_id that triggers Gate B. Q2 "name looked right" → explained (AI display name vs TMDB query name); Fix B (Task 3) keeps the stored name clean. Q3 "no subtitles / search by tmdb_id" → Fix A resolves the id so the existing tmdb_id subtitle path works; no new search path needed (documented in Background). In-flight job → Appendix. ✅
- **Placeholder scan:** `#NNN` in Task 4 is an intentional PR-number fill at PR time, called out explicitly; no other placeholders. Test/impl code is complete and concrete. ✅
- **Type/name consistency:** `generate_name_variations`, `classify_from_tmdb`, `_search_tmdb(url, name, headers, base_params, timeout) -> (result, results)`, `TMDB_SEARCH_TV_URL`, `ContentType`, `IDENTIFICATION_PROMPT`, `_TRAILING_SET_SUBTITLE_RE` used consistently across tasks. ✅
- **Risk:** the new rule only *adds* a search variation tried after the exact name fails, and the count-marker requirement prevents truncating real titles — low regression risk; guarded by `test_does_not_strip_marker_without_a_count`.

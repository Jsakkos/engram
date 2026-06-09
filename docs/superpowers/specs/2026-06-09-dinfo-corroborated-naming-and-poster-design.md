# TMDB-authoritative, DINFO-corroborated naming + poster-by-tmdb_id

**Date:** 2026-06-09
**Status:** Approved design, pending implementation plan
**Branch:** `claude/busy-banzai-d3cc34`

## Problem

A Breaking Bad disc (volume label `BREAKINGBADS2`) was organized into
`X:\media\series\Breakingbad\Season 2\...` (one concatenated word) and showed no
poster thumbnail on its disc card. Investigation of job 99 in `~/.engram/engram.log`
established the root cause.

### Evidence (job 99)

| Time | Log line | Meaning |
|------|----------|---------|
| 08:47:16 | `Drive event: F: inserted (label: BREAKINGBADS2)` | Volume label has no word separators |
| 08:47:53 | `Scan completed ... disc name: 'Breaking Bad: Season 2: Disc 1'` | MakeMKV DINFO reported a clean, correct name |
| 08:47:58 | `TMDB matched via variation 'Breaking bad' (original: 'Breakingbad')` | TMDB matched, but only after inserting a space |
| 08:47:58 | `TMDB: TV match 'Breaking Bad' (id=1396 ...)` | Correct identity resolved |
| 08:47:58 | `TMDB name 'Breaking Bad' is dissimilar to parsed name 'Breakingbad' — ignoring TMDB name override` | The authoritative name was **rejected** |
| 09:51:43 | `Organizing TV extra: ... -> X:\media\series\Breakingbad\Season 2\...` | Garbled name reaches the filesystem |

Persisted job 99 row (read-only query of `engram.db`):

```
detected_title = 'Breakingbad'   ← drives folder + poster search
tmdb_id        = 1396            ← correct, authoritative identity (ignored downstream)
tmdb_name      = 'Breaking Bad'  ← correct name, resolved then discarded
```

### Root cause

The system resolved the correct identity (`tmdb_id=1396`, `tmdb_name="Breaking Bad"`,
`ambiguous_identity=False`) but two consumers kept using the garbled label-derived
name `Breakingbad`:

1. **Name guard false-negative.** `analyst._names_are_similar()` is pure word-token
   Jaccard. `Breakingbad` → `{breakingbad}` vs `Breaking Bad` → `{breaking, bad}` →
   intersection 0 → score 0.0 < 0.5 → the authoritative TMDB name was rejected
   (`analyst.py:300` and the twin check at `analyst.py:540`).

2. **DINFO relegated to a dead branch.** The MakeMKV DINFO disc name
   (`Breaking Bad: Season 2: Disc 1`) is only consulted by a fallback gated behind
   `if not tmdb_signal:` (`identification_coordinator.py:1139`). Because TMDB *did*
   match off the garbled label (via lenient variation matching), `tmdb_signal` was
   truthy and the better name was never used.

3. **`_parse_disc_name` can't parse the colon form.** Verified empirically:
   `_parse_disc_name('Breaking Bad: Season 2: Disc 1')` returns
   `('Breaking Bad: Season 2:', None)` — mangled title, lost season. So even if the
   DINFO branch had run, it would have produced garbage. (Colons *inside* a title are
   fine: `'Star Trek: Strange New Worlds - Season 3 (Disc 1)'` → `('Star Trek: Strange
   New Worlds', 3)`.)

4. **Poster endpoint ignores `tmdb_id`.** `get_job_poster` (`routes.py:1329`) always
   searches `/search/{tv|movie}?query=detected_title`. With `detected_title="Breakingbad"`
   and no variation expansion, the raw query returns nothing → `poster_url: None` → blank
   card. The authoritative `tmdb_id=1396` sitting on the job is never used.

Note: episode *matching* succeeded (the matcher canonicalizes
`'Breakingbad' → 'Breaking Bad'` for the subtitle cache). The defect is isolated to the
display/organize name and the poster.

## Design principle

**TMDB is the authoritative name source. DINFO is a corroborating input that lets us
trust the TMDB name (and serves as a better fallback base name when TMDB is absent) —
it is not itself the display name.**

The breakingbad bug was not a missing name; it was a guard rejecting the authoritative
name because it only compared against one garbled signal. The fix makes the "should I
trust the TMDB name?" decision robust by (a) comparing whitespace-insensitively and
(b) corroborating against every on-disc naming signal, including DINFO.

## Changes

Five coordinated changes plus tests.

### 1. `_names_are_similar` — whitespace/punctuation-insensitive (`app/core/analyst.py`)

Add a second acceptance path: if word-token Jaccard is below threshold, collapse both
strings (strip all non-alphanumerics, lowercase) and accept when they are **equal**.

- `Breakingbad` vs `Breaking Bad` → `breakingbad == breakingbad` → similar.
- Conservative: only rescues "same name, different spacing/punctuation"; never makes
  unrelated names match (`breakingbad` ≠ `friends`).
- Existing token path and the empty-token "allow override" branch are unchanged.
- **Rejected (YAGNI):** substring/containment on collapsed strings — risks short-token
  false positives (`up` ⊂ `supernatural`); the reported class does not need it.

Used by both corroboration call sites (`analyze()` and the TV-result builder).

### 2. `_parse_disc_name` — handle colon-as-separator (`app/core/analyst.py`)

Treat `:` like `-` immediately before the trailing `Season`/`Disc` indicators, and trim
trailing separator punctuation (`:`, `-`, en-dash, whitespace) from the resulting title,
while leaving colons *inside* a title intact.

Acceptance cases:

| Input | Expected |
|-------|----------|
| `Breaking Bad: Season 2: Disc 1` | `('Breaking Bad', 2)` |
| `Star Trek: Strange New Worlds - Season 3 (Disc 1)` | `('Star Trek: Strange New Worlds', 3)` (unchanged) |
| `The Office - Season 2` | `('The Office', 2)` (unchanged) |
| `Supernatural Season 11 Disc 2` | `('Supernatural', 11)` (unchanged) |

### 3. Name selection in `analyze()` + TV-result builder (`app/core/analyst.py`)

- **Base name** = `disc_title` (parsed DINFO title) if present, else `label_name`
  (volume-label parse). DINFO preferred as the base.
- **Adopt `tmdb_signal.tmdb_name` as authoritative** when (a) there is no base name to
  contradict it (`detected_name is None`), **or** (b) it is corroborated by the label name
  **OR** the DINFO title (using the whitespace-insensitive `_names_are_similar`).
  Otherwise keep the base name and log which signals were checked. (Clause (a) preserves
  the existing `detected_name is None` adoption path.)
- This replaces the prior `name_hint`-bypasses-the-guard semantics with
  "DINFO is one of the corroboration signals." A *wrong* TMDB match still cannot silently
  override (it corroborates with neither signal → base name kept); a *right* TMDB match
  can no longer be rejected merely because the volume label was garbled.

Interface: replace the `name_hint` parameter of `analyze()` with an explicit
`disc_title: str | None` (parsed DINFO title) argument, documented as "base display name
when present and an additional corroboration signal for the authoritative TMDB name."

### 4. Wire DINFO unconditionally in `_compute_classification` (`app/services/identification_coordinator.py`)

- Parse the DINFO disc name **whenever it is present** (after rejecting generic/empty
  names via the existing normalization), producing `disc_title` / `disc_season` —
  decoupled from the `if not tmdb_signal` gate.
- **Identity resolution is unchanged:** volume label remains the primary TMDB query, and
  the DINFO name is still used as a TMDB *query* fallback only when the label query fails.
  No extra TMDB calls in the common case; no change to which show is matched.
- Pass `disc_title` / `disc_season` into `analyze()` as the corroboration/base signal.
  Continue to propagate `disc_season` when the volume label yielded no season.

### 5. Poster endpoint uses `tmdb_id` (`app/api/routes.py`)

In `get_job_poster`: when `job.tmdb_id` is set, fetch the canonical record directly —
`GET /3/tv/{tmdb_id}` or `/3/movie/{tmdb_id}` — and read `poster_path`. Fall back to the
existing name search only when `tmdb_id` is absent (unidentified discs still get a
best-effort poster). Reuse the function's existing `_build_auth` + `BASE_IMAGE_URL`;
mirror the `/tv/{id}` pattern in `tmdb_client.fetch_show_details`. Poster URLs continue to
be built from the allowlisted `image.tmdb.org` base.

## Why this cannot regress to the breakingbad bug

- **DINFO present:** `disc_title="Breaking Bad"` corroborates `tmdb_name="Breaking Bad"`
  → adopted.
- **DINFO absent:** the whitespace-insensitive label check
  (`breakingbad` ≡ `breaking bad`) corroborates `tmdb_name` → still adopted.

Two independent paths to the authoritative name.

## Testing (TDD — failing tests written first)

Unit tests under `backend/tests/unit/`:

1. **`_names_are_similar`**: `(Breakingbad, Breaking Bad)→True`,
   `(Strangenewworlds, Strange New Worlds)→True`, `(Breakingbad, Friends)→False`;
   existing cases stay green.
2. **`_parse_disc_name`**: the four table cases in change 2.
3. **`analyze()` regression**: `volume_label="BREAKINGBADS2"` +
   `tmdb_signal(tmdb_name="Breaking Bad", ambiguous_identity=False)` →
   `detected_name=="Breaking Bad"` and `detected_season==2`, tested **both** with a DINFO
   `disc_title="Breaking Bad"` and without one.
4. **Corroboration safety**: a spurious `tmdb_name` dissimilar to both the label and the
   DINFO title → base name kept (no silent override).
5. **Poster endpoint**: job with `tmdb_id=1396`, `content_type=tv`, garbled
   `detected_title` → issues a `/3/tv/1396` request (not `/search/tv`) and returns the
   poster URL, proving independence from `detected_title`.

Update the one existing test that asserts `name_hint` *bypasses* the guard
(`backend/tests/unit/test_disc_name_identification.py:109`) to the corroboration model.

## Out of scope (follow-ups)

- **Containment/substring corroboration** for "TMDB name has extra leading words" (e.g.
  label `BATMAN` → TMDB `Batman Begins`, or a DINFO-less `STRANGENEWWORLDS` →
  `Star Trek: Strange New Worlds`). Deferred (YAGNI); DINFO corroboration covers the
  common cases.
- **Identity-resolution restructuring** (making DINFO the primary identity query, or
  dual-query reconciliation). Explicitly not done — keeps blast radius small and does not
  change which show is matched.
- **Renaming already-organized files** (`X:\media\series\Breakingbad`). The user will
  re-rip; no migration of existing files.

## Files touched

- `backend/app/core/analyst.py` — changes 1, 2, 3
- `backend/app/services/identification_coordinator.py` — change 4
- `backend/app/api/routes.py` — change 5
- `backend/tests/unit/` — new/updated unit tests

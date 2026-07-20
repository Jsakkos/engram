# Fix Native Title-Number Mismatch (Issue #517) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix discs where MakeMKV's own `_tNN` filename numbering doesn't start at 0 (e.g. the disc has no "t00", numbering starts at "t01") — today this causes the first scanned title to never resolve to a ripped file (hangs, fails "couldn't find the file") and the last scanned title's content to be silently orphaned in staging and deleted on cleanup.

**Architecture:** Persist the disc-native `_tNN` number MakeMKV *suggests* for each title (already parsed at scan time into `TitleInfo.disc_title`, just never saved) onto a new `DiscTitle.output_index` column. Change the two places that currently *guess* the native number equals `DiscTitle.title_index` (`resolve_title_from_filename` and `find_staging_file`'s glob fallback) to use the recorded `output_index` first, falling back to the old `title_index`-based behavior for rows that predate this fix (so existing/legacy jobs are unaffected).

**Tech Stack:** Python 3.11, SQLModel/SQLAlchemy, Alembic (`batch_alter_table` for SQLite), pytest (async, `pytest.mark.unit`).

---

## Root cause (context for the engineer)

Confirmed from issue #517's log snippet: MakeMKV's scan reports title at **scan-order index 0** with suggested output filename `C1title_t01.mkv` — i.e. MakeMKV's own `_tNN` numbering starts at 1, not 0, on this disc set (there is no "t00" title at all).

Two places in the ripping pipeline wrongly assume "the number in the ripped filename's `_tNN` suffix IS `DiscTitle.title_index`":

- `app/services/ripping_helpers.py::resolve_title_from_filename` (matches a just-finished ripped file back to its `DiscTitle` row by parsing `_tNN` out of the filename and looking up `title_index == parsed_number`)
- `app/services/ripping_helpers.py::find_staging_file` (glob fallback `*_t{title.title_index:02d}.mkv`)

Both live in `app/core/extractor.py::title_index_from_filename`, which parses the number — that parser is fine, it's *what gets compared against it* that's wrong.

On an offset disc, every ripped file's parsed number is one higher than the true `title_index`: title 0's file is named `_t01.mkv` and gets mis-attributed to the DB row for `title_index == 1`; title 1's file (`_t02.mkv`) gets mis-attributed to `title_index == 2`; and so on. The DB row for `title_index == 0` never receives a matching file (nothing produces `_t00.mkv`) — it hangs, then fails. The very last title's file parses to a number one past the end of the title set, is treated as a "foreign file" (a deliberate protection added for single-track re-rip, see `test_resolve_foreign_filename_index_not_in_subset_returns_none`), is never claimed by any row, and gets deleted by staging cleanup.

Fix: stop assuming; record what MakeMKV actually told us at scan time (`TINFO` attribute 27, already captured into `TitleInfo.disc_title`, e.g. `"C1title_t01.mkv"`) and match against *that*.

**Out of scope (follow-up, not this plan):** `app/core/extractor.py::_files_to_ignore` has the same class of assumption, but it only fires when `rip_titles` is called with a real subset of titles — reached by two callers: the manual single-track re-rip path (`job_manager.rerip_titles`), and the automatic one-pass-stalled fallback that individually re-rips titles a failed 'all' pass left missing (`job_manager.py`'s `TestOnePassRipFallback`-covered logic). It has no DB access to look up `output_index`, so fixing it requires threading a title_index→output_index map through `rip_titles`/`_rip_titles_unlocked`. The risk is bounded in practice — a stale sibling file typically earns `TitleCompletionDetector`'s deletion-protection within `STABLE_CHECKS_REQUIRED` polls (~9s), well inside the default stall timeout, so the exposure is redundant reprocessing rather than data loss. Left as a follow-up so this plan stays focused on the reported bug.

---

## File Structure

- Modify: `backend/app/models/disc_job.py` — add `DiscTitle.output_index` column
- Create: `backend/migrations/versions/<hash>_add_disc_titles_output_index.py` — Alembic migration for the new column
- Modify: `backend/app/services/identification_coordinator.py` — persist `output_index` when creating `DiscTitle` rows from scan results
- Modify: `backend/app/services/ripping_helpers.py` — `resolve_title_from_filename` and `find_staging_file` prefer `output_index`
- Test: `backend/tests/unit/test_extractor.py` — pins the scan-time `output_index` expression
- Test: `backend/tests/unit/test_ripping_helpers.py` — resolution/glob preference over `output_index`, plus the exact issue-517 regression scenario

---

### Task 1: Add `output_index` column to `DiscTitle`

**Files:**
- Modify: `backend/app/models/disc_job.py:169` (add field near `title_index`)
- Create: `backend/migrations/versions/f1a2b3c4d5e6_add_disc_titles_output_index.py`
- Test: `backend/tests/unit/test_ripping_helpers.py` (new test class, see Step 1)

- [ ] **Step 1: Write a failing test asserting the model has the field with the right default**

Add to `backend/tests/unit/test_ripping_helpers.py` (near the top, after existing imports — no new imports needed, `DiscTitle` is already imported):

```python
@pytest.mark.unit
def test_disc_title_output_index_defaults_to_none():
    """New column: the disc-native _tNN number captured at scan time.

    Must default to None so legacy rows (created before this migration, or by
    call sites that never populate it) fall back to title_index-based matching.
    """
    t = DiscTitle(job_id=1, title_index=0, duration_seconds=100)
    assert t.output_index is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/unit/test_ripping_helpers.py::test_disc_title_output_index_defaults_to_none -v`
Expected: FAIL with `AttributeError: 'DiscTitle' object has no attribute 'output_index'` (or a Pydantic "unexpected keyword" style error — either confirms the field doesn't exist yet).

- [ ] **Step 3: Add the field to the model**

In `backend/app/models/disc_job.py`, in the `DiscTitle` class, right after the existing `title_index` line (currently line 169):

```python
    title_index: int  # MakeMKV title index (scan-order position, 0-based)
    output_index: int | None = None  # Disc-native "_tNN" number MakeMKV embeds in this
    # title's suggested output filename (TINFO attr 27), captured at scan time. Usually
    # equal to title_index, but not guaranteed — some discs number titles starting at 1
    # or with gaps (issue #517). None on rows created before this field existed, or
    # where MakeMKV supplied no suggested filename; those fall back to title_index.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_ripping_helpers.py::test_disc_title_output_index_defaults_to_none -v`
Expected: PASS

- [ ] **Step 5: Add the Alembic migration**

Find the current head revision first:

Run (from `backend/`): `uv run alembic heads`
Expected output: `33568e53d94d (head)` (use whatever the actual current head is if this has changed)

Create `backend/migrations/versions/f1a2b3c4d5e6_add_disc_titles_output_index.py`:

```python
"""add disc_titles.output_index

Records the disc-native "_tNN" number MakeMKV embeds in a title's suggested
output filename (TINFO attr 27), captured at scan time. Some discs number
titles starting at 1 (no "t00") or with gaps, so this can differ from
title_index (the 0-based scan-order position) — see issue #517. Mirrors the
database.py reconciler path used by frozen builds (which skip Alembic) — the
two must stay in agreement.

Revision ID: f1a2b3c4d5e6
Revises: 33568e53d94d
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "33568e53d94d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("output_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("output_index")
```

Confirm the actual head from Step 5's `alembic heads` output before writing `down_revision` — if it differs from `33568e53d94d`, use the real value.

- [ ] **Step 6: Verify migration applies cleanly**

Run (from `backend/`): `uv run alembic upgrade head`
Expected: No errors; final line mentions running the new revision `f1a2b3c4d5e6`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/disc_job.py backend/migrations/versions/f1a2b3c4d5e6_add_disc_titles_output_index.py backend/tests/unit/test_ripping_helpers.py
git commit -m "feat(db): add disc_titles.output_index for native title numbering (#517)"
```

---

### Task 2: Persist `output_index` at scan time

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:19` (import) and `:346-358` (the `for title in titles:` loop that builds `DiscTitle` rows from scan results, inside `identify_disc`)
- Test: `backend/tests/unit/test_extractor.py` (extend the existing `TestParseDiscInfo`-adjacent coverage with a test of the exact guarded expression used in the persistence loop)

`identify_disc` (`identification_coordinator.py:243`) is a large single-session orchestration method (scan → TMDB/DiscDB/AI classification → persist → broadcast) with no smaller persistence-only helper to call in isolation, and the existing `tests/unit/test_identification_coordinator.py` deliberately tests `_run_classification` and pure helpers rather than driving `identify_disc` end-to-end (see that file's module docstring) — mocking the full method here would mean stubbing TMDB config, the analyst, the broadcaster, the state machine, and disk-writing snapshot code, which is disproportionate to a one-expression change. Instead, pin the exact expression being added with a direct unit test, and rely on Task 3's `resolve_title_from_filename` tests (which seed `DiscTitle.output_index` directly, exactly as this loop will populate it) for behavioral coverage of the actual bug.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/test_extractor.py`, in the `TestParseDiscInfo` class (after `test_cinfo_other_than_attr_2_is_ignored`, using the class's existing `SAMPLE` fixture which already contains `TINFO:0,27,0,"Inception_t00.mkv"`):

```python
    def test_scan_time_output_index_expression(self):
        """Pins the exact expression identification_coordinator.py uses when
        building a DiscTitle from a scanned TitleInfo — output_index is the
        native _tNN number parsed from the suggested filename (disc_title),
        or None when MakeMKV supplied no suggested filename (issue #517: this
        is what lets resolve_title_from_filename match ripped files correctly
        even when that native number doesn't equal the scan-order index).
        """
        titles, _ = _extractor()._parse_disc_info(self.SAMPLE)
        t0 = {t.index: t for t in titles}[0]
        output_index = title_index_from_filename(t0.disc_title) if t0.disc_title else None
        assert output_index == 0  # this sample's disc_title is "Inception_t00.mkv"

        t1 = {t.index: t for t in titles}[1]  # has no TINFO:1,27 line -> disc_title == ""
        output_index_t1 = title_index_from_filename(t1.disc_title) if t1.disc_title else None
        assert output_index_t1 is None
```

Add `title_index_from_filename` to the existing `from app.core.extractor import (...)` import block at the top of `test_extractor.py` if it isn't already imported there (check first — line 28 already imports it per a prior grep, so this may be a no-op).

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/unit/test_extractor.py::TestParseDiscInfo::test_scan_time_output_index_expression -v`
Expected: PASS already for this specific assertion shape (it's testing a plain expression, not code inside `identification_coordinator.py` yet) — this step is a sanity check that `_parse_disc_info` really does leave `t1.disc_title == ""` (no attr 27 line for title 1 in `SAMPLE`) and that `title_index_from_filename("Inception_t00.mkv") == 0`. If it fails, the assumption about `SAMPLE`'s shape is wrong — re-read `TestParseDiscInfo.SAMPLE` and adjust the assertions to match reality before proceeding.

- [ ] **Step 3: Populate `output_index` in the scan-persistence loop**

In `backend/app/services/identification_coordinator.py`, add the import (line 19 currently reads `from app.core.extractor import MakeMKVExtractor, ScanTimeoutError`):

```python
from app.core.extractor import MakeMKVExtractor, ScanTimeoutError, title_index_from_filename
```

Then change the title-persistence loop (currently lines 346-358):

```python
                # Save title information
                for title in titles:
                    disc_title = DiscTitle(
                        job_id=job_id,
                        title_index=title.index,
                        output_index=title_index_from_filename(title.disc_title)
                        if title.disc_title
                        else None,
                        duration_seconds=title.duration_seconds,
                        file_size_bytes=title.size_bytes,
                        chapter_count=title.chapter_count,
                        video_resolution=title.video_resolution,
                        source_filename=title.source_filename or None,
                        segment_count=title.segment_count,
                        segment_map=title.segment_map or None,
                    )
                    session.add(disc_title)
```

- [ ] **Step 4: Run the full extractor and identification-coordinator unit suites to confirm no regressions**

Run (from `backend/`): `uv run pytest tests/unit/test_extractor.py tests/unit/test_identification_coordinator.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/identification_coordinator.py backend/tests/unit/test_extractor.py
git commit -m "fix: persist MakeMKV native title numbering at scan time (#517)"
```

---

### Task 3: Prefer `output_index` when resolving a ripped file to its title

**Files:**
- Modify: `backend/app/services/ripping_helpers.py:65-124` (`resolve_title_from_filename`)
- Test: `backend/tests/unit/test_ripping_helpers.py` (extend the existing `_seed` helper and add new tests)

- [ ] **Step 1: Extend the `_seed` test helper to accept per-title `output_index`**

In `backend/tests/unit/test_ripping_helpers.py`, replace the existing `_seed` helper (lines 55-82):

```python
async def _seed(
    indices: list[int], output_indices: list[int | None] | None = None
) -> tuple[int, list[DiscTitle]]:
    """Persist a job with titles at the given title_index values (detached).

    ``output_indices``, when given, must be the same length as ``indices`` and
    sets each title's ``output_index`` (the disc-native "_tNN" number). Defaults
    to all-None (legacy rows with no recorded native number).
    """
    if output_indices is None:
        output_indices = [None] * len(indices)
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S3D4",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        titles = []
        for idx, out_idx in zip(indices, output_indices, strict=True):
            t = DiscTitle(
                job_id=job.id,
                title_index=idx,
                output_index=out_idx,
                duration_seconds=2700,
                state=TitleState.RIPPING,
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            titles.append(t)
        for t in titles:
            session.expunge(t)
        return job.id, titles
```

- [ ] **Step 2: Write the failing regression test for issue #517's exact scenario**

Add to `backend/tests/unit/test_ripping_helpers.py`, after the existing `resolve_title_from_filename` tests:

```python
@pytest.mark.unit
async def test_resolve_by_output_index_when_native_numbering_offset():
    """Issue #517: disc has no "t00" — MakeMKV's native numbering starts at 1.

    Scan-order title_index=0 has output_index=1 (from its suggested filename
    "..._t01.mkv"). The ripped file for that title is literally named
    "..._t01.mkv". It must resolve to title_index=0, not to whatever row (if
    any) happens to have title_index==1.
    """
    job_id, titles = await _seed([0, 1], output_indices=[1, 2])
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(
            Path("C1title_t01.mkv"), titles, 1, job_id, session
        )
    assert t is not None
    assert t.title_index == 0


@pytest.mark.unit
async def test_resolve_falls_back_to_title_index_when_output_index_unset():
    """Legacy rows (output_index=None) keep the old title_index-based matching."""
    job_id, titles = await _seed([0, 1, 2, 3])  # output_index defaults to None
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("E1_t03.mkv"), titles, 4, job_id, session)
    assert t is not None
    assert t.title_index == 3
```

- [ ] **Step 3: Run the new test to verify it fails**

Run: `uv run pytest tests/unit/test_ripping_helpers.py::test_resolve_by_output_index_when_native_numbering_offset -v`
Expected: FAIL — with today's code, filename `C1title_t01.mkv` parses to `1`, matching the row with `title_index == 1` (the wrong title), so `t.title_index == 0` fails.

- [ ] **Step 4: Run the existing suite to confirm nothing else fails yet**

Run: `uv run pytest tests/unit/test_ripping_helpers.py -v`
Expected: the new "offset" test fails; all other existing tests (including `test_resolve_foreign_filename_index_not_in_subset_returns_none` and the new fallback test) still pass unchanged.

- [ ] **Step 5: Fix `resolve_title_from_filename`**

In `backend/app/services/ripping_helpers.py`, replace the matching block (currently lines 83-109):

```python
    # Try to extract the MakeMKV title index from the filename (e.g.
    # B1_t00.mkv -> 0). MakeMKV's _tNN suffix is its own disc-native title
    # number, which is NOT guaranteed to equal DiscTitle.title_index (the
    # 0-based scan-order position) — some discs number titles starting at 1
    # or with gaps (issue #517). Prefer the native number recorded at scan
    # time (DiscTitle.output_index); fall back to title_index for rows that
    # predate that field (output_index is None) so legacy behavior is unchanged.
    title_index = title_index_from_filename(path.name)

    if title_index is not None:
        for st in sorted_titles:
            expected = st.output_index if st.output_index is not None else st.title_index
            if expected == title_index:
                title = await session.get(DiscTitle, st.id)
                break
        if title:
            logger.debug(
                f"Mapped {safe_name} to native title number {title_index} "
                f"(Title DB id={title.id}, title_index={title.title_index}, Job {job_id})"
            )
        else:
            # The filename names a real title number that isn't among the titles
            # this rip produced — it's a foreign file (e.g. another title's
            # already-finished output sitting in the staging dir during a
            # single-title re-rip). Do NOT positionally fall back: that would
            # mis-attribute it onto the wrong (subset) title and stamp it with
            # the wrong filename. Treat it as unresolved.
            logger.debug(
                f"Ripped file {safe_name} has native title number {title_index} not in this "
                f"rip's title set — ignoring as foreign (Job {job_id})"
            )
            return None
```

Leave the rest of the function (the sequential `rip_index` fallback for unparseable filenames, and the final `if not title:` warning) unchanged.

- [ ] **Step 6: Run tests to verify all pass**

Run: `uv run pytest tests/unit/test_ripping_helpers.py -v`
Expected: All PASS, including `test_resolve_by_output_index_when_native_numbering_offset`, `test_resolve_falls_back_to_title_index_when_output_index_unset`, and the pre-existing `test_resolve_foreign_filename_index_not_in_subset_returns_none` / `test_resolve_by_filename_index` / `test_resolve_unparseable_filename_falls_back_positionally`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/ripping_helpers.py backend/tests/unit/test_ripping_helpers.py
git commit -m "fix: resolve ripped files by native title number, not scan index (#517)"
```

---

### Task 4: Prefer `output_index` in the staging-file glob fallback

**Files:**
- Modify: `backend/app/services/ripping_helpers.py:127-157` (`find_staging_file`)
- Test: `backend/tests/unit/test_ripping_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/test_ripping_helpers.py`:

```python
@pytest.mark.unit
def test_find_staging_file_glob_prefers_output_index(tmp_path):
    """Same offset scenario as Task 3, but for the glob-fallback path (used
    when output_filename is missing/stale, e.g. re-matching after a restart).
    """
    f = tmp_path / "C1title_t01.mkv"
    f.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path))
    title = SimpleNamespace(
        output_filename=None, title_index=0, output_index=1, organized_to=None
    )
    assert find_staging_file(job, title) == f


@pytest.mark.unit
def test_find_staging_file_glob_falls_back_to_title_index_when_output_index_unset(tmp_path):
    f = tmp_path / "Show_t07.mkv"
    f.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path))
    title = SimpleNamespace(
        output_filename=None, title_index=7, output_index=None, organized_to=None
    )
    assert find_staging_file(job, title) == f
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `uv run pytest tests/unit/test_ripping_helpers.py::test_find_staging_file_glob_prefers_output_index -v`
Expected: FAIL — today's glob is `*_t00.mkv` (from `title_index=0`), which doesn't match `C1title_t01.mkv`.

- [ ] **Step 3: Fix the glob to prefer `output_index`**

In `backend/app/services/ripping_helpers.py`, in `find_staging_file`, replace:

```python
    if job.staging_path:
        matches = list(Path(job.staging_path).glob(f"*_t{title.title_index:02d}.mkv"))
        if matches:
            return matches[0]
```

with:

```python
    if job.staging_path:
        output_index = getattr(title, "output_index", None)
        glob_index = output_index if output_index is not None else title.title_index
        matches = list(Path(job.staging_path).glob(f"*_t{glob_index:02d}.mkv"))
        if matches:
            return matches[0]
```

(`getattr` with a default keeps this safe against the `SimpleNamespace`-based tests in this file that predate the `output_index` attribute, and against any other lightweight stand-ins elsewhere in the codebase that model a title without every real column.)

- [ ] **Step 4: Run tests to verify both pass**

Run: `uv run pytest tests/unit/test_ripping_helpers.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ripping_helpers.py backend/tests/unit/test_ripping_helpers.py
git commit -m "fix: staging-file glob fallback also prefers native title number (#517)"
```

---

### Task 5: Full-suite verification and changelog entry

**Files:**
- Modify: `CHANGELOG.md` (repo root)

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `uv run pytest`
Expected: All tests PASS (no regressions in `tests/unit/`, `tests/integration/`, `tests/pipeline/`).

- [ ] **Step 2: Run ruff**

Run (from `backend/`): `uv run ruff check .`
Expected: No new violations. If any appear in touched files, fix them.

- [ ] **Step 3: Confirm the migration round-trips**

Run (from `backend/`): `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: Both commands succeed with no errors — confirms `downgrade()` in the new migration is correct.

- [ ] **Step 4: Add a changelog entry**

In the repo-root `CHANGELOG.md`, add to the `[Unreleased]` section's `### Fixed` subsection (create it if absent):

```markdown
### Fixed

- Fixed titles being lost or mis-organized on discs where MakeMKV's native title numbering doesn't start at "t00" (e.g. some Blu-ray sets start at "t01") — the first title would hang and fail after ripping, and the last title's rip would be silently deleted from staging (#517).
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add changelog entry for native title numbering fix (#517)"
```

---

## Follow-up (not in this plan)

File a separate, smaller issue for `app/core/extractor.py::_files_to_ignore` (used only by the single-track re-rip path in `_rip_titles_unlocked`): it has the same "filename number == title_index" assumption but no DB access to consult `output_index`. Fixing it requires the caller (`job_manager.py::rerip_titles` / wherever `rip_titles(title_indices=...)` is invoked for a subset) to pass in a translated set of expected native numbers (built from the DB rows' `output_index`/`title_index`) instead of raw `title_index` values. Only matters when re-ripping a single track on a disc with this exact native-numbering quirk — narrow enough to defer.

---

## Addendum (2026-07-19): Tasks 6-9, found by final holistic review

The final full-diff code review (after Task 5) found that Tasks 3-4 fixed only 2 of ~6 places in the codebase that assume a ripped filename's `_tNN` number equals `DiscTitle.title_index`. Verified independently by reading the code (not just trusting the review):

- **`job_manager.py::_find_title_file`** (line 1131), reached via **`reconcile_stuck_titles`** (called unconditionally after every rip, at lines 907, 2567, 2588, 2624) — this function's own docstring says it exists to guarantee "no selected title is stranded in RIPPING once the MakeMKV subprocess has exited (**the orphaned-last-title bug**)". This is likely the *actual* mechanism behind issue #517's reported symptom (the last title never resolving) more than the happy-path callback fixed in Task 3 — MakeMKV's completion detector structurally can't prove the *last* file is done via the "another file started growing" signal, so this reconciliation pass is how the final title normally gets resolved. It still globs raw `title_index` — unfixed.
- **`finalization_coordinator.py::_resolve_source_file`** (line 1069) — organize-time fallback glob, also raw `title_index`. Its `to_organize` capture (line 1030) doesn't even carry `output_index` yet.
- **`job_manager.py::_has_complete_output`** (line 1112) and **`_filesystem_progress_monitor`** (line 2312, glob at 2325) — same pattern, lower severity (redundant re-rip / progress-UI staleness rather than data loss).
- **`job_manager.py::_backfill_unmatched_titles`** (line 3202) — reimplements filename parsing inline instead of reusing `title_index_from_filename`; its own dedup check (`assigned_indices`) still keys off raw `title_index`, causing redundant reprocessing (not data loss, since the reprocess call goes through the now-fixed `resolve_title_from_filename`).
- **`extractor.py::title_index_from_filename`** docstring (line 67) is now stale — it asserts the parsed number "is... the same number stored as `DiscTitle.title_index`", which Tasks 3-4 disproved.

Given ~6 near-duplicate sites now confirmed, consolidate into one shared helper rather than patching each site with its own inline ternary.

### Task 6: Extract shared helper + fix `job_manager.py`'s three remaining sites

**Files:**
- Modify: `backend/app/services/ripping_helpers.py` — add `expected_native_index(title) -> int` helper; refactor `resolve_title_from_filename` and `find_staging_file` to use it (pure refactor — behavior must not change, existing tests must stay green as-is)
- Modify: `backend/app/services/job_manager.py` — fix `_find_title_file` (line ~1131), `_has_complete_output` (line ~1112, change signature from `title_index: int` to take the title object), `_filesystem_progress_monitor`'s glob (line ~2325)
- Test: `backend/tests/unit/test_ripping_helpers.py` (test the new helper directly), `backend/tests/unit/test_job_manager.py` or equivalent (test `_find_title_file` / `_has_complete_output` prefer `output_index`)

```python
def expected_native_index(title) -> int:
    """The disc-native "_tNN" number expected in this title's ripped filename.

    Prefers ``output_index`` (MakeMKV's actual native number, captured at scan
    time); falls back to ``title_index`` for rows/stand-ins without it. Takes
    a duck-typed object (not strictly ``DiscTitle``) via ``getattr`` so test
    doubles (``SimpleNamespace``) that don't define ``output_index`` still work.
    """
    output_index = getattr(title, "output_index", None)
    return output_index if output_index is not None else title.title_index
```

`_has_complete_output`'s only call site (job_manager.py:2482) already has a real `DiscTitle` in scope (`t`, from `sorted_titles` — no need to use the freshly-fetched `db_t`, since `output_index` doesn't change after scan) — change the call from `self._has_complete_output(output_dir, t.title_index)` to `self._has_complete_output(output_dir, t)` and update the method's glob to use `expected_native_index(title)`.

### Task 7: Fix `finalization_coordinator.py::_resolve_source_file`

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py` — add `"output_index": t.output_index` to the `to_organize` dict comprehension (line ~1030-1041, the only one in this file — verified via grep, mirroring the pattern already established for `title_index`/`output_filename`), update `_resolve_source_file` (line ~1069) and its one call site (line ~1085) to accept and prefer it
- Test: appropriate existing finalization/organize test file (check `backend/tests/unit/` for existing `_resolve_source_file` or organize-path coverage first and extend it, following that file's established mocking conventions)

### Task 8: Fix `job_manager.py::_backfill_unmatched_titles`

**Files:**
- Modify: `backend/app/services/job_manager.py` (line ~3202) — replace the inline `re.search` filename parsing with the existing `title_index_from_filename` (already imported/used elsewhere in this file's import block — verify), and build `assigned_native = {expected_native_index(t) for t in titles if t.output_filename is not None}` instead of `assigned_indices = {t.title_index for t in titles if t.output_filename is not None}`
- Test: extend existing backfill coverage if present (check `test_job_manager.py`), else add a focused unit test

### Task 9: Docstring fix + full re-verification + changelog check

**Files:**
- Modify: `backend/app/core/extractor.py:67-76` — update `title_index_from_filename`'s docstring; it currently claims the parsed number "is... the same number stored as `DiscTitle.title_index`" and that this makes `resolve_title_from_filename` unable to disagree with the completion detector's ignore-list — both now false. Rewrite to describe it as parsing MakeMKV's disc-native title number, which callers should prefer resolving against `DiscTitle.output_index` (falling back to `title_index`), and note `_files_to_ignore` is a known, documented exception (see the Follow-up section above).
- Run: full backend suite (`uv run pytest`), ruff, migration round-trip — same as Task 5.
- Re-check: does the CHANGELOG.md entry from Task 5 still accurately describe the fix now that Tasks 6-8 close the "last title" gap? If Task 5's entry already reads correctly as a user-facing description (it should, since it describes the symptom not the mechanism), no changelog edit is needed — confirm rather than assume.

### Final step: re-run the whole-diff holistic review

After Tasks 6-9 land, dispatch one more final-review pass (same as after Task 5) covering the full `5ba4ea1a..HEAD` diff, specifically re-checking finding #1-#6 from the prior holistic review are now resolved, and looking for any *further* duplicate sites that might still remain (e.g. anywhere else in the codebase matching `_t{.*title_index.*:02d}` or similar glob patterns).

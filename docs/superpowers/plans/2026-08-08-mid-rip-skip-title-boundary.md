# Mid-Rip Track Skip: Abort at Title Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skipping a not-yet-ripped track must never interrupt the track MakeMKV is currently writing, must never leave the dashboard without progress, and must never eject a disc with unripped wanted tracks.

**Architecture:** A full-disc `mkv … all` pass is one MakeMKV invocation, so it cannot drop an individual title. Issue #539 honored a mid-rip skip by killing that process on the *next stdout line*, which destroyed the title in flight. This plan moves the abort to a **title boundary** (the moment MakeMKV creates the next output file), and fires it only when the title just opened *and every title after it* are user-skipped. In that one case stopping costs nothing: the previous title finished, the new file is still a stub, and no wanted title remains. Every other skip is honored by letting the pass run and deleting the skipped title's file on completion, which `_on_title_ripped` already does. Two independent defects uncovered by the same investigation are fixed alongside: the per-title fallback rip ran with no progress monitor (and therefore no watchdog heartbeat), and `skip_rip_title` accepted `QUEUED` titles that were already fully ripped to disk.

**Tech Stack:** Python 3.11+ / FastAPI / SQLModel / pytest (backend), React 18 + TypeScript / vitest + Playwright (frontend), `uv` for all Python commands.

---

## Background: the three symptoms and their causes

From the bug report (0.28.2, Docker, Ubuntu 24.04):

| Symptom | Cause | Fixed by |
|---|---|---|
| "clicking skip on future tracks stopped ripping the current track" | `extractor.py:947` checks the skip-set on **every stdout line** and terminates MakeMKV mid-title; `:1010-1035` then deletes the partial file | Task 2 |
| "frozen-ish… stops making progress" | `job_manager.py:2669` cancels the filesystem progress monitor **before** the per-title fallback at `:2754`. That monitor is the sole `_note_activity` heartbeat during `RIPPING`, so the stale-job watchdog cancels a healthy recovery rip after `timeout_ripping_seconds` (default 1200s) | Task 5 |
| "completes 'successfully' and ejects" | `files_incomplete_at_abort` preserves the in-flight file whenever it did not grow between the last 3s poll and the kill (`size == prev > 0`). `_has_complete_output` then sees a non-empty `*_tNN.mkv`, the title drops out of `missing`, and with everything else skipped `missing` is empty → no fallback → eject | Task 2 (the boundary abort makes the stub provably 0-byte, so the heuristic is correct there) |

Two contributing defects, also fixed:

- `RipResult.aborted_for_skip` is set but **never read by any caller** (grep: `extractor.py` and its own test only). Recovery is entirely implicit. Fixed by Task 4.
- `skip_rip_title` accepts `TitleState.QUEUED` (`job_manager.py:1533`) and `TrackGrid.tsx:241` renders SKIP for `queued`. `QUEUED` means "ripped, on disk, waiting for a matching slot" (`disc_job.py:39`), so the UI lets a user silently discard a successful rip. Fixed by Task 6.

---

## CORRECTION (2026-08-08, during execution of Task 2)

Code review of the first Task 2 attempt found two Critical defects in the mechanism this plan
originally specified. Both were independently confirmed by measurement, so the boundary
DETECTION method below is superseded. The approach (stop only at a title boundary, only when
nothing wanted remains) is unchanged.

**1. The chosen boundary signal does not exist in real MakeMKV output.** The plan keyed the
boundary off `_extract_created_mkv`, which requires the literal substring `created` plus a
quoted `.mkv` path. Across the 187 real `~/.engram/logs/makemkv/*/rip.log` files on this
machine, **zero** contain `created`; only 4 mention `.mkv` at all, and those are
`MSG:5003 "Failed to save title ..."` errors. The one log with an `MSG:5014,0,1,"file
TEST_t01.mkv"` line is a synthetic fixture, and it lacks `created` too. So
`_extract_created_mkv` effectively never fires in production and cannot drive the abort.

(This does not contradict the diagnosis of the reported bug: the #539 abort it replaces keyed
off `self._skipped_indices.get(job_id)` on every stdout line, which does fire.)

What MakeMKV 1.18.x actually emits per title is a `PRGC:5057,<n>` / `PRGC:5017,<n>` pair whose
second field is a per-title ordinal (0..N-1, one pair per saved title). That ordinal indexes
MakeMKV's own save order, not `DiscTitle.title_index`, so it is not directly usable as an
identity either.

**2. At a boundary, `files_incomplete_at_abort` deletes the title that just FINISHED.** It
infers "was still growing" from the last polled size, and the completion poll runs on a 3s
cadence. A boundary is precisely the moment that reading is stale, so the finished title reads
as `size > prev` and is condemned. Verified by execution:

```
poll({t01: 900}); poll({t01: 1000}); seed(t02)          # boundary, _known stale
files_incomplete_at_abort({t01: 1050, t02: 0}) -> ['t01.mkv', 't02.mkv']   # WRONG
poll({t01: 900}); poll({t01: 1050, t02: 0})              # poll AT the boundary
files_incomplete_at_abort({t01: 1050, t02: 0}) -> ['t02.mkv']              # correct
```

That is a milder form of the very symptom this work exists to fix.

**Corrected design: detect the boundary from the FILESYSTEM, evaluated immediately after a
completion poll.** A new `*_tNN.mkv` appearing in the staging dir is the boundary, and it is a
version- and locale-independent signal that the extractor already gathers. Evaluating right
after `_check_for_completed_files()` makes `_known` current by construction, which fixes defect
2 for free.

Two consequential follow-ons:

- `should_abort_all_pass` drops its `seen_native` parameter and computes `remaining` as
  `{scan for native, scan in disc_title_map.items() if native > opened_native}`. MakeMKV saves
  titles in ascending native order (the module already relies on sequential writing). Ordering
  is also more robust than set membership here: `makemkvcon mkv <disc> all` applies MakeMKV's
  own selection profile, so a short title the scan saw may never be written at all. Under the
  membership rule such a title stays in `remaining` forever and the abort can never fire; under
  the ordering rule only filtered titles *above* the current one block it, and blocking is
  fail-safe (the rip simply runs to the end).
- The reader loop's hardcoded `3.0` poll cadence becomes a module constant `FS_POLL_INTERVAL`
  so tests can shorten it, mirroring the existing `STALL_POLL_INTERVAL`.

Tasks 1 and 2 below are superseded by this correction. Tasks 3-9 are unaffected except that
Task 3 still supplies `disc_title_map` exactly as written.

---

## File Structure

**Backend (modify):**
- `backend/app/core/extractor.py`: add `should_abort_all_pass()` (pure, testable); thread a `disc_title_map` parameter through `rip_titles` / `_rip_titles_unlocked`; move the abort from the stdout loop into the file-created branch.
- `backend/app/services/job_manager.py`: pass `disc_title_map` for the all-pass; log `aborted_for_skip` explicitly; keep a progress monitor alive across the fallback rip; restrict `skip_rip_title` to `PENDING`.
- `backend/app/services/simulation_service.py`: make the simulated rip loop honor `SKIPPED`, so the E2E asserts real behavior instead of UI state alone.

**Backend (tests):**
- `backend/tests/unit/test_extractor_skip.py`: modify (the three `TestAllPassSkipAbort` cases encode the old per-line semantics).
- `backend/tests/unit/test_skip_rip_title.py`: modify (add the `QUEUED` rejection case).
- `backend/tests/unit/test_rip_fallback_heartbeat.py`: create.

**Frontend (modify):**
- `frontend/src/app/components/TrackGrid.tsx`: SKIP only for `pending`.
- `frontend/src/app/components/TrackGrid.test.tsx`: add skip-affordance cases.
- `frontend/e2e/track-skipping.spec.ts`: add the regression scenario.

**Docs:**
- `CHANGELOG.md`: `[Unreleased]` entry.

---

### Task 1: The abort decision as a pure function

The whole fix hinges on one predicate. Isolating it makes the rule testable without a fake MakeMKV process.

**Files:**
- Modify: `backend/app/core/extractor.py`
- Test: `backend/tests/unit/test_extractor_skip.py`

- [ ] **Step 1: Write the failing tests**

Add `should_abort_all_pass` to the existing `from app.core.extractor import (...)` list at the top of `backend/tests/unit/test_extractor_skip.py`, then insert this class **immediately before** `class TestAllPassSkipAbort` (currently line 132). Placement matters: Task 2 replaces that class wholesale, and appending here instead would put this class inside the replaced range.

```python
@pytest.mark.unit
class TestShouldAbortAllPass:
    """A full-disc 'all' pass may only be interrupted when stopping costs
    nothing: MakeMKV just opened a title the user skipped, and every title it
    has not opened yet is skipped too."""

    def test_no_abort_when_the_opened_title_is_wanted(self):
        # native -> scan index. User skipped scan index 3; MakeMKV opened 2.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {3}) is False

    def test_no_abort_when_a_wanted_title_still_lies_ahead(self):
        # The opened title IS skipped, but title 3 is still wanted. Killing the
        # process here would cost a full disc re-open to rip title 3 alone, and
        # would throw away whatever title 2 had written.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {2}) is False

    def test_aborts_when_the_opened_title_and_every_later_one_are_skipped(self):
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {2, 3}) is True

    def test_never_aborts_without_a_disc_title_map(self):
        # No map means the caller could not tell us what is on the disc, so we
        # cannot know whether a wanted title lies ahead. Fail safe: keep ripping.
        assert should_abort_all_pass(2, None, {2}, {2}) is False
        assert should_abort_all_pass(2, {}, {2}, {2}) is False

    def test_maps_native_numbering_to_the_scan_index_the_skip_set_uses(self):
        # Issue #517: MakeMKV's native title numbers can diverge from scan
        # order. The skip set is keyed on DiscTitle.title_index (scan order),
        # while the opened title is parsed from the output FILENAME (native).
        m = {0: 1, 1: 2, 2: 3}  # native -> scan
        # Skipped scan indices {2, 3}; MakeMKV opened native 1 (== scan 2).
        assert should_abort_all_pass(1, m, {0, 1}, {2, 3}) is True
        # Same disc, only scan 2 skipped -> scan 3 is still wanted.
        assert should_abort_all_pass(1, m, {0, 1}, {2}) is False

    def test_unknown_native_number_never_aborts(self):
        # A filename whose _tNN does not correspond to any scanned title.
        m = {1: 1, 2: 2}
        assert should_abort_all_pass(97, m, {1, 97}, {1, 2}) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_extractor_skip.py::TestShouldAbortAllPass -v
```

Expected: collection error with `ImportError: cannot import name 'should_abort_all_pass' from 'app.core.extractor'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/extractor.py`, insert immediately after `_build_rip_commands` (which ends around line 190):

```python
def should_abort_all_pass(
    opened_native: int,
    disc_title_map: dict[int, int] | None,
    seen_native: set[int],
    skipped_indices: set[int],
) -> bool:
    """Whether a full-disc 'all' pass should stop at the title just opened.

    True only when BOTH hold:

    * the title MakeMKV just opened is user-skipped, and
    * every title it has not opened yet is user-skipped too.

    That is exactly the case where stopping costs nothing: the previous title
    finished, the file just created is still a stub, and no wanted title
    remains on the disc. Any other skip is honored by letting the pass run to
    the end and deleting the skipped title's file once MakeMKV finishes it
    (``job_manager._on_title_ripped``). Killing the process mid-title instead
    threw away the rip in flight — which is why skipping a *later* track used
    to stop the *current* one.

    ``opened_native`` is MakeMKV's disc-native title number, parsed from the
    output filename. ``disc_title_map`` maps that number to the scan-order
    ``DiscTitle.title_index`` the skip set is keyed on; the two diverge on some
    discs (issue #517). ``seen_native`` is every native number opened so far,
    including ``opened_native``. An absent or empty map means the caller could
    not tell us what is on the disc, so we never abort.
    """
    if not disc_title_map:
        return False
    opened = disc_title_map.get(opened_native)
    if opened is None or opened not in skipped_indices:
        return False
    remaining = {scan for native, scan in disc_title_map.items() if native not in seen_native}
    return remaining <= skipped_indices
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_extractor_skip.py::TestShouldAbortAllPass -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_skip.py
git commit -m "refactor(extractor): extract the all-pass skip-abort decision as a pure predicate"
```

---

### Task 2: Move the abort to the title boundary

**Files:**
- Modify: `backend/app/core/extractor.py:632-646` (`rip_titles` signature), `:682-696` (`_rip_titles_unlocked` signature), `:744` (state declarations), `:939-955` (delete), `:986-995` (extend)
- Test: `backend/tests/unit/test_extractor_skip.py`

- [ ] **Step 1: Replace the three `TestAllPassSkipAbort` tests**

The existing tests in `TestAllPassSkipAbort` assert the old per-line semantics, they pass a skip-set with no `disc_title_map` and expect an abort. Replace that class in its entirety (from its `@pytest.mark.unit` decorator through the end of `test_per_title_pass_is_not_aborted_by_skip_set`) with:

```python
@pytest.mark.unit
class TestAllPassSkipAbort:
    """A full-disc 'all' pass is honored at TITLE BOUNDARIES only: MakeMKV
    announces a newly created output file, and the pass stops there when that
    title and every later one are skipped. A skip that lands mid-title must
    never interrupt the title being written."""

    @staticmethod
    def _created(name: str) -> str:
        """A MakeMKV robot-mode line announcing that it opened *name*."""
        return f'MSG:5014,0,1,"file created \'{name}\'"'

    async def test_does_not_abort_mid_title_when_a_later_track_is_skipped(self, tmp_path):
        # The exact user-reported case: title 1 is being written, the user skips
        # title 3. The pass MUST run on -- title 1's rip is not thrown away.
        procs: list[_FakeProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc(
                [
                    self._created("TEST_t01.mkv"),
                    "PRGV:1,2,65536",
                    "PRGV:3,4,65536",
                    "PRGV:5,6,65536",
                ]
            )
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(21, 3)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,  # full-disc 'all' pass
                stall_timeout=0,
                job_id=21,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is False
        assert procs and procs[0].terminated is False

    async def test_aborts_at_the_boundary_when_every_remaining_title_is_skipped(self, tmp_path):
        # Title 1 finished; MakeMKV opens title 2. Both 2 and 3 are skipped, so
        # there is no work left and stopping costs nothing.
        procs: list[_FakeProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc(
                [
                    self._created("TEST_t01.mkv"),
                    "PRGV:1,2,65536",
                    self._created("TEST_t02.mkv"),
                    "PRGV:3,4,65536",
                ]
            )
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(22, 2)
        ex.skip_title_index(22, 3)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=22,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is True
        assert result.success is True
        assert procs and procs[0].terminated is True

    async def test_boundary_abort_deletes_the_stub_it_stopped_on(self, tmp_path):
        # The just-opened stub must never reach matching. This is the
        # "completes successfully and ejects with a truncated file" symptom: at
        # a title boundary the stub is provably 0-byte, so
        # files_incomplete_at_abort classifies it correctly -- unlike a mid-title
        # kill, where a file that happened not to grow since the last 3s poll was
        # preserved and handed on as if finished.
        #
        # This test deliberately asserts ONLY the stub deletion. Whether an
        # already-finished title survives depends on the 3s completion poll
        # having run, which a sub-second fake process never reaches; that
        # preservation contract is covered directly, and thoroughly, by
        # TestFilesIncompleteAtAbort above.
        def _fake_popen(cmd, **kwargs):
            return _FakeProc(
                [
                    self._created("TEST_t01.mkv"),
                    "PRGV:1,2,65536",
                    self._created("TEST_t02.mkv"),
                ]
            )

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(23, 2)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            # The stub appears on disk the moment MakeMKV opens it.
            (tmp_path / "TEST_t02.mkv").write_bytes(b"")
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=23,
                disc_title_map={1: 1, 2: 2},
            )

        assert result.aborted_for_skip is True
        assert not (tmp_path / "TEST_t02.mkv").exists()

    async def test_no_abort_without_a_disc_title_map(self, tmp_path):
        # Fail-safe: an all-pass whose caller did not supply the disc contents
        # can never conclude that nothing is left, so it always runs to the end.
        procs: list[_FakeProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc([self._created("TEST_t02.mkv"), "PRGV:1,2,65536"])
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(24, 2)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0", tmp_path, title_indices=None, stall_timeout=0, job_id=24
            )

        assert result.aborted_for_skip is False
        assert procs and procs[0].terminated is False

    async def test_per_title_pass_is_not_aborted_by_skip_set(self, tmp_path):
        # In per-title mode the loop already drops skipped indices command-by-
        # command, so it must NOT take the all-pass abort path.
        def _fake_popen(cmd, **kwargs):
            return _FakeProc(["PRGV:1,2,65536"])

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(13, 4)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[4, 6],  # per-title mode
                stall_timeout=0,
                job_id=13,
            )

        assert result.aborted_for_skip is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_extractor_skip.py::TestAllPassSkipAbort -v
```

Expected: FAIL. `test_does_not_abort_mid_title_when_a_later_track_is_skipped` errors with `TypeError: rip_titles() got an unexpected keyword argument 'disc_title_map'`.

- [ ] **Step 3: Add the `disc_title_map` parameter to both signatures**

In `backend/app/core/extractor.py`, `rip_titles`, add the keyword-only parameter and pass it through:

```python
    async def rip_titles(
        self,
        drive: str,
        output_dir: Path,
        title_indices: list[int] | None = None,
        title_complete_callback: TitleCompleteCallback | None = None,
        stall_timeout: float | None = None,
        title_error_callback: TitleErrorCallback | None = None,
        log_dir: Path | None = None,
        *,
        job_id: int = 0,
        disc_title_map: dict[int, int] | None = None,
    ) -> RipResult:
        """Rip selected titles from a disc.

        Args:
            drive: Drive letter or disc specification
            output_dir: Directory to save MKV files
            title_indices: List of title indices to rip, or None for all
            title_complete_callback: Optional callback when a title finishes ripping
            stall_timeout: Seconds of no file growth before killing the process
                and skipping to the next title. None or 0 disables detection.
            title_error_callback: Optional callback when a title fails (e.g., stall
                detected). Called with (command_idx, error_reason).
            log_dir: Optional directory for saving MakeMKV rip logs
            disc_title_map: ``{native title number: DiscTitle.title_index}`` for
                every title a full-disc 'all' pass will produce. Used only in
                all-pass mode, to decide at a title boundary whether the user's
                skips have left no wanted title on the disc (see
                ``should_abort_all_pass``). Ignored when ``title_indices`` is
                given — a per-title pass drops skipped indices command-by-command.

        Returns:
            RipResult with success status and output files
        """
        lock = self._get_drive_lock(drive)
        if lock.locked():
            logger.warning(
                f"Drive {drive} is already in use by another MakeMKV operation, "
                f"waiting for it to finish"
            )

        async with lock:
            return await self._rip_titles_unlocked(
                drive,
                output_dir,
                title_indices,
                title_complete_callback,
                stall_timeout=stall_timeout,
                title_error_callback=title_error_callback,
                log_dir=log_dir,
                job_id=job_id,
                disc_title_map=disc_title_map,
            )
```

And on `_rip_titles_unlocked`, add the same keyword-only parameter after `job_id`:

```python
    async def _rip_titles_unlocked(
        self,
        drive: str,
        output_dir: Path,
        title_indices: list[int] | None = None,
        title_complete_callback: TitleCompleteCallback | None = None,
        stall_timeout: float | None = None,
        title_error_callback: TitleErrorCallback | None = None,
        log_dir: Path | None = None,
        *,
        job_id: int = 0,
        disc_title_map: dict[int, int] | None = None,
    ) -> RipResult:
```

- [ ] **Step 4: Declare the boundary-tracking state**

In `_rip_titles_unlocked`, replace the `aborted_for_skip` declaration block (currently at `extractor.py:740-744`) with:

```python
        # Set by the reader loop when a full-disc "all" pass is stopped at a title
        # boundary because every remaining title is user-skipped. The RipResult
        # returns below read it, so — like region_mismatch — it lives out here
        # rather than inside run_rip_with_streaming.
        aborted_for_skip = [False]

        # Native title numbers MakeMKV has opened so far, parsed from the
        # "file created" lines. Feeds should_abort_all_pass, which needs to know
        # which titles are still ahead of the one being written.
        seen_native: set[int] = set()
```

- [ ] **Step 5: Delete the per-line abort**

In `run_rip_with_streaming`'s stdout loop, delete this entire block (currently `extractor.py:939-955`), the lines from the `# All-pass skip (issue #538)` comment through the `break`:

```python
                        # All-pass skip (issue #538): a single "mkv … all"
                        # invocation cannot be told to drop one title, so a skip
                        # requested mid-pass is honored by aborting here. The
                        # titles finished before the abort are kept; the caller
                        # re-rips the remaining not-skipped titles per-title,
                        # where the live skip-set IS consulted. (Per-title
                        # commands carry a real title_index and skip themselves
                        # before starting, so this only fires for the all-pass.)
                        if title_index is None and self._skipped_indices.get(job_id):
                            logger.info(
                                f"Skip requested during full-disc 'all' pass "
                                f"(job {job_id}); aborting to re-rip the remaining "
                                f"titles individually"
                            )
                            aborted_for_skip[0] = True
                            process.terminate()
                            break
```

The loop must now go straight from the `if job_id in self._cancelled_jobs:` block to `line = line.strip()`.

- [ ] **Step 6: Add the boundary check to the file-created branch**

Replace the `_extract_created_mkv` block (currently `extractor.py:985-995`) with:

```python
                        # Catch robot-mode MSG lines about file creation
                        filepath = _extract_created_mkv(line, output_dir)
                        if filepath is not None:
                            # Track the file for stable-size detection but do NOT
                            # fire title_complete_callback — the file was just created,
                            # not finished writing. Let _check_for_completed_files
                            # detect true completion via stable file size.
                            opened_native: int | None = None
                            with _fs_lock:
                                if not completion.is_known(filepath.name):
                                    completion.seed(filepath.name)
                                    logger.info(f"MakeMKV created output file: {filepath.name}")
                                    opened_native = title_index_from_filename(filepath.name)

                            # Title boundary — the ONLY safe point to honor a skip
                            # during a full-disc 'all' pass. The previous title has
                            # finished writing and this one is still a stub, so
                            # terminating here destroys nothing. The original #539
                            # fix checked the skip-set on every stdout line, which
                            # killed the title being written when the user skipped a
                            # *later* one.
                            if opened_native is not None:
                                seen_native.add(opened_native)
                                if title_index is None and should_abort_all_pass(
                                    opened_native,
                                    disc_title_map,
                                    seen_native,
                                    self._skipped_indices.get(job_id, set()),
                                ):
                                    logger.info(
                                        f"Job {job_id}: every remaining title is "
                                        f"user-skipped; stopping the full-disc pass "
                                        f"at native title {opened_native}"
                                    )
                                    aborted_for_skip[0] = True
                                    process.terminate()
                                    break
```

- [ ] **Step 7: Update the `files_incomplete_at_abort` docstring**

Its rationale changes: the abort is now always at a boundary, so the "file being written" it deletes is the freshly-opened stub. In `backend/app/core/extractor.py`, replace the first paragraph of `TitleCompletionDetector.files_incomplete_at_abort`'s docstring (currently `:332-337`, ending "...at most one file is partial: the one whose size **grew since the last poll**") with:

```python
        """Files to delete when the rip process was killed mid-pass.

        Used on an abort — a stall, or a skip that stopped a full-disc "all"
        pass at a title boundary. The ``force`` promotion path is unsafe here
        because the process did not exit naturally, so any file MakeMKV still
        had open is truncated. MakeMKV writes titles sequentially, so at a kill
        at most one file is partial: the one whose size **grew since the last
        poll** (or a zero-byte stub, or one never polled at all). At a title
        boundary that is the stub just created, which is exactly right. A title
        that already finished has a size equal to its last-polled value — even
        if its "a later title started growing" completion gate never fired
        because it was the most recent title when the kill landed — and MUST be
        preserved rather than needlessly re-ripped. ``_completed`` and
        pre-existing (``_ignored``) files are always preserved.
```

Leave the rest of the docstring (the `_active` paragraph) and the body unchanged.

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_extractor_skip.py tests/unit/test_extractor.py tests/unit/test_extractor_callbacks.py -v
```

Expected: all pass. If `test_extractor_callbacks.py` fails, the file-created branch was mis-edited, `completion.seed` must still be called exactly once per new filename.

- [ ] **Step 9: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_skip.py
git commit -m "fix(extractor): honor a mid-rip skip at title boundaries, not mid-title

Skipping a not-yet-ripped track terminated makemkvcon on the next stdout
line, discarding the title actually being written. The pass now stops only
when MakeMKV opens a skipped title AND every later title is skipped too --
the one case where stopping costs nothing."
```

---

### Task 3: Tell the extractor what is on the disc

The boundary check is inert until the caller supplies `disc_title_map`. Without this task the abort never fires and skipping a trailing track no longer saves any time, the behavior a previous round of user feedback rejected.

**Files:**
- Modify: `backend/app/services/job_manager.py:2659-2668`
- Test: `backend/tests/unit/test_rip_disc_title_map.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_rip_disc_title_map.py`:

```python
"""_run_ripping must hand the extractor the disc's native->scan title map.

Without it should_abort_all_pass can never conclude that nothing is left to
rip, so a user who skips every trailing track saves no time at all.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

import app.database as _db
from app.core.extractor import RipResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


class _RecordingExtractor:
    """Captures rip_titles kwargs; rips nothing."""

    def __init__(self):
        self.kwargs: list[dict] = []

    def skip_title_index(self, job_id, idx):
        pass

    def unskip_title_index(self, job_id, idx):
        pass

    async def rip_titles(self, drive, output_dir, **kw):
        self.kwargs.append(kw)
        return RipResult(success=True, output_files=[Path("x.mkv")])


async def _seed(staging: Path, native_offset: int) -> int:
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=3,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        for idx in (1, 2, 3):
            s.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    output_index=idx + native_offset,
                    duration_seconds=1300,
                    file_size_bytes=4096,
                    state=TitleState.PENDING,
                    is_selected=True,
                )
            )
        await s.commit()
        return job.id


async def test_all_pass_receives_the_native_to_scan_map(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    # output_index is MakeMKV's native number; here it is scan-1 (a disc whose
    # native numbering starts at 0) — the divergence from issue #517.
    job_id = await _seed(staging, native_offset=-1)

    ext = _RecordingExtractor()
    monkeypatch.setattr(job_manager, "_extractor", ext)
    # _run_ripping ejects at the end of the rip; drive "Z:" does not exist.
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    await job_manager._run_ripping(job_id)

    assert ext.kwargs, "rip_titles was never called"
    first = ext.kwargs[0]
    assert first["title_indices"] is None, "every title selected -> all-pass"
    assert first["disc_title_map"] == {0: 1, 1: 2, 2: 3}


async def test_per_title_pass_gets_no_map(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id = await _seed(staging, native_offset=0)

    # Deselect one title so the rip is a per-title pass, where the skip-set is
    # consulted command-by-command and the boundary rule does not apply.
    async with async_session() as s:
        rows = (await s.execute(text("SELECT id FROM disc_titles ORDER BY title_index"))).all()
        t = await s.get(DiscTitle, rows[-1][0])
        t.is_selected = False
        await s.commit()

    ext = _RecordingExtractor()
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    await job_manager._run_ripping(job_id)

    assert ext.kwargs[0]["title_indices"] == [1, 2]
    assert ext.kwargs[0]["disc_title_map"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_rip_disc_title_map.py -v
```

Expected: FAIL with `KeyError: 'disc_title_map'`.

- [ ] **Step 3: Pass the map from `_run_ripping`**

In `backend/app/services/job_manager.py`, replace the main `rip_titles` call (currently `:2659-2668`) with:

```python
                result = await self._extractor.rip_titles(
                    drive_id,
                    output_dir,
                    title_indices=rip_indices,
                    title_complete_callback=on_title_complete,
                    stall_timeout=stall_timeout,
                    title_error_callback=on_title_error,
                    log_dir=get_makemkv_log_dir(job_id),
                    job_id=job_id,
                    # All-pass only: lets the extractor decide, at a title
                    # boundary, whether the user's skips have left nothing wanted
                    # on the disc. Keyed by MakeMKV's NATIVE title number (what
                    # the output filename carries) because scan order and native
                    # numbering diverge on some discs — issue #517.
                    disc_title_map=(
                        {expected_native_index(t): t.title_index for t in sorted_titles}
                        if rip_all
                        else None
                    ),
                )
```

`expected_native_index` is already imported in this module (`_has_complete_output` uses it). If ruff reports it undefined, add it to the existing `from app.services.ripping_helpers import ...` line.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_rip_disc_title_map.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_rip_disc_title_map.py
git commit -m "feat(ripping): give the extractor the disc's native->scan title map for all-pass skips"
```

---

### Task 4: Read `aborted_for_skip` instead of inferring

`RipResult.aborted_for_skip` has never been read by any caller, so a user-requested stop and a dying disc produced the same alarming `single-pass rip left N title(s) missing` warning. Logging/observability only, control flow is unchanged.

**Files:**
- Modify: `backend/app/services/job_manager.py:2705-2710`, `:2743-2748`

- [ ] **Step 1: Distinguish the two causes in the `missing`-empty branch**

Replace the `if not missing:` branch (currently `:2705-2710`) with:

```python
                if not missing:
                    # The single pass produced every title. 'all'-mode stall
                    # bookkeeping uses command indices that don't map to titles,
                    # so discard it — nothing actually failed.
                    if result.aborted_for_skip:
                        logger.info(
                            f"Job {safe_job}: full-disc pass stopped at a title "
                            f"boundary because every remaining title was skipped "
                            f"by the user — nothing left to rip"
                        )
                    result.stalled_titles = None
                    result.success = True
```

- [ ] **Step 2: Distinguish the two causes in the fallback branch**

Replace the `else:` branch's `logger.warning` (currently `:2744-2748`) with:

```python
                else:
                    if result.aborted_for_skip:
                        # A title was un-skipped between the boundary abort and
                        # this check. Not a fault — rip what is still wanted.
                        logger.info(
                            f"Job {safe_job}: full-disc pass stopped for a skip, but "
                            f"{len(missing)} title(s) are still wanted; ripping them "
                            f"individually: {[t.title_index for t in missing]}"
                        )
                    else:
                        logger.warning(
                            f"Job {safe_job}: single-pass rip left {len(missing)} title(s) "
                            f"missing; re-ripping individually: "
                            f"{[t.title_index for t in missing]}"
                        )
```

- [ ] **Step 3: Verify nothing regressed**

```bash
cd backend && uv run pytest tests/unit/test_rip_disc_title_map.py tests/unit/test_extractor_skip.py tests/unit/test_ripping_helpers.py tests/unit/test_rip_progress.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/job_manager.py
git commit -m "fix(ripping): report a skip-driven early stop as intent, not a missing-title fault"
```

---

### Task 5: Keep a progress monitor alive across the fallback rip

Independent of the skip bug and worth landing on its own. The per-title recovery pass runs with the filesystem progress monitor cancelled. That monitor is the **only** `_note_activity` heartbeat while a job is `RIPPING` (`job_manager.py:2556`), so a recovery pass longer than `timeout_ripping_seconds` (default 1200s, routine for even one Blu-ray title) is cancelled by the stale-job watchdog and force-advanced. The dashboard is also blank for the whole pass.

**Files:**
- Modify: `backend/app/services/job_manager.py:2677-2682` (comment), `:2749-2763`
- Test: `backend/tests/unit/test_rip_fallback_heartbeat.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_rip_fallback_heartbeat.py`:

```python
"""The per-title fallback rip must keep feeding the stale-job watchdog.

The filesystem progress monitor is the sole _note_activity heartbeat while a
job is RIPPING. Cancelling it before the fallback let reconcile_and_advance
cancel a perfectly healthy recovery rip after timeout_ripping_seconds.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

import app.database as _db
from app.core.extractor import RipResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


class _FallbackExtractor:
    """First ('all') pass produces nothing; the fallback writes a growing file."""

    def __init__(self, staging: Path):
        self.staging = staging

    def skip_title_index(self, job_id, idx):
        pass

    def unskip_title_index(self, job_id, idx):
        pass

    async def rip_titles(self, drive, output_dir, *, title_indices=None, job_id=None, **kw):
        if title_indices is None:
            return RipResult(success=True, output_files=[])
        # Fallback pass: grow a file for ~3s so the 1s progress monitor sees real
        # progress and — if it is running — fires _note_activity.
        p = self.staging / "TEST_t01.mkv"
        for i in range(1, 4):
            p.write_bytes(b"x" * 4096 * i)
            await asyncio.sleep(1.1)
        cb = kw.get("title_complete_callback")
        if cb:
            cb(1, p)
        return RipResult(success=True, output_files=[p])


async def test_fallback_rip_keeps_the_watchdog_clock_moving(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()

    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=1,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        s.add(
            DiscTitle(
                job_id=job.id,
                title_index=1,
                duration_seconds=1300,
                file_size_bytes=4096 * 3,
                state=TitleState.PENDING,
                is_selected=True,
            )
        )
        await s.commit()
        job_id = job.id

    ext = _FallbackExtractor(staging)
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr(job_manager, "_loop", asyncio.get_running_loop())
    # _run_ripping ejects at the end of the rip; drive "Z:" does not exist.
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    beats: list[int] = []
    real_note = job_manager._note_activity

    def _spy(jid: int) -> None:
        beats.append(jid)
        real_note(jid)

    monkeypatch.setattr(job_manager, "_note_activity", _spy)

    await job_manager._run_ripping(job_id)

    # One beat is the pre-fallback clock reset. The point of this test is that
    # MORE arrive while the fallback rip is running — i.e. a live monitor.
    assert beats.count(job_id) > 1, (
        "the fallback rip produced no heartbeat; the stale-job watchdog will "
        "cancel any recovery pass longer than timeout_ripping_seconds"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_rip_fallback_heartbeat.py -v
```

Expected: FAIL with the assertion message. Exactly one beat is recorded: the pre-fallback clock reset.

- [ ] **Step 3: Restart the monitor for the fallback pass**

In `backend/app/services/job_manager.py`, replace the pre-fallback comment and rip call (currently `:2749-2763`) with:

```python
                    # The fs monitor is the SOLE _note_activity heartbeat while a
                    # job is RIPPING, and it was cancelled when the main pass
                    # returned. Restart it for the recovery pass: without it the
                    # dashboard shows no progress, no speed and no per-title state
                    # for the whole pass, and — worse — the stale-job watchdog
                    # cancels a healthy recovery rip after timeout_ripping_seconds
                    # (default 1200s), which a single Blu-ray title routinely
                    # exceeds. Reset the clock first so the pass starts with a
                    # full window.
                    self._note_activity(job_id)
                    fallback_monitor = asyncio.create_task(_filesystem_progress_monitor())
                    _background_tasks.add(fallback_monitor)
                    fallback_monitor.add_done_callback(_background_tasks.discard)
                    try:
                        result = await self._extractor.rip_titles(
                            drive_id,
                            output_dir,
                            title_indices=[t.title_index for t in missing],
                            title_complete_callback=on_title_complete,
                            stall_timeout=stall_timeout,
                            title_error_callback=on_title_error,
                            log_dir=None,  # keep the informative all-pass rip.log
                            job_id=job_id,
                        )
                    finally:
                        fallback_monitor.cancel()
                        try:
                            await fallback_monitor
                        except asyncio.CancelledError:
                            # Expected: the monitor task was just cancelled above.
                            pass
```

- [ ] **Step 4: Correct the now-stale comment above the fallback block**

Replace the block comment at `:2677-2682` so it reads:

```python
            # One-pass + per-title fallback: a single 'all' pass that stalls or
            # errors leaves later titles unripped. Re-rip just the still-missing
            # selected titles individually so one bad title can't lose the rest of
            # the disc. (The fs progress monitor is restarted for that pass, so
            # live progress and the watchdog heartbeat continue; titles finalize
            # via the title-complete callback, and reconcile_stuck_titles is the
            # final safety net.)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_rip_fallback_heartbeat.py -v
```

Expected: 1 passed (takes ~5s, it deliberately spans several monitor polls).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_rip_fallback_heartbeat.py
git commit -m "fix(ripping): keep the progress monitor alive across the per-title fallback

It is the only _note_activity heartbeat during RIPPING, so cancelling it let
the stale-job watchdog cancel a healthy recovery rip after 1200s -- and left
the dashboard blank for the entire pass."
```

---

### Task 6: Never offer SKIP for an already-ripped track

`TitleState.QUEUED` means "ripped, on disk, waiting for a matching slot" (`disc_job.py:39`). `skip_rip_title` accepted it and `TrackGrid` offered the button for it, so a user could silently discard a successful rip, and, before Task 2, trigger the all-pass abort by doing so.

**Files:**
- Modify: `backend/app/services/job_manager.py:1533`, `frontend/src/app/components/TrackGrid.tsx:17,241`
- Test: `backend/tests/unit/test_skip_rip_title.py`, `frontend/src/app/components/TrackGrid.test.tsx`

- [ ] **Step 1: Write the failing backend test**

Append to `backend/tests/unit/test_skip_rip_title.py`:

```python
async def test_skip_rip_rejects_queued_title():
    # QUEUED means "ripped, on disk, waiting for a matching slot" — the bytes
    # already exist. Offering skip there silently discards a successful rip.
    job_id, title_id = await _make_job(title_state=TitleState.QUEUED)
    ok = await job_manager.skip_rip_title(job_id, title_id)
    assert ok is False
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.QUEUED
        assert t.is_selected is True
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_skip_rip_title.py::test_skip_rip_rejects_queued_title -v
```

Expected: FAIL, `assert True is False` (the skip is currently accepted).

- [ ] **Step 3: Restrict the backend guard**

In `backend/app/services/job_manager.py`, in `skip_rip_title`, replace:

```python
            if title.state not in (TitleState.PENDING, TitleState.QUEUED):
                return False
```

with:

```python
            # PENDING only. QUEUED means the title is already ripped and sitting
            # on disk waiting for a matching slot (see TitleState) — "skip the
            # rip" is meaningless there and silently discarded good bytes.
            if title.state != TitleState.PENDING:
                return False
```

In the same method's docstring, replace the line `Acts only on PENDING or QUEUED titles (never one actively RIPPING or` and its continuation `already terminal). Marks the title SKIPPED + deselected, registers its` with:

```python
        Acts only on PENDING titles (never one actively RIPPING, one already
        ripped and QUEUED for matching, or one already terminal). Marks the
        title SKIPPED + deselected, registers its
```

- [ ] **Step 4: Run the backend tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_skip_rip_title.py tests/unit/test_completion_skipped.py -v
```

Expected: all pass, including the pre-existing `test_skip_rip_pending_title_marks_skipped`.

- [ ] **Step 5: Write the failing frontend test**

Append to `frontend/src/app/components/TrackGrid.test.tsx`:

```tsx
describe("TrackGrid — skip affordance", () => {
  it("offers SKIP for a pending track", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "7", state: "pending" })]}
        onSkipTrack={() => {}}
      />,
    );
    expect(screen.getByTestId("skip-track-7")).toBeInTheDocument();
  });

  it("does NOT offer SKIP for a queued track — it is already ripped to disk", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "8", state: "queued" })]}
        onSkipTrack={() => {}}
      />,
    );
    expect(screen.queryByTestId("skip-track-8")).not.toBeInTheDocument();
  });

  it("still offers UN-SKIP for a skipped track", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "9", state: "skipped" })]}
        onUnskipTrack={() => {}}
      />,
    );
    expect(screen.getByTestId("unskip-track-9")).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

```bash
cd frontend && npm run test:unit -- TrackGrid
```

Expected: the queued case FAILS, `skip-track-8` is currently rendered.

- [ ] **Step 7: Restrict the frontend condition**

In `frontend/src/app/components/TrackGrid.tsx`, replace:

```tsx
                  {onSkipTrack && (track.state === "pending" || track.state === "queued") && (
```

with:

```tsx
                  {/* PENDING only: a queued track is already ripped and on disk,
                      so "skip the rip" would just discard good bytes. */}
                  {onSkipTrack && track.state === "pending" && (
```

And update the prop doc at `TrackGrid.tsx:17`:

```tsx
  /** Skip a not-yet-ripped track (PENDING only). Omit to hide the control. */
```

- [ ] **Step 8: Run the frontend tests to verify they pass**

```bash
cd frontend && npm run test:unit -- TrackGrid
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_skip_rip_title.py frontend/src/app/components/TrackGrid.tsx frontend/src/app/components/TrackGrid.test.tsx
git commit -m "fix(skip): only offer SKIP for PENDING tracks, never already-ripped QUEUED ones"
```

---

### Task 7: Make the simulated rip honor a skip

The E2E in Task 8 is worthless without this: `_simulate_ripping` walks its title list unconditionally and would flip a `SKIPPED` track back to `RIPPING`, so the spec would assert UI state the pipeline contradicts.

**Files:**
- Modify: `backend/app/services/simulation_service.py` (inside `_simulate_ripping`'s per-title loop, around `:605-612`)
- Test: `backend/tests/unit/test_simulation_skip.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_simulation_skip.py`:

```python
"""The simulated rip must drop a track the user skipped mid-rip."""

import asyncio

import pytest
from sqlalchemy import text

import app.database as _db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def test_simulated_rip_skips_a_track_skipped_mid_rip():
    async with async_session() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path="/tmp/none",
            total_titles=3,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        titles = []
        for idx in (1, 2, 3):
            t = DiscTitle(
                job_id=job.id,
                title_index=idx,
                duration_seconds=1300,
                file_size_bytes=1024,
                state=TitleState.PENDING,
                is_selected=True,
            )
            s.add(t)
            await s.commit()
            await s.refresh(t)
            titles.append(t)
            s.expunge(t)
        job_id = job.id
        last_id = titles[-1].id

    # Use the wired singleton: SimulationService requires an EventBroadcaster and
    # a JobStateMachine, and JobManager has already constructed it with both.
    sim = job_manager._simulation
    rip = asyncio.create_task(
        sim._simulate_ripping(job_id, titles, speed_multiplier=1, content_type=ContentType.TV)
    )
    # Skip the LAST track while the loop is still on the first one.
    await asyncio.sleep(0.3)
    assert await job_manager.skip_rip_title(job_id, last_id) is True
    await rip

    async with async_session() as s:
        t = await s.get(DiscTitle, last_id)
        assert t.state == TitleState.SKIPPED
        assert t.output_filename is None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_simulation_skip.py -v
```

Expected: FAIL, the state is `queued` (the sim ripped it anyway) and `output_filename` is set.

- [ ] **Step 3: Honor SKIPPED in the sim loop**

In `backend/app/services/simulation_service.py`, inside `_simulate_ripping`'s `for i, title in enumerate(titles):` body, replace:

```python
                title_db = await session.get(DiscTitle, title.id)
                if title_db:
                    title_db.state = TitleState.RIPPING
```

with:

```python
                # The rip loop holds ONE long-lived session, so its identity map
                # still caches the pre-skip row. skip_rip_title commits from a
                # different session, so expire first or the skip is invisible here.
                session.expire_all()
                title_db = await session.get(DiscTitle, title.id)
                if title_db and title_db.state == TitleState.SKIPPED:
                    # The user skipped this track from the dashboard mid-rip. The
                    # real extractor drops it too (per-title commands consult the
                    # live skip-set; an all-pass stops at the next title boundary
                    # when nothing wanted remains), so the simulation must match.
                    logger.info(
                        f"Job {job_id}: simulated rip skipping user-skipped title "
                        f"{title.title_index}"
                    )
                    continue
                if title_db:
                    title_db.state = TitleState.RIPPING
```

Leave the rest of the loop body unchanged. Note the overall progress percentage will now stop short of 100% when tracks are skipped, this matches the real pipeline, whose `total_job_bytes` is likewise computed before any skip.

- [ ] **Step 4: Run it to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_simulation_skip.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Verify no simulation regressions**

```bash
cd backend && uv run pytest tests/integration/test_simulation.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/simulation_service.py backend/tests/unit/test_simulation_skip.py
git commit -m "fix(simulation): drop a user-skipped track instead of ripping it anyway"
```

---

### Task 8: E2E regression, a skip must not stop the current track

**Files:**
- Modify: `frontend/e2e/track-skipping.spec.ts`

- [ ] **Step 1: Correct the stale comment in the existing test**

The first test's comment says "The SKIP control only renders for tracks in pending/queued state". Task 6 narrowed that. Replace that sentence with:

```ts
        // The SKIP control only renders for tracks in the PENDING state
        // (TrackGrid gates it there — a QUEUED track is already ripped to disk),
        // so we act on the LAST pending track, which the rip loop reaches last.
```

- [ ] **Step 2: Add the regression scenario**

Append inside the existing `test.describe('Track skipping, skip / un-skip a not-yet-ripped track', ...)` block:

```ts
    test('skipping later tracks leaves the in-flight rip running and the job finishing', async ({ page }) => {
        // Regression for the 0.28.2 report: skipping FUTURE tracks stopped the
        // track being ripped, and the disc either ejected "successfully" with
        // work outstanding or froze with no progress. Rip slowly enough that
        // several tracks are still PENDING when we click.
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 2,
        });

        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByTestId(/^skip-track-\d+$/).first()).toBeVisible({ timeout: 15000 });

        // Skip the three LAST pending tracks — the ones the rip loop reaches
        // last, so they stay PENDING while we click through them.
        const ids: string[] = [];
        for (let i = 0; i < 3; i++) {
            const btn = page.getByTestId(/^skip-track-\d+$/).last();
            const testId = await btn.getAttribute('data-testid');
            if (!testId) break;
            const trackId = testId.replace('skip-track-', '');
            ids.push(trackId);
            await btn.click();
            await expect(page.getByTestId(`unskip-track-${trackId}`)).toBeVisible({ timeout: 10000 });
        }
        expect(ids.length).toBe(3);

        // The job must NOT fail. Before the fix, skipping every remaining track
        // ejected with work outstanding, and skipping some froze the job in
        // RIPPING until the 1200s watchdog force-advanced it to an error.
        await expect(page.locator(SELECTORS.stateFailed)).toHaveCount(0);

        // No track is left stuck mid-rip: the RIPPING indicator clears on its own.
        await expect(page.locator(SELECTORS.stateRipping)).toHaveCount(0, { timeout: 60000 });

        // Every skipped track still reads SKIPPED — none was ripped after the fact.
        for (const id of ids) {
            const card = page
                .locator(`${SELECTORS.trackItem}:has([data-testid="unskip-track-${id}"])`)
                .first();
            await expect(card.getByText('SKIPPED, WILL NOT RIP')).toBeVisible({ timeout: 30000 });
        }
    });
```

- [ ] **Step 3: Run the spec**

Start the E2E backend (`DEBUG=true`) and the Vite dev server per `CLAUDE.md`, then:

```bash
cd frontend && npm run test:e2e -- track-skipping.spec.ts
```

Expected: 2 passed. Run it **twice**, this spec is timing-sensitive by nature, and one green run does not establish stability.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/track-skipping.spec.ts
git commit -m "test(e2e): skipping later tracks must not stop the in-flight rip"
```

---

### Task 9: Changelog and full verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the `[Unreleased]` entries**

Under the `## [Unreleased]` heading's `### Fixed` subsection in `CHANGELOG.md` (create the subsection if absent), add:

```markdown
- Skipping a not-yet-ripped track no longer interrupts the track currently being ripped. A full-disc rip now stops only at a title boundary, and only when every remaining track has been skipped — the one case where stopping wastes nothing. Previously, skipping a later track killed MakeMKV mid-write, discarded the in-progress track, and could eject the disc with work outstanding or leave the job with no visible progress. (#NNN)
- The per-title recovery rip now reports live progress and keeps the stale-job watchdog fed. It previously ran with the progress monitor stopped, so the dashboard went blank and any recovery longer than the ripping timeout was cancelled as if it had stalled. (#NNN)
- Tracks that have already finished ripping no longer offer a SKIP control. Skipping one discarded a successful rip. (#NNN)
```

Replace each `#NNN` with the real PR number once it exists.

- [ ] **Step 2: Run the full backend unit tier**

```bash
cd backend && uv run pytest tests/unit/ -q
```

Expected: all pass. Runs several minutes. Do **not** add `-p no:logging`, it speeds the tier up but breaks the `caplog` tests.

- [ ] **Step 3: Run the ripping-related integration tests**

```bash
cd backend && uv run pytest tests/integration/test_workflow.py tests/integration/test_simulation.py tests/integration/test_error_recovery.py -q
```

Expected: all pass. `test_workflow.py` runs against the real app DB, check the project notes before running it against a populated library.

- [ ] **Step 4: Lint and format the backend**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

Expected: no findings.

- [ ] **Step 5: Build and lint the frontend**

```bash
cd frontend && npm run test:unit && npm run build && npm run lint
```

Expected: all pass, clean build.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for the mid-rip skip fixes"
```

---

## Manual verification (real disc)

Automated coverage cannot exercise a real MakeMKV all-pass. Before merging, with exactly one backend running against a real disc (see `CLAUDE.md`, "Parallel sessions"):

- [ ] Insert a multi-track TV disc, let the first track start ripping, then skip **one** middle track. The current track's progress bar must keep moving without interruption, the skipped track must show `SKIPPED, WILL NOT RIP`, and no `*_tNN.mkv` for it may remain in staging afterwards.
- [ ] On a fresh disc, let the first track rip, then skip **every** remaining track. The rip must stop shortly after the current track finishes (not mid-track), the log must show `every remaining title is user-skipped; stopping the full-disc pass`, and the disc must eject with the completed tracks organized.
- [ ] Confirm no `single-pass rip left N title(s) missing` warning appears in either case.
- [ ] Confirm the SKIP control is absent on tracks that have finished ripping and are waiting to match.

---

## Deliberately not included

- **Splitting the all-pass into per-title commands whenever a skip is pending.** This would let a skip be honored anywhere, but costs one disc re-open per title, the thrashing the all-pass exists to avoid.
- **A "discard this ripped track" action for QUEUED/MATCHED titles.** Task 6 removes the accidental affordance; a deliberate one needs its own design, since it means deleting a file already written and possibly already organized.
- **Reworking the stale-job watchdog's phase model.** Task 5 fixes the missing heartbeat; making the watchdog aware of rip sub-phases is a separate concern.

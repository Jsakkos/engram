# Fast-Fail Zero-Output Rip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a disc MakeMKV cannot read at all from occupying engram for ~26 minutes, and replace the misleading "dirty or damaged" message with MakeMKV's specific region-mismatch diagnosis.

**Architecture:** Three independent changes. Two pure helper functions in `extractor.py` (region-mismatch detection, circuit-breaker decision) are unit-tested in isolation, then wired into the existing `run_rip_with_streaming` command loop. A third change in `job_manager.py` skips the per-title fallback when the all-pass both stalled and produced nothing. Pure helpers first matches this codebase's established pattern (`_is_stalled`, `_extract_created_mkv`, `title_index_from_filename` are all module-level and separately tested).

**Tech Stack:** Python 3.11, pytest (`uv run pytest`), ruff (line length 100, double quotes), SQLModel/async SQLAlchemy.

**Spec:** `docs/superpowers/specs/2026-07-19-fast-fail-zero-output-rip-design.md`

---

## Context an implementer needs

- **All commands run from `backend/`.** This project uses `uv`, never bare `python`/`pytest`/`pip`. Always `uv run pytest`, `uv run ruff check .`.
- **Never delete `backend/engram.db`.** It holds real API keys.
- **Do not write em dashes or en dashes** in code comments, docstrings, commit messages, or user-facing strings. Use colons, commas, semicolons, or parentheses. (Existing strings in the file use them; leave those alone, just do not add new ones.)
- **The `rip_stalled` error code is reused deliberately.** Do not invent a `region_mismatch` error code. New REVIEW error codes must be registered in `_NON_REMATCHABLE_REVIEW_ERRORS` (`app/services/finalization_coordinator.py:222`) or auto-escalation overwrites `match_details` and the diagnosis is lost before the user sees it.
- **Some pipeline/unit tests need an initialized DB.** If tests fail with `no such table`, run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` once in this worktree.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/core/extractor.py` | Modify | Adds two pure helpers (`_is_region_mismatch`, `_should_abandon_zero_output_rip`), a `STALL_POLL_INTERVAL` constant, a `REGION_MISMATCH_FAILURE_REASON` constant, a `failure_reason` field on `RipResult`, and wires all of it into `run_rip_with_streaming`. |
| `backend/app/services/job_manager.py` | Modify | Gates the per-title fallback (`:2485-2508`) on stall-plus-zero-output; uses `RipResult.failure_reason` when routing stalled titles to review (`:2531-2533`). |
| `backend/tests/unit/test_extractor_zero_output.py` | Create | All new extractor tests: both pure helpers plus one wiring test through a stubbed `subprocess.Popen`. |
| `backend/tests/unit/test_job_manager.py` | Modify | Adds fallback-skip tests to the existing `TestOnePassRipFallback` class, reusing its `_seed_two_selected` / `rip_env` harness. |
| `CHANGELOG.md` | Modify | User-facing entry under `[Unreleased]`. |

---

### Task 1: Region-mismatch detection helper

**Files:**
- Modify: `backend/app/core/extractor.py` (constants block near line 29)
- Test: `backend/tests/unit/test_extractor_zero_output.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_extractor_zero_output.py`:

```python
"""Tests for fast-failing a rip that produces no output (issue #506).

Covers the two pure helpers added to the extractor and their wiring into the
MakeMKV command loop:

* ``_is_region_mismatch`` recognises MakeMKV's MSG:3032 region warning, so a
  region-locked disc gets an actionable message instead of "dirty or damaged".
* ``_should_abandon_zero_output_rip`` stops re-opening a disc that has already
  proven unreadable, instead of burning one full stall timeout per title.
"""

import pytest

from app.core.extractor import (
    REGION_MISMATCH_FAILURE_REASON,
    STALL_FAILURE_REASON,
    _is_region_mismatch,
)


@pytest.mark.unit
class TestRegionMismatchDetection:
    """MSG:3032 is MakeMKV's region-mismatch warning."""

    def test_detects_robot_mode_region_message(self):
        line = (
            'MSG:3032,0,2,"Region setting of drive ASUS:BW-16D1HT does not match '
            'the region of currently inserted disc, trying to work around..."'
        )
        assert _is_region_mismatch(line) is True

    def test_progress_line_is_not_a_region_mismatch(self):
        assert _is_region_mismatch("PRGV:14417,11915,65536") is False

    def test_other_msg_codes_are_not_region_mismatch(self):
        line = "MSG:5011,0,0,\"File '/output/title00.mkv' created successfully.\""
        assert _is_region_mismatch(line) is False

    def test_unrelated_code_containing_3032_is_not_matched(self):
        # A different message code that merely contains the digits must not match.
        assert _is_region_mismatch('MSG:13032,0,2,"Something else"') is False

    def test_region_reason_differs_from_generic_stall_reason(self):
        # The whole point of the change: the user must not be told the disc is
        # dirty when the real problem is the drive's region setting.
        assert REGION_MISMATCH_FAILURE_REASON != STALL_FAILURE_REASON
        assert "region" in REGION_MISMATCH_FAILURE_REASON.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: FAIL at import with `ImportError: cannot import name 'REGION_MISMATCH_FAILURE_REASON'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/core/extractor.py`, immediately after the existing `STALL_FAILURE_REASON` definition (line 29), add:

```python
# MakeMKV emits MSG:3032 when the drive's region setting does not match the
# inserted disc. It retries internally ("trying to work around...") and can hang
# there indefinitely, so the rip reads as a generic stall. Detecting the code
# lets us name the real cause instead of blaming the disc.
REGION_MISMATCH_FAILURE_REASON = (
    "Ripping stalled: the drive's region setting does not match this disc's "
    "region, so MakeMKV could not open the disc. Set the drive's region to match "
    "the disc, or use a region-free drive."
)
```

Then, next to the other pure line helpers (after `_extract_created_mkv`, around line 64), add:

```python
def _is_region_mismatch(line: str) -> bool:
    """Whether *line* is MakeMKV's MSG:3032 region-mismatch warning.

    Robot mode emits ``MSG:3032,0,2,"Region setting of drive ..."``. The trailing
    comma in the prefix keeps this from matching unrelated codes that merely
    contain the digits (e.g. ``MSG:13032``).
    """
    return line.startswith("MSG:3032,")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_zero_output.py
git commit -m "feat(extractor): detect MakeMKV MSG:3032 region-mismatch warning (#506)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Circuit-breaker decision helper

**Files:**
- Modify: `backend/app/core/extractor.py` (constants block, and next to `_is_stalled`)
- Test: `backend/tests/unit/test_extractor_zero_output.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_extractor_zero_output.py`:

```python
@pytest.mark.unit
class TestZeroOutputAbandonDecision:
    """When every attempt stalls and nothing is written, stop re-opening the disc.

    Abandoning is not lossy: each skipped title still routes to REVIEW as
    re-rippable, and the user has a manual re-rip path. So the threshold can be
    aggressive.
    """

    def test_does_not_abandon_below_the_stall_threshold(self):
        # One stall is not yet evidence the whole disc is unreadable.
        assert _should_abandon_zero_output_rip(stall_count=1, completed_outputs=0) is False

    def test_abandons_at_threshold_with_no_output(self):
        assert _should_abandon_zero_output_rip(stall_count=2, completed_outputs=0) is True

    def test_abandons_above_threshold_with_no_output(self):
        assert _should_abandon_zero_output_rip(stall_count=5, completed_outputs=0) is True

    def test_never_abandons_once_any_output_exists(self):
        # A disc that produced a file is partially readable. Stalls on later
        # titles are the "one bad title" case the per-title loop exists to
        # survive, so keep going.
        assert _should_abandon_zero_output_rip(stall_count=9, completed_outputs=1) is False

    def test_no_stalls_never_abandons(self):
        assert _should_abandon_zero_output_rip(stall_count=0, completed_outputs=0) is False
```

Add `_should_abandon_zero_output_rip` to the import block at the top of the file:

```python
from app.core.extractor import (
    REGION_MISMATCH_FAILURE_REASON,
    STALL_FAILURE_REASON,
    _is_region_mismatch,
    _should_abandon_zero_output_rip,
)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: FAIL at import with `ImportError: cannot import name '_should_abandon_zero_output_rip'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/core/extractor.py`, add to the constants block (after `STABLE_CHECKS_REQUIRED`, line 34):

```python
# Consecutive stalled commands, with nothing written by any of them, after which
# a rip gives up instead of re-opening the disc once per remaining title. A disc
# that has failed this many times in a row at disc-open is not going to succeed
# on the next title, and each retry costs a full ripping_stall_timeout.
# Abandoning is safe because every skipped title still routes to REVIEW as
# re-rippable (see route_rip_failure_to_review).
ZERO_OUTPUT_STALL_LIMIT = 2
```

Add the helper next to `_is_stalled` (after line 54):

```python
def _should_abandon_zero_output_rip(stall_count: int, completed_outputs: int) -> bool:
    """Whether to stop issuing rip commands because the disc is unreadable.

    True only when this invocation has stalled ``ZERO_OUTPUT_STALL_LIMIT`` times
    **and** produced no completed output at all. Requiring zero output is what
    keeps the "one bad title, rest of the disc fine" case working: as soon as a
    single file lands, the rip is partially succeeding and every remaining title
    is still worth attempting.
    """
    if completed_outputs > 0:
        return False
    return stall_count >= ZERO_OUTPUT_STALL_LIMIT
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_zero_output.py
git commit -m "feat(extractor): add zero-output rip abandon decision helper (#506)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Carry the specific failure reason on RipResult

**Files:**
- Modify: `backend/app/core/extractor.py:171-178` (the `RipResult` dataclass)
- Test: `backend/tests/unit/test_extractor_zero_output.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_extractor_zero_output.py`:

```python
@pytest.mark.unit
class TestRipResultFailureReason:
    """RipResult carries the specific stall reason so the live per-title update
    and the History entry cannot disagree about why a rip failed."""

    def test_failure_reason_defaults_to_none(self):
        result = RipResult(success=True, output_files=[])
        assert result.failure_reason is None

    def test_failure_reason_round_trips(self):
        result = RipResult(
            success=False,
            output_files=[],
            stalled_titles=[1],
            failure_reason=REGION_MISMATCH_FAILURE_REASON,
        )
        assert result.failure_reason == REGION_MISMATCH_FAILURE_REASON
```

Add `RipResult` to the import block at the top of the file:

```python
from app.core.extractor import (
    REGION_MISMATCH_FAILURE_REASON,
    STALL_FAILURE_REASON,
    RipResult,
    _is_region_mismatch,
    _should_abandon_zero_output_rip,
)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py::TestRipResultFailureReason -v
```

Expected: FAIL with `TypeError: RipResult.__init__() got an unexpected keyword argument 'failure_reason'`.

- [ ] **Step 3: Write minimal implementation**

Replace the `RipResult` dataclass at `backend/app/core/extractor.py:171-178`:

```python
@dataclass
class RipResult:
    """Result of a ripping operation."""

    success: bool
    output_files: list[Path]
    error_message: str | None = None
    stalled_titles: list[int] | None = None  # Command indices that were skipped due to stall
    # Specific reason for a stall, when one is known (e.g. a region mismatch).
    # None means the generic STALL_FAILURE_REASON applies. Callers routing
    # stalled titles to review read this so the live update and History agree.
    failure_reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_zero_output.py
git commit -m "feat(extractor): carry a specific failure_reason on RipResult (#506)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire both helpers into the command loop

This is the only task that touches the live subprocess loop. Read `run_rip_with_streaming` (`backend/app/core/extractor.py:672-919`) in full before editing.

**Files:**
- Modify: `backend/app/core/extractor.py` (constants block; `run_rip_with_streaming`)
- Test: `backend/tests/unit/test_extractor_zero_output.py`

- [ ] **Step 1: Add the patchable poll-interval constant**

The stall watchdog currently hardcodes a 5 second poll interval as a default argument, which makes the loop untestable without a 5+ second sleep. Add to the constants block in `backend/app/core/extractor.py` (after `ZERO_OUTPUT_STALL_LIMIT`):

```python
# Seconds between stall-watchdog polls. A module constant so tests can shorten it;
# production behaviour is unchanged at 5 s.
STALL_POLL_INTERVAL = 5.0
```

Then change the watchdog thread construction (currently `backend/app/core/extractor.py:803-808`) to pass it explicitly:

```python
                        watchdog_thread = threading.Thread(
                            target=_stall_watchdog,
                            args=(process, output_dir, stall_timeout, STALL_POLL_INTERVAL),
                            daemon=True,
                        )
```

Leave the `poll_interval=5.0` default in the `_stall_watchdog` signature alone; the explicit argument now always wins.

- [ ] **Step 2: Write the failing wiring test**

Append to `backend/tests/unit/test_extractor_zero_output.py`. Add these imports at the top of the file:

```python
import threading
from pathlib import Path
from unittest.mock import patch

from app.core.extractor import MakeMKVExtractor
```

Then append:

```python
class _FakeStdout:
    """MakeMKV stdout that emits a few lines then hangs until terminated.

    The reader loop consumes this with ``iter(process.stdout.readline, "")``, so
    ``readline`` must return "" to signal EOF. Blocking until the stall watchdog
    kills the process is exactly the behaviour of a MakeMKV stuck at disc-open.
    """

    def __init__(self, lines: list[str], killed: threading.Event):
        self._lines = list(lines)
        self._killed = killed

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0) + "\n"
        self._killed.wait(timeout=10)
        return ""


class _FakeProc:
    """A makemkvcon that never writes a file and must be killed to stop."""

    def __init__(self, lines: list[str]):
        self._killed = threading.Event()
        self.returncode = None
        self.stdout = _FakeStdout(lines, self._killed)
        self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1
        self._killed.set()

    def wait(self):
        self._killed.wait(timeout=10)
        if self.returncode is None:
            self.returncode = 1
        return self.returncode


@pytest.mark.unit
class TestZeroOutputAbandonWiring:
    """End-to-end through the real command loop with makemkvcon stubbed."""

    async def test_abandons_remaining_titles_and_reports_them_stalled(self, tmp_path):
        """Four titles, every one stalling with no output: the loop must stop
        after ZERO_OUTPUT_STALL_LIMIT and still report all four as stalled."""
        spawned = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc(["PRGV:14417,11915,65536"])
            spawned.append(proc)
            return proc

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1, 2, 3],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=1,
            )

        # Only the first two commands ever ran; the rest were abandoned.
        assert len(spawned) == 2
        # All four titles are still accounted for as stalled, so none is left
        # stranded in RIPPING with no review entry.
        assert result.stalled_titles == [1, 2, 3, 4]
        assert sorted(idx for idx, _ in errors) == [1, 2, 3, 4]

    async def test_region_mismatch_sets_the_specific_reason(self, tmp_path):
        """A stall preceded by MSG:3032 reports the region cause, not 'dirty'."""

        def _fake_popen(cmd, **kwargs):
            return _FakeProc(
                [
                    'MSG:3032,0,2,"Region setting of drive ASUS:BW-16D1HT does not '
                    'match the region of currently inserted disc, trying to work '
                    'around..."',
                    "PRGV:14417,11915,65536",
                ]
            )

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=2,
            )

        assert result.failure_reason == REGION_MISMATCH_FAILURE_REASON
        assert all(reason == REGION_MISMATCH_FAILURE_REASON for _, reason in errors)

    async def test_plain_stall_keeps_the_generic_reason(self, tmp_path):
        """Without MSG:3032 the message is unchanged, so existing behaviour holds."""

        def _fake_popen(cmd, **kwargs):
            return _FakeProc(["PRGV:14417,11915,65536"])

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=3,
            )

        assert result.failure_reason is None
        assert all(reason == STALL_FAILURE_REASON for _, reason in errors)
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py::TestZeroOutputAbandonWiring -v
```

Expected: FAIL. `test_abandons_remaining_titles_and_reports_them_stalled` fails with `assert 4 == 2` (all four commands ran), and the region test fails with `assert None == '...region...'`.

- [ ] **Step 4: Track the region flag in the reader loop**

In `run_rip_with_streaming`, alongside the existing `last_progress` declaration (`backend/app/core/extractor.py:686`), add:

```python
            # Set when MakeMKV reports a region mismatch (MSG:3032). Single-element
            # list so the watchdog thread sees writes from the reader loop, matching
            # the last_progress pattern above.
            region_mismatch = [False]

            def _stall_reason() -> str:
                """The most specific reason we can give for a stall."""
                return REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else STALL_FAILURE_REASON
```

In the stdout reader loop, directly after the existing `PRGV:`/`PRGC:`/`PRGT:` liveness block (`backend/app/core/extractor.py:836-837`), add:

```python
                        # A region mismatch makes MakeMKV retry disc-open forever;
                        # remember it so the stall is reported with its real cause.
                        if _is_region_mismatch(line):
                            region_mismatch[0] = True
```

In `_stall_watchdog`, replace the hardcoded `STALL_FAILURE_REASON` in the `title_error_callback` invocation (`backend/app/core/extractor.py:750`) with `_stall_reason()`:

```python
                        if title_error_callback:
                            _safe_callback(
                                title_error_callback,
                                current_title_idx,
                                _stall_reason(),
                                label="title_error_callback",
                            )
```

- [ ] **Step 5: Add the circuit breaker to the command loop**

In the command loop's stall-handling branch, after the incomplete-file cleanup and immediately before the existing `continue` (`backend/app/core/extractor.py:891-892`), replace:

```python
                            # Don't break — continue to next command
                            continue
```

with:

```python
                            # Give up rather than re-opening a disc that has
                            # already failed at disc-open this many times with
                            # nothing written. Each retry costs a full
                            # stall_timeout, which is what made a region-locked
                            # disc take ~26 minutes to resolve (issue #506).
                            if _should_abandon_zero_output_rip(
                                len(stalled_commands), len(output_files)
                            ):
                                remaining = list(
                                    range(current_title_idx + 1, len(commands) + 1)
                                )
                                if remaining:
                                    logger.warning(
                                        f"Abandoning rip after {len(stalled_commands)} "
                                        f"stalled command(s) with no output. Skipping "
                                        f"{len(remaining)} remaining command(s)."
                                    )
                                # Report the untried commands as stalled too, so every
                                # title still reaches review instead of being stranded.
                                for skipped_idx in remaining:
                                    stalled_commands.add(skipped_idx)
                                    if title_error_callback:
                                        _safe_callback(
                                            title_error_callback,
                                            skipped_idx,
                                            _stall_reason(),
                                            label="title_error_callback",
                                        )
                                break

                            # Don't break — continue to next command
                            continue
```

- [ ] **Step 6: Populate `failure_reason` on the returned RipResult**

There are three `return RipResult(...)` sites in the success/stall paths. Update the two that can carry a stall (`backend/app/core/extractor.py:967-973` and `:983-987`) so both report the reason. First:

```python
            if returncode != 0 and not stalled:
                return RipResult(
                    success=False,
                    output_files=output_files,
                    error_message=stderr or "Unknown error during ripping",
                    stalled_titles=stalled_list,
                    failure_reason=REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else None,
                )
```

Second:

```python
            return RipResult(
                success=True,
                output_files=output_files,
                stalled_titles=stalled_list,
                failure_reason=REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else None,
            )
```

`region_mismatch` is declared inside `run_rip_with_streaming` but these returns are in the enclosing `_rip_titles_unlocked` scope. Hoist the declaration: move the `region_mismatch = [False]` line and the `_stall_reason` helper out of `run_rip_with_streaming` to just above its `def` (next to the `_fs_lock` declaration at `backend/app/core/extractor.py:625`), so both scopes can read it. Keep `last_progress` where it is.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_extractor_zero_output.py -v
```

Expected: 15 passed.

- [ ] **Step 8: Verify no existing extractor behaviour regressed**

```bash
uv run pytest tests/unit/test_extractor.py tests/unit/test_extractor_callbacks.py tests/unit/test_extractor_shutdown.py tests/unit/test_extractor_skip.py tests/unit/test_title_completion_detector.py -v
```

Expected: all pass. If `test_extractor_shutdown.py` hangs, the `_FakeProc.wait()` timeout is masking a real deadlock in the new `break` path; check that `break` exits the `for` loop and not the `try`.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_zero_output.py
git commit -m "feat(extractor): abandon a rip that stalls with zero output (#506)

A disc MakeMKV cannot open at all previously burned one full stall
timeout per title. Stop issuing commands after two consecutive stalls
with nothing written, and report the remaining titles as stalled so each
still reaches review. Surface MakeMKV's MSG:3032 region mismatch as the
stall reason when present.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Skip the per-title fallback when the pass stalled with no output

**Files:**
- Modify: `backend/app/services/job_manager.py:2485-2508` (fallback gate), `:2531-2533` (reason)
- Test: `backend/tests/unit/test_job_manager.py` (extend `TestOnePassRipFallback`, line 871)

- [ ] **Step 1: Write the failing tests**

Append these three tests inside the existing `TestOnePassRipFallback` class in `backend/tests/unit/test_job_manager.py` (after `test_single_pass_failure_reripsonly_missing`, line 923). They reuse that class's existing `_seed_two_selected` and `rip_env` fixtures:

```python
    async def test_stalled_pass_with_no_output_skips_fallback(self, rip_env, monkeypatch):
        """A disc that stalled and wrote nothing is unreadable: re-opening it
        once per title only burns another stall timeout each (issue #506)."""
        job = await _seed_two_selected(str(rip_env))
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        rip = AsyncMock(
            return_value=RipResult(success=True, output_files=[], stalled_titles=[1])
        )
        monkeypatch.setattr(job_manager._extractor, "rip_titles", rip)

        await job_manager._run_ripping(job.id)

        assert rip.await_count == 1

    async def test_stalled_pass_with_some_output_still_falls_back(self, rip_env, monkeypatch):
        """A partially readable disc keeps the per-title recovery path: one bad
        title must not cost the rest of the disc."""
        job = await _seed_two_selected(str(rip_env))
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        produced = rip_env / "Some Show_t00.mkv"
        produced.write_bytes(b"data")

        rip = AsyncMock(
            return_value=RipResult(
                success=True, output_files=[produced], stalled_titles=[2]
            )
        )
        monkeypatch.setattr(job_manager._extractor, "rip_titles", rip)

        await job_manager._run_ripping(job.id)

        assert rip.await_count == 2

    async def test_region_reason_reaches_the_review_entry(self, rip_env, monkeypatch):
        """The region diagnosis must survive to match_details, under the existing
        rip_stalled code (a new code would be wiped by auto-escalation)."""
        from app.core.extractor import REGION_MISMATCH_FAILURE_REASON

        job, title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            is_selected=True,
            title_index=0,
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(
            monkeypatch,
            RipResult(
                success=True,
                output_files=[],
                stalled_titles=[1],
                failure_reason=REGION_MISMATCH_FAILURE_REASON,
            ),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscTitle, title.id)
        d = json.loads(refreshed.match_details)
        assert d["error"] == "rip_stalled"
        assert d["reason"] == REGION_MISMATCH_FAILURE_REASON
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_job_manager.py::TestOnePassRipFallback -v
```

Expected: `test_stalled_pass_with_no_output_skips_fallback` FAILS with `assert 2 == 1`, and `test_region_reason_reaches_the_review_entry` FAILS on the `reason` assertion. The other two pass already.

Note: if `test_region_reason_reaches_the_review_entry` instead fails with a `KeyError: 'reason'`, inspect what `route_rip_failure_to_review` actually stores (`app/services/matching_coordinator.py:1813`) and assert against the field it uses, rather than changing the coordinator.

- [ ] **Step 3: Gate the fallback**

In `backend/app/services/job_manager.py`, change the fallback trigger at line 2485 from:

```python
                if missing:
```

to:

```python
                # A pass that stalled and wrote nothing means the disc itself is
                # unreadable (bad region setting, unsupported protection, dead
                # drive). The per-title fallback exists to rescue the "one bad
                # title lost the rest of the disc" case, which presupposes
                # partial success; with zero output there is nothing to rescue
                # and each retry costs another full stall timeout (issue #506).
                # Both conditions are required: a pass that simply reported no
                # files without stalling has given us no evidence of an
                # unreadable disc, so it still earns a per-title retry.
                disc_unreadable = bool(result.stalled_titles) and not result.output_files
                if disc_unreadable and missing:
                    logger.warning(
                        f"Job {safe_job}: single-pass rip stalled with no output; "
                        f"skipping the per-title fallback for {len(missing)} title(s) "
                        f"and routing them to review."
                    )
                if missing and not disc_unreadable:
```

- [ ] **Step 4: Use the specific failure reason when routing stalled titles**

Replace the hardcoded reason at `backend/app/services/job_manager.py:2531-2533`:

```python
                        await self._matching.route_rip_failure_to_review(
                            job_id,
                            stalled_title.id,
                            "rip_stalled",
                            result.failure_reason or STALL_FAILURE_REASON,
                        )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_job_manager.py::TestOnePassRipFallback -v
```

Expected: 6 passed, including the pre-existing `test_single_pass_failure_reripsonly_missing` and `test_all_selected_rips_in_single_pass`.

- [ ] **Step 6: Run the full affected suites**

```bash
uv run pytest tests/unit/test_job_manager.py tests/unit/test_rerip.py tests/unit/test_stuck_job_recovery.py tests/unit/test_auto_review_escalation.py -v
```

Expected: all pass. `test_rerip.py` and `test_auto_review_escalation.py` assert on the `rip_stalled` code, which this change deliberately preserves.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add backend/app/services/job_manager.py backend/tests/unit/test_job_manager.py
git commit -m "fix(ripping): skip per-title fallback when the pass stalled with no output (#506)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full backend verification

**Files:** none modified.

- [ ] **Step 1: Run the whole backend suite**

```bash
uv run pytest
```

Expected: no new failures relative to the branch point. If `no such table` errors appear, initialize the worktree DB first (see "Context an implementer needs") and re-run.

- [ ] **Step 2: Confirm the branch point baseline if anything fails**

If a failure looks pre-existing, verify it against main rather than assuming:

```bash
git stash && git checkout main && uv run pytest tests/unit/<failing_file> -v; git checkout - && git stash pop
```

- [ ] **Step 3: Lint**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: both clean.

---

### Task 7: Changelog

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` section)

- [ ] **Step 1: Add the entries**

Under `## [Unreleased]`, add to `### Fixed` (create the subsection if absent). Write user-facing prose, not commit subjects:

```markdown
- A disc that MakeMKV cannot read at all now resolves in about two minutes
  instead of roughly twenty-six. Previously, when the initial rip pass stalled
  without writing anything, engram retried every title individually, spending a
  full stall timeout on each before the phase watchdog finally stepped in. (#506)
- A rip that stalls because the drive's region setting does not match the disc
  now says so, instead of reporting that the disc may be dirty or damaged. (#506)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note fast-fail and region-mismatch reporting (#506)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Open the PR

- [ ] **Step 1: Stop any servers this session started**

Per CLAUDE.md, servers must be stopped before opening a PR. This plan starts none, but confirm nothing is stray:

```powershell
Get-Process -Name makemkvcon* -ErrorAction SilentlyContinue
```

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/506-fast-fail-zero-output-rip
```

Then open a PR whose body covers: the 26-minute timeline reconstruction (all-pass stall, then the serial per-title fallback, then the 1200 s phase watchdog); the three changes; the note that `fix/506-watchdog-cancel-mislabel` has not landed and touches nearby regions of `_run_ripping`; and that this does not fix the `"Cancelled by user"` mislabel.

- [ ] **Step 3: Request review**

Per project convention, `code-review.yml` only fires on the `opened` event, so the bot must be pinged explicitly:

```bash
gh pr comment <PR#> --body "@claude please review this PR"
```

---

## Self-Review Notes

- **Spec coverage:** change 1 is Task 5; change 2 is Tasks 2 and 4; change 3 is Tasks 1, 3, and 4. All three spec testing sections map to tests in Tasks 1 to 5. The spec's "reuse `rip_stalled`" constraint is asserted in Task 5 Step 1.
- **Known risk carried from the spec:** Task 4 Step 6 requires hoisting `region_mismatch` out of `run_rip_with_streaming`'s scope. If that hoist proves awkward, the fallback is to return the reason out of `run_rip_with_streaming` alongside `stalled_commands` (it already returns a 3-tuple; make it a 4-tuple). Either is acceptable; do not leave the variable referenced from a scope that cannot see it.
- **Test-count expectations** in each "Expected" line assume the tests are added cumulatively in the order given. If running a single class, the counts differ.
</content>
</invoke>

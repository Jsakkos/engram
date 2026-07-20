# Fast-fail a rip that produces no output

Date: 2026-07-19
Issue: [#506](https://github.com/Jsakkos/engram/issues/506)
Branch: `feat/506-fast-fail-zero-output-rip`

## Problem

A disc that MakeMKV cannot read at all still occupies engram for roughly 26
minutes before the job resolves. Jobs 47 and 48 (engram 0.25.0, Linux, MakeMKV
1.18.3, the same `SINGIN_IN_THE_RAIN` DVD) both ran that long and produced zero
output files. The runs were byte-identical in failure shape, so this is
deterministic, not a flaky disc.

The root cause was a DVD region mismatch. The disc scan completed normally (1175
log lines, all titles enumerated, no error MSGs), but every rip invocation died
during the disc-open "Scanning contents" phase at `PRGV:14417,11915,65536`
(about 22%) after 21 occurrences of:

```
MSG:3032,0,2,"Region setting of drive ASUS:BW-16D1HT does not match the region of
currently inserted disc, trying to work around..."
```

### Where the time actually goes

The phase timeout is not the mechanism that burns the time. It is the mechanism
that finally stops it.

1. The `rip_all` single pass hangs at disc-open and is killed by the 120 s stall
   watchdog (`ripping_stall_timeout`).
2. Because that pass left titles missing, `_run_ripping` enters the **per-title
   fallback** (`job_manager.py:2485-2508`) and re-rips every missing title
   individually and serially. Each invocation re-opens the disc, hits the same
   deterministic region wall, and burns its own full 120 s stall timeout.
3. That serial burn continues until the 1200 s `timeout_ripping_seconds` watchdog
   fires, calls `reconcile_and_advance`, and cancels the rip task.

Two existing design decisions make this worse than it looks:

- **The stall clock is fed by stdout, not by bytes.** `extractor.py:836` treats
  any `PRGV:`, `PRGC:`, or `PRGT:` line as liveness. A disc looping "trying to
  work around..." keeps MakeMKV chattering, so the 120 s timer only starts
  counting once MakeMKV goes fully silent.
- **The watchdog has no independent heartbeat during ripping.**
  `_filesystem_progress_monitor` calls `_note_activity` only when an output file
  actually grows (`job_manager.py:2345`). Zero bytes written means the watchdog
  clock never advances from rip start, so `timeout_ripping_seconds` fires on
  schedule regardless of how many stalls preceded it.

Separately, `STALL_FAILURE_REASON` (`extractor.py:29`) says the disc may be dirty
or damaged, which is actively misleading for a region-locked disc that is
physically perfect. The reporter was left guessing.

## Non-goals

- **No new timeout config knob.** A dedicated "produced no output whatsoever"
  ceiling was considered and rejected: it adds a third timeout to reason about
  alongside `ripping_stall_timeout` and `timeout_ripping_seconds`, when the fixes
  below make the ceiling irrelevant by not spending the time in the first place.
- **Not fixing the watchdog-cancel mislabel.** A job cut off by
  `reconcile_and_advance` currently records `error = "Cancelled by user"`. That is
  a real bug and the reason issue #506 was confusing, but it is separate work
  (`fix/506-watchdog-cancel-mislabel`, not yet landed as of main `8e3021f6`).
  This change makes that path far less likely to be reached; it does not correct
  the label.
- **No change to stall detection itself.** Feeding the stall clock from stdout is
  deliberate: it prevents false positives on tiny tracks being finalized. We work
  with that behavior rather than changing it.

## Design

Three changes, ordered by how much time each saves.

### 1. Skip the per-title fallback when the pass produced nothing (`job_manager.py`)

The per-title fallback exists to recover the "one bad title lost the rest of the
disc" case. That premise requires partial success worth salvaging. When the
all-pass stalled and produced zero output files, there is no partial success: the
disc itself is the problem, and re-opening it once per title is guaranteed waste.

Skip the fallback only when both conditions hold:

- the all-pass reported stalled titles (`result.stalled_titles` is non-empty), and
- the all-pass produced zero output files.

**Both conditions are required.** A pass that returns `success=True` with an empty
`output_files` list has produced no evidence that the disc is unreadable, so a
per-title retry is still legitimate there. Only a stall is positive evidence that
MakeMKV could not read the disc. This distinction is load-bearing: the existing
regression test `test_single_pass_failure_reripsonly_missing`
(`tests/unit/test_job_manager.py:908-923`) asserts that a zero-output pass with no
stall still triggers the fallback, and it must keep passing.

When both hold, skip the fallback and route every still-missing title straight to
review via the existing stall-routing path.

This is the single largest saving. It removes the entire serial burn rather than
trimming it, taking job 47's shape from about 26 minutes to roughly the 2 minutes
of the initial all-pass stall.

### 2. Zero-output circuit breaker in the extractor command loop (`extractor.py`)

Change 1 only covers the all-pass to fallback transition. The same serial burn is
reachable from `rerip_titles` (a multi-title review-driven re-rip) and from any
explicit `title_indices` list. Add the guard at the source, inside
`run_rip_with_streaming`'s command loop, so every caller benefits.

After a command is terminated for stalling, trip a breaker when both of these
hold:

- the number of stalled commands in this invocation has reached
  `ZERO_OUTPUT_STALL_LIMIT` (a module constant, value 2), and
- the invocation has produced zero completed output files so far.

On trip: stop issuing further commands, record the remaining command indices as
stalled, and fire `title_error_callback` for each so their titles route to review
on the same path as a genuine stall. Bookkeeping stays consistent, so a caller
cannot tell an abandoned command from a stalled one.

The threshold is a module constant, not an `AppConfig` field. This codebase
requires a new config field to land in `AppConfig`, `ConfigUpdate`,
`ConfigResponse`, and `ConfigWizard` or Pydantic silently drops it, and users have
no basis on which to tune this number.

**Why an aggressive threshold of 2 is safe:** abandoning is not lossy. Every
skipped title routes to `REVIEW` through
`route_rip_failure_to_review(..., "rip_stalled", ...)` as re-rippable, and
`rerip_title_manual` already provides a manual path back. The worst case, a disc
whose first two titles are bad extras but whose later titles are fine, costs
review-queue entries the user can re-rip, not data.

### 3. Surface the region mismatch (`extractor.py`)

`MSG:3032` is a specific, actionable diagnosis that is currently invisible. Detect
it in the existing stdout reader loop, alongside the progress-code and
created-file handling already there, and set a per-invocation flag.

When the flag is set, the reason passed to `title_error_callback` and carried on
`RipResult` becomes a region-specific message instead of `STALL_FAILURE_REASON`:

> Ripping stalled: the drive's region setting does not match this disc's region,
> so MakeMKV could not open the disc. Set the drive's region to match the disc, or
> use a region-free drive.

Implementation notes:

- Add `failure_reason: str | None = None` to `RipResult`, carrying the specific
  reason. The fallback stall-routing in `job_manager.py` (currently hardcoding
  `STALL_FAILURE_REASON` at `job_manager.py:2531-2533`) reads it so the live
  per-title update and the History entry agree.
- **Reuse the existing `rip_stalled` error code.** Do not introduce a new one. New
  REVIEW error codes must be added to `_NON_REMATCHABLE_REVIEW_ERRORS`
  (`finalization_coordinator.py:222`) or auto-escalation overwrites
  `match_details`. Reusing `rip_stalled` keeps the existing re-rip eligibility and
  escalation behavior and avoids that trap entirely.
- Detection is a substring test for `MSG:3032,` on the raw line. Robot mode emits
  `MSG:3032,0,2,"..."`.

## Data flow

```
makemkvcon stdout
  |- PRGV:/PRGC:/PRGT:  -> last_progress bump (unchanged)
  |- MSG:3032           -> region_mismatch flag        [new, change 3]
  '- "... created ..."  -> completion.seed (unchanged)

stall watchdog fires
  -> title_error_callback(cmd_idx, region-aware reason) [change 3]
  -> stalled_commands.add(cmd_idx)
  -> breaker check: stalls >= 2 AND zero output?        [new, change 2]
       yes -> mark remaining commands stalled, fire callbacks, stop loop
       no  -> continue to next command

RipResult(stalled_titles=[...], failure_reason=...)     [change 3]
  -> _run_ripping: all-pass stalled AND zero files?     [new, change 1]
       yes -> skip per-title fallback
       no  -> per-title fallback as today
  -> route each stalled title to REVIEW (rip_stalled, region-aware reason)
```

## Testing

Backend tests, `backend/tests/unit/`.

**Circuit breaker (change 2).** Drive the extractor with a stubbed
`subprocess.Popen` so no real MakeMKV is involved:

- Two stalled commands with zero output: remaining commands are not executed, and
  every remaining command index appears in `stalled_titles`.
- Two stalled commands with zero output: `title_error_callback` fires once per
  abandoned command, so no title is left stranded in `RIPPING`.
- Stalls but output files exist: the breaker does not trip and the loop runs to
  completion. Guards the "one bad title, rest of disc fine" case.
- A single stall with zero output: does not trip, since the threshold is 2.

**Fallback skip (change 1).** Exercise `_run_ripping` with a stubbed extractor:

- All-pass stalls with zero output files: `rip_titles` is called exactly once (no
  fallback invocation), and all selected titles land in `REVIEW` with
  `rip_stalled`.
- All-pass stalls but produced some output files: the fallback still runs,
  preserving today's recovery behavior for a partially readable disc.
- All-pass returns `success=True` with zero output files and no stall: the
  fallback still runs. This is the existing
  `test_single_pass_failure_reripsonly_missing` case and must not regress.

**Region mismatch (change 3):**

- Feeding `MSG:3032,0,2,"Region setting..."` through the reader sets the flag, and
  the resulting `RipResult.failure_reason` is the region message.
- Without `MSG:3032`, the reason remains `STALL_FAILURE_REASON`.
- The routed review entry carries error code `rip_stalled` (not a new code) with
  the region message as its reason.

Existing tests that must keep passing: `tests/unit/test_rerip.py` (asserts
`rip_stalled` routing), `tests/unit/test_job_manager.py:522-540` (stall to
REVIEW), and `tests/unit/test_stuck_job_recovery.py` (phase-timeout behavior,
unchanged).

Run from `backend/`: `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.

## Risks

- **Overlap with `fix/506-watchdog-cancel-mislabel`.** That branch touches
  `reconcile_and_advance` and `_run_ripping`'s `CancelledError` handler. Change 1
  edits `_run_ripping`'s fallback block, a different region of the same function.
  Conflicts are likely mechanical but should be expected on rebase.
- **A disc whose first two titles legitimately stall.** Mitigated by requiring
  zero output overall, and bounded by the fact that abandonment routes to review
  rather than discarding.
</content>
</invoke>

# State Machine

Engram uses explicit state machines to track both job-level and title-level processing. Every state transition is validated, persisted to SQLite, and broadcast to connected clients over WebSocket.

## Job States

A `DiscJob` moves through the following states during its lifecycle:

| State | Value | Description |
|-------|-------|-------------|
| **IDLE** | `idle` | Job created, waiting to start |
| **IDENTIFYING** | `identifying` | Scanning disc structure, classifying content type |
| **REVIEW_NEEDED** | `review_needed` | Human intervention required (low confidence, ambiguous content) |
| **RIPPING** | `ripping` | Active extraction via MakeMKV |
| **MATCHING** | `matching` | Episode matching (transcript against reference subtitles) |
| **ORGANIZING** | `organizing` | Moving files from staging to the media library |
| **COMPLETED** | `completed` | Terminal state -- all processing finished successfully |
| **FAILED** | `failed` | Terminal state -- processing failed with an error |

### Job State Diagram

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> IDENTIFYING
    IDLE --> FAILED

    IDENTIFYING --> RIPPING : High confidence
    IDENTIFYING --> REVIEW_NEEDED : Low confidence / ambiguous
    IDENTIFYING --> FAILED

    REVIEW_NEEDED --> RIPPING : User confirms
    REVIEW_NEEDED --> COMPLETED : User skips
    REVIEW_NEEDED --> FAILED

    RIPPING --> MATCHING : TV content
    RIPPING --> ORGANIZING : Movie content (no matching needed)
    RIPPING --> REVIEW_NEEDED : Ambiguous results
    RIPPING --> COMPLETED
    RIPPING --> FAILED

    MATCHING --> ORGANIZING : Matches resolved
    MATCHING --> REVIEW_NEEDED : Low-confidence matches
    MATCHING --> COMPLETED
    MATCHING --> FAILED

    ORGANIZING --> REVIEW_NEEDED : Conflict resolution needed
    ORGANIZING --> COMPLETED : Files organized
    ORGANIZING --> FAILED

    COMPLETED --> [*]
    FAILED --> [*]
```

### Valid Transitions

The `JobStateMachine` enforces these transitions. Any attempt to perform an invalid transition is rejected and logged as a warning.

| From State | Valid Next States |
|------------|-------------------|
| `IDLE` | `IDENTIFYING`, `FAILED` |
| `IDENTIFYING` | `RIPPING`, `REVIEW_NEEDED`, `FAILED` |
| `REVIEW_NEEDED` | `RIPPING`, `COMPLETED`, `FAILED` |
| `RIPPING` | `MATCHING`, `ORGANIZING`, `REVIEW_NEEDED`, `COMPLETED`, `FAILED` |
| `MATCHING` | `ORGANIZING`, `REVIEW_NEEDED`, `COMPLETED`, `FAILED` |
| `ORGANIZING` | `REVIEW_NEEDED`, `COMPLETED`, `FAILED` |
| `COMPLETED` | *(none -- terminal state)* |
| `FAILED` | *(none -- terminal state)* |

!!! note "Same-state transitions"
    A job is allowed to "transition" to its current state (a no-op update). This is useful for progress updates that refresh the `updated_at` timestamp without changing the state.

### REVIEW_NEEDED Branching

The `REVIEW_NEEDED` state is reachable from multiple stages, each for a different reason:

- **From IDENTIFYING**: Classification confidence is too low. The Analyst or TMDB Classifier could not determine whether the disc is TV or movie with sufficient certainty.
- **From RIPPING**: Ambiguous results during extraction (e.g., unusual title structure).
- **From MATCHING**: Episode matching produced low-confidence results or competing candidates for the same episode slot.
- **From ORGANIZING**: A file conflict requires user resolution (when `conflict_resolution_default` is set to `"ask"`).

When a job enters `REVIEW_NEEDED`, it appears in the Review Queue on the frontend. After the user resolves the issue, the job transitions to `RIPPING` (to continue processing) or `COMPLETED` (if the user chooses to skip).

### Mid-rip eject

`POST /api/jobs/{id}/eject` releases the disc without cancelling the job. It is
accepted only in `IDENTIFYING` and `RIPPING` (the states that hold the drive)
and returns 409 otherwise, including for manual-import jobs, which carry
`drive_id == "import"` and hold no drive at all.

While `RIPPING` there is no state transition of its own. `JobManager.eject_disc_for_job`
aborts the MakeMKV pass and opens the tray; the in-flight rip returns
`RipResult(aborted_for_eject=True)`, and `_run_ripping` then:

1. routes every still-unfinished selected title to `REVIEW` with
   `match_details.error = "rip_ejected"` and `rerip_eligible = true`,
2. skips the per-title fallback pass (the disc is gone, so each retry would
   cost a full stall timeout),
3. skips the auto-eject block (already ejected; re-issuing would reset sentinel
   state spuriously),
4. falls through to the normal post-rip flow, so the job settles in
   `REVIEW_NEEDED` via the usual completion check.

Two ordering constraints hold this together, both covered by regression tests:

- **The routing runs before `reconcile_stuck_titles`.** That function marks a
  fileless `PENDING`/`RIPPING` title `FAILED`, and because the abort deletes the
  truncated in-flight file, an interrupted track and a never-started track are
  indistinguishable to it. Routing afterwards would make every unfinished track
  `FAILED` and non-re-rippable, inverting the feature.
- **The routing skips titles that already have complete output.** The final
  completion poll fires its callback asynchronously, so a track that finished
  just before the abort can still read `RIPPING` while its `.mkv` is on disk.
  Without the check it would be parked in review telling the user to re-rip a
  track that ripped perfectly.

`"rip_ejected"` is registered in `RIP_FAILURE_ERROR_CODES`, which grants
re-rip eligibility and excludes it from auto re-match. The frontend keeps its
own copy of that set in `frontend/src/components/ReviewQueue/rerip.ts`; both
must be updated together or the Re-rip control never renders.

During `IDENTIFYING` the same endpoint ejects and then cancels the job, because
nothing has been produced to salvage.

### FAILED Branching

The `FAILED` state is reachable from every non-terminal state. Common failure causes:

- MakeMKV subprocess errors during scanning or ripping
- Database commit failures
- External API errors (TMDB, TheDiscDB) when no fallback is available
- File system errors during organization

When a job fails, the `error_message` field is populated with the reason, and `completed_at` is set to the current timestamp.

---

## Title States

Individual titles (tracks) on a disc have their own state machine tracked by `TitleState`:

| State | Value | Description |
|-------|-------|-------------|
| **PENDING** | `pending` | Title discovered, waiting to be processed |
| **RIPPING** | `ripping` | Currently being extracted by MakeMKV |
| **MATCHING** | `matching` | Episode matching in progress |
| **MATCHED** | `matched` | Successfully matched to an episode but not yet organized |
| **REVIEW** | `review` | Ripped successfully but needs human review for episode assignment |
| **COMPLETED** | `completed` | Organized into the media library |
| **FAILED** | `failed` | Processing failed for this title |

### Title State Diagram

```mermaid
stateDiagram-v2
    [*] --> PENDING

    PENDING --> RIPPING

    RIPPING --> MATCHING : TV content
    RIPPING --> MATCHED : Movie (no matching needed)
    RIPPING --> FAILED

    MATCHING --> MATCHED : High confidence
    MATCHING --> REVIEW : Low confidence
    MATCHING --> FAILED

    MATCHED --> COMPLETED : Organized to library
    MATCHED --> FAILED

    REVIEW --> MATCHED : User assigns episode
    REVIEW --> FAILED

    COMPLETED --> [*]
    FAILED --> [*]
```

---

## Terminal State Callbacks

The `JobStateMachine` supports registering callbacks that fire when a job reaches a terminal state (`COMPLETED` or `FAILED`). These are used for cleanup operations.

```python
state_machine.on_terminal_state(callback)
# Callback signature: async def callback(job_id: int, state: JobState) -> None
```

**Current uses:**

- **Staging cleanup**: When a job completes or fails, its staging directory is cleaned up according to the configured policy (immediate delete, move to trash, or retain for a configurable period).
- **completed_at timestamp**: Automatically set in the `transition()` method when entering `COMPLETED` or `FAILED`.

---

## State Transition Validation

The `JobStateMachine.transition()` method performs the following steps for every state change:

1. **Validate** -- Check the transition against the `VALID_TRANSITIONS` map. If invalid, log a warning and return `False`.
2. **Update** -- Set the new state and `updated_at` timestamp on the job. If transitioning to `FAILED`, set `error_message`. If transitioning to a terminal state, set `completed_at`.
3. **Persist** -- Commit the change to the database via the async session.
4. **Broadcast** -- Send the appropriate WebSocket message to all connected clients. Broadcasting failures are non-fatal since the database is already committed.
5. **Callbacks** -- Fire any registered terminal-state callbacks for `COMPLETED` or `FAILED` transitions.

### Convenience Methods

The state machine provides convenience methods for common transitions:

| Method | Target State | Extra Behavior |
|--------|-------------|----------------|
| `transition_to_failed()` | `FAILED` | Requires `error_message` parameter |
| `transition_to_review()` | `REVIEW_NEEDED` | Optionally sets `review_reason` on the job |
| `transition_to_completed()` | `COMPLETED` | Sets `completed_at` automatically |

### Introspection

```python
# Check if a transition is allowed
can_proceed = state_machine.can_transition(JobState.RIPPING, JobState.MATCHING)

# Get all valid next states from current state
next_states = state_machine.get_next_states(JobState.IDENTIFYING)
# Returns: {JobState.RIPPING, JobState.REVIEW_NEEDED, JobState.FAILED}
```

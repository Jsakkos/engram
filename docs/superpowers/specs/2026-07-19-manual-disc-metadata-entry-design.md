# Manual Disc Metadata Entry — Design

**Date**: 2026-07-19
**Status**: Draft, pending user review
**Origin**: [Issue #520](https://github.com/Jsakkos/engram/issues/520) (feature request), plus a
direct request for a manual-entry escape hatch for edge-case discs.

## Problem

Engram identifies a disc automatically: MakeMKV scan, then the Analyst heuristics and the
TMDB classifier resolve a show/movie name, content type, and season. For most discs this
works and the walk-away workflow (v0.21.0, PR #398) means a disc rips immediately even when
identification is uncertain, carrying a non-blocking prompt that pools into a single review
at the end.

There is no way for a user to assert the identity themselves. When a disc is a known edge
case (a homemade compilation, an obscure release, a catalog-number label, a foreign title,
a box set with a label TMDB cannot resolve) the user must let Engram guess wrong first, then
correct it through a review or re-identify prompt. The correction path exists but is
strictly reactive.

Note on issue #520: the reporter's premise ("the system rips all titles first and only
attempts to identify the series afterward... the process halts midway") is largely outdated.
Identification runs before ripping, and the walk-away gates removed the mid-process halt for
the four common identification-failure cases over a month before the issue was filed. The
legitimate underlying ask, reframed, is proactive control over identity, which is what this
design provides.

## Key existing behavior this design relies on

`POST /api/jobs/{job_id}/re-identify` (`routes.py:1301`) already accepts
`{title, content_type, season, tmdb_id}` and is explicitly valid in **both** `REVIEW_NEEDED`
and `RIPPING` states, dispatching identity-parked titles without interrupting an in-flight
rip. `GET /api/tmdb/search` (`routes.py:1330`) already backs a live show/movie autocomplete,
and `ReIdentifyModal.tsx` already renders that search plus the title/type/season fields.

So the "edit a live job" half of this feature is mostly already built on the backend. The
frontend gap is a single condition at `App.tsx:743`:

```js
onReIdentify={disc.needsReview && disc.title ? () => {...} : undefined}
```

The "Wrong title?" control only renders when the job needs review. Relaxing this condition
is most of the work for entry point 2.

`DiscJob.classification_source` (`disc_job.py:66`) is already a plain string defaulting to
`"heuristic"`, so recording manual provenance needs no new column.

## Scope

In scope:

- **Identity only**: show/movie name, content type, season, and optional `tmdb_id`. Two entry
  points, one shared modal.
- **Entry point 1, pre-insert (arm)**: the user enters metadata *before* inserting, the next
  disc in that drive adopts it, and the job rips unattended with no prompts.
- **Entry point 2, live card edit**: an always-on identity control on active job cards,
  routed through the existing re-identify endpoint.
- Provenance display so a manually identified job is visibly distinct.

Out of scope (explicitly not building):

- **Manual title-to-episode mapping.** Episode matching still runs automatically on a
  manually identified disc. Anything matching cannot resolve falls into the existing Review
  Queue, which already does hand-assignment. No duplicate UI.
- **A park-and-wait manual mode.** Metadata is entered before insertion only. There is no
  variant where a disc scans and then waits for the user to name it, because that reintroduces
  the mid-process halt this project spent PR #398 removing.
- **A global "always ask me" setting.** This is an edge-case escape hatch, not a new default.
- **Persistence of the armed state across a backend restart.** In-memory only.
- Any change to matching, organizing, or the Review Queue.

## Decisions

These were settled during brainstorming and should be confirmed before planning:

| Decision | Choice |
|---|---|
| Depth of manual override | Identity only; Review Queue handles episode mapping |
| Pre-insert timing | Enter metadata before insert; no park-and-wait branch |
| Identity button label | Contextual: "Wrong title?" on auto-identified jobs, "Edit ID" on manual ones |
| Armed drive expiry | No timer. Cleared by disc insertion, explicit disarm, or backend restart |
| Armed state persistence | In-memory; a restart is the only implicit clear |
| "Manual" button placement | Top bar, beside IMPORT |

## Backend design

### Provenance marker

`DiscJob.classification_source` records how identity was obtained. It is already a
fine-grained descriptive taxonomy (`heuristic`, `tmdb`, `tmdb+heuristic`, `ai`,
`fingerprint_network`, `discdb_*`, `staging_import`), so this design adds two values in the
same spirit rather than collapsing both entry points into one:

- `"manual"`: identity asserted up front via the arm path, never guessed.
- `"manual_correction"`: the user overrode an automatic guess from a live card.

The value is **descriptive only**. No control flow may branch on it. The manual identify
branch is selected by the presence of an armed payload, not by reading this string back,
which keeps a diagnostic label from silently becoming load-bearing. The field is already
serialized into the DiscDB export (`discdb_exporter.py:168`) and diagnostic snapshots, so the
distinction pays for itself in later debugging at no cost.

The UI renders a single `MANUAL ID` chip for both values. The distinction is for diagnostics,
not for the user. No schema change.

### Arm store

An in-memory dict on `JobManager`, keyed by drive id, holding one payload
(`title`, `content_type`, `season`, `tmdb_id`, optional `disc_number`). One-shot: consumed by
the next insert on that drive. This mirrors the existing `_last_job_created_at` pattern in
`job_manager.py`. A backend restart clears it, which is the accepted expiry mechanism.

### Endpoints

- `POST /api/manual/arm` with the payload above plus a target `drive_id`. Rejects with 409 if
  that drive already holds an active job (the caller should edit that job instead).
- `POST /api/manual/disarm` with a `drive_id`.
- New WebSocket event `drive_armed` carrying the payload, or a cleared form on disarm, so the
  dashboard chip stays in sync across clients and reloads.

### Insert hook

In `_handle_disc_inserted` (`job_manager.py:635`), after the `DiscJob` row is created and
before the identify task is spawned, pop any armed payload for that drive. If present, stamp
the job with the manual metadata plus `classification_source="manual"` and spawn the manual
identify path. If absent, today's `identify_disc` call is untouched. Every existing guard
(the drive-occupied check, the 15 second cooldown, the re-rip lookup) runs unchanged before
this point.

### Manual identify path

An early branch inside `identify_disc`, placed after the MakeMKV scan and after the
`DiscTitle` rows are persisted and broadcast, but before the classification block. A separate
coroutine would have to duplicate the scan, persist, and broadcast logic, so a branch is
preferred. The branch:

1. Runs the MakeMKV scan as normal. This is still required: the manual payload supplies
   identity, not the disc's title list, durations, or sizes.
2. Skips `_run_classification` entirely (no TMDB lookup, no Analyst TV/movie clustering, no
   DiscDB identity lookup).
3. Applies `detected_title`, `content_type`, `detected_season` from the payload. Resolves
   `tmdb_id` from the payload if the user picked an autocomplete row; otherwise attempts a
   best-effort TMDB search by name; otherwise leaves it `None`.
4. Runs the existing title-selection heuristics unchanged. Play-All detection, extras
   tagging, and main-feature selection depend on durations and DiscDB mappings, not on how
   identity was obtained.
5. Starts subtitle prefetch and transitions straight to `RIPPING`.

Critically, **none of the walk-away identity gates (A unreadable label, B TMDB lookup failed,
C same-name collision, D unknown season) and no collision backstop may fire on a manual job.**
The user has asserted identity; a manual disc must never park or raise an identity prompt.

### Card edit path

Reuses `POST /jobs/{job_id}/re-identify`. The one backend change is widening its
accepted-state check from `{REVIEW_NEEDED, RIPPING}` to also include `IDENTIFYING`, so the
control is usable for the whole window the UI offers it. Submitting from a live card sets
`classification_source="manual"`, since the resulting identity is user-asserted regardless of
entry point.

## Frontend design

Mockup: `docs/design_handoff_synapse/explorations/2026-07-19-manual-disc-metadata-entry.html`,
rendered against the real Synapse v2 tokens.

1. **`ManualIdentityModal`**, generalized from `ReIdentifyModal`. Content-type segmented
   control, title with TMDB autocomplete, season, and an optional disc-number field. Two
   modes differing only in header text and primary button label: "Arm drive" and
   "Save & re-match".

   The disc-number field is load-bearing, not decoration. `disc_number` is normally parsed
   from the volume label by regex and defaults to `1` when that fails, and `organizer.py:619`
   names extras `"{show} Disc {disc_number} Extra t{NN}.mkv"`. On a multi-disc box set with
   unreadable labels, which is precisely this feature's target case, every disc would default
   to `1` and their extras would collide on disk. The field prevents that.
2. **Top bar**: a magenta `MANUAL` button beside `IMPORT` in `SvTopBar.tsx`, following the
   existing IMPORT button's mono/uppercase/1px-border vocabulary.
3. **Armed card**: a dashed, glow-less panel occupying a dashboard card slot, showing the
   locked identity, the target drive, and a disarm control.
4. **Provenance chip**: a `MANUAL ID` chip in the card header for jobs with
   `classification_source="manual"`, styled like the existing `DAMAGED TRACK` badge.
5. **Always-on identity control**: relax `App.tsx:743` so `onReIdentify` is passed for active
   states (`scanning`, `ripping`, `review_needed`) whenever a title exists, with the label
   contextual on provenance. Deliberately excluded: `matching` and `organizing` (work in
   flight) and `completed` (the History page's `AmendTitleModal` already covers it).

## Edge cases

| Case | Behavior |
|---|---|
| Arm a drive that already holds an active job | 409; UI routes the user to that job's edit instead |
| A different disc than intended is inserted | It adopts the armed identity. That is the contract; disarm or edit the card to correct |
| Freeform title with no TMDB match | Allowed. `tmdb_id` stays null, matching falls back to name-keyed lookup, Review Queue catches misses |
| Backend restarts between arm and insert | Arm is lost. Accepted, and the only expiry mechanism |
| Manual content type contradicts the disc | The user's assertion wins. Correctable via card edit |
| Manual movie | Season field hidden; main-feature selection unchanged |
| Multiple optical drives | Arm targets one drive id; default to the sole detected drive when there is only one |

## Testing

Backend unit:
- Manual identify applies the payload and does not call the classifier.
- No identity prompt or collision gate fires on a manual job.
- Arm store is one-shot and drive-scoped.
- `tmdb_id` resolution: payload value wins, name search is the fallback, null is tolerated.
- `classification_source` persists as `"manual"` through both entry points.

Backend integration:
- Arm, then simulated insert, yields a job that reaches `RIPPING` with the asserted identity
  and a null `identity_prompt_json`.
- Arm rejected while the drive is occupied.
- Re-identify during `RIPPING` still dispatches parked titles (existing coverage).

Frontend unit (vitest, colocated):
- Modal renders arm vs edit mode correctly and submits the right payload.
- Armed card renders and disarms.
- Identity button visibility and label per state and provenance.

E2E: the simulation endpoints need an arm-aware path so
`POST /api/simulate/insert-disc` can exercise arm, insert, and unattended rip.

## Resolved during review

- **Disc-number field: keep it.** It prevents an extras filename collision on multi-disc sets
  with unreadable labels. See the frontend section for the mechanism.
- **Provenance values: two, not one.** `"manual"` and `"manual_correction"`, descriptive only,
  rendered as one chip. See the provenance marker section.

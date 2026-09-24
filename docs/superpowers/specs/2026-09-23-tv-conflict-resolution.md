# TV library conflicts: explicit per-track choice only

Follow-up to #685. Status: decided, implemented alongside this spec.

## Problem

#685 made a movie review able to answer a FILE_EXISTS conflict (`overwrite` / `rename` /
`skip`), falling back to `conflict_resolution_default`. The TV organizes never pass a strategy,
so a TV track whose target already exists can only be resolved by reassigning it, marking it an
extra, or discarding it. There is no way to say "this rip is the good one, replace the library
copy" (the re-rip-a-damaged-disc case).

TV organize sites (keep in sync):

| Site | Path |
|------|------|
| `FinalizationCoordinator.finalize_disc_job` | automatic end-of-disc organize (captured dicts) |
| `FinalizationCoordinator._finalize_tv_if_resolved` | after the last review decision |
| `FinalizationCoordinator.process_matched_titles` | "organize matched" action |
| `JobManager.amend_title_assignment` | correcting a COMPLETED job; hard-codes `"ask"`, unchanged |

`MatchingCoordinator._handle_extras` no longer organizes: extras defer to the end-of-disc
finalize (#449). `organize_tv_extras` has no strategy parameter; extras are named by title
index, so a collision there means the same disc was ripped twice. Out of scope.

## Decision

1. **The configured default never applies to TV.** A movie FILE_EXISTS is almost always "I
   have this movie already" and the default answers that. A TV FILE_EXISTS is usually a
   duplicate track (play-all, alternate angle) or a mis-matched episode. `overwrite` as a
   default would replace a correct episode with a wrong one with no signal at all, and
   `rename` would scatter `S01E03 (v2).mkv` duplicates that Plex shows as versions. The
   default stays movie-only.
2. **An explicit per-track choice is honored**, read from `DiscTitle.conflict_resolution`
   (already written by `apply_review` for any content type). Only `overwrite` and `rename`
   reach the TV organizer; anything else is `"ask"`.
3. **TV `skip` is Discard.** "Keep the library copy, drop this rip" is exactly what
   `episode_code="skip"` already does (title FAILED, file left in staging, job resolves).
   `apply_review` translates it, so the organize sites never see a skip result and no new
   result branch is needed.
4. **A choice is tied to its target.** Reassigning a track to a different episode (or a movie
   to a different edition) clears a recorded choice, because it answered a conflict on the old
   path. Without this, "Replace" chosen for S01E03 would silently replace S01E05 after a later
   reassignment in the batch flow, where the organize waits until every track is resolved. A
   choice sent in the same request as the reassignment is applied after the clear, so it still
   holds.

## Not in scope

- A TV conflict notice in the review UI. The API accepts `conflict_resolution` per track now;
  the TV review page does not send it yet.
- `amend_title_assignment` keeps `"ask"`: it corrects a completed job and should fail loudly
  rather than replace library files.

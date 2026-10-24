# Pre-rip short-title skip

**Request (Discord, Jahan):** a setting to skip video files shorter than N minutes, so
5-12 minute bonus tracks on a disc of 30-40 minute episodes are not ripped without
someone hitting SKIP on each before the rip reaches it.

## Why not a minutes setting

A fixed cutoff is wrong for a whole class of shows: an 11-minute cartoon's episodes
sit under any cutoff that catches a drama's featurettes. And `extras_policy = skip`
already existed, but only acted in matching (`_handle_extras`), after the bytes had
been ripped.

## Design

`app/core/episode_runtime.find_short_titles` is a pure function, called from
`IdentificationCoordinator._skip_short_titles_before_rip` right after the identity
collision flags are computed (gates A and B have already returned: identity-unknown
discs rip permissively by design).

Two signals; a track is skipped only when **every available** signal agrees:

| Signal | Rule |
|---|---|
| TMDB | under the season's SHORTEST runtime minus `EPISODE_DURATION_UNDER_TOLERANCE_MIN` (5). Lower bound only; long tracks are pilots or conjoined segments. |
| Disc | under 50% of the disc's episode group (40% with no TMDB, and the group then needs 3 tracks, not 2). |

The **episode group** is not a plain median: a disc of 2 episodes + 6 featurettes has a
featurette median. Group similar durations (+-120 s, the analyst's TV variance);
with TMDB runtimes prefer the largest group whose length is a plausible episode
(single or conjoined); otherwise the largest group, ties to the longer.

Asymmetric cost drives the conservatism: a wrong skip means re-inserting an ejected
disc, a wrong keep costs rip time and is still caught post-rip. So: no signal, no
skip; fewer than 3 candidate tracks, no skip; a rule that would skip every
candidate skips nothing.

## Decisions

- **Rides on `extras_policy = skip`**, no new setting. `keep`/`ask` rip everything.
- **TMDB-only is allowed** (no disc group): the margin under the shortest runtime is
  already conservative.
- **Runtimes are re-fetched against the job's FINAL identity** (`lru_cache`d), not
  carried from classification, which fetched for the label's identity before
  disc-network/DiscDB overrides. **Untrusted identities** (same-name collision,
  no-year twin, uncorroborated) skip the TMDB signal and use disc evidence only.
- Skipped tracks get the manual-skip state (`SKIPPED`, deselected,
  `match_details.skipped`) plus `auto_skipped` and a reason, so the existing UN-SKIP
  works and finalization already treats them as terminal and unmatchable.
- Dashboard: per-track "AUTO-SKIPPED" with the reason, plus a card-level count,
  because a wrong skip is only cheap to undo while the disc is in the drive.
- `duration_matches_episode_runtime` / `conjoined_episode_count` moved from
  `matching_coordinator` into `episode_runtime` so the pre- and post-rip gates share
  one set of windows.

## Out of scope

Movies (main-feature selection already handles them), staging imports (files already
exist), the rip-first gates A/B (no usable identity).

## Tests

- `tests/unit/test_episode_runtime.py`: the rule, on real disc shapes (Jahan's disc,
  extras-heavy disc, Gilmore long episode, DS9 double pilot, cartoon conjoined and
  split segments, missing/zero runtimes, never-skip-everything).
- `tests/unit/test_identify_short_title_skip.py`: wiring through `identify_disc`
  (policies, movie, untrusted identity, un-skip of an auto-skipped track).
- `TrackGrid.test.tsx`, `adapters.test.ts`: card note, reason, stale-flag clearing.

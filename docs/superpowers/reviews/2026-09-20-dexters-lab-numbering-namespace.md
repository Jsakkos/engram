# Dexter's Laboratory beta report: the episode-numbering namespace bug

**Date:** 2026-09-20
**Reporter:** Mindlessfreak30 (Discord beta tester)
**Engram version:** 0.36.1 (frozen build, Windows 10)
**Artifacts:** diagnostic bundles for jobs 31 (Season 2, completed), 32 (Season 3, failed), 33 (Season 1, completed with wrong filenames)
**Show:** Dexter's Laboratory, TMDB id 4229, imported from a watch folder (`drive_id = "import"`)

## Reported symptoms

1. **Season 2** matched all 13 tracks automatically, no review. Filenames came out `S02E01` .. `S02E13`, which is what the tester wanted, but *not* the conjoined form (`S01E01-E02-E03`) he had been told to expect.
2. **Season 1** matched correctly, then filed under shifted numbers: the review page said `S01E01`, the file on disk is `Dexter's Laboratory - S01E19.mkv`.
3. **Season 3** failed to match a single track. All 14 parked in review; the job was then cancelled by the user.

All three are the same root cause.

## Root cause

Engram conflates two different episode-numbering universes and has no way to tell them apart.

| | Namespace | Dexter's Lab, seasons 1-4 |
|---|---|---|
| Canonical identity (`matched_episode`, fingerprint key, DiscDB export, ordering projection) | TMDB **aired** order, which for a segment show numbers each ~7-minute short | 38 / 108 / 36 / 38 |
| The matcher's actual label space (the subtitle reference corpus) | the subtitle providers' numbering, which counts 22-minute **broadcast half-hours** | 13 / 40 / 13 / 13 |

From the live published cache manifest (`subtitle-cache-latest`, content version 2026-09-20):

```json
{"tmdb_id": 4229, "name": "Dexter's Laboratory",
 "seasons": [1, 2, 3, 4],
 "episode_counts": {"1": 13, "2": 40, "3": 13, "4": 13}}
```

Those counts cannot be canonical rosters. The corpus is numbered by half-hours; Engram files it
under canonical season keys and then reads every code that comes out of it as a canonical
coordinate.

Two consequences, and every symptom falls out of them:

1. **`detected_season` from the disc label is assumed to be TMDB's aired season.** True for this
   boxset's Season 1 and Season 2, false for Season 3.
2. **`matched_episode` is assumed to be a canonical TMDB coordinate.** It is a half-hour index.
   This stays invisible until something *dereferences* the code, and the episode-ordering
   projection (#200) is the only code in the system that does.

## Evidence

### Season 1: the shift is the DVD-ordering projection, exactly

The tester has DVD ordering enabled for this show (he asked for "the joined order mentioned at
thetvdb"). `resolve_episode_group_id` picks the TMDB episode group with the most episodes among
type-3 (DVD) candidates. For show 4229 that is `The Complete Series`
(id `68017e2491d3f65c5facedc4`, 221 episodes, 7 groups), beating the 9-episode `JellyFin` group.

Rebuilding `build_projection` against live TMDB reproduces every filename in job 33:

| matched (canonical) | projection | filed as | match |
|---|---|---|---|
| S01E01 | Season 1 / 19 | `S01E19.mkv` | yes |
| S01E02 | Season 1 / 20 | `S01E20.mkv` | yes |
| S01E03 | Season 1 / 21 | `S01E21.mkv` | yes |
| S01E04 | Season 1 / 22 | `S01E22.mkv` | yes |
| S01E05 | Season 1 / 23 | `S01E23.mkv` | yes |
| S01E06 | Season 1 / 24 | `S01E24.mkv` | yes |
| S01E07 | Season 1 / 13 | `S01E13.mkv` | yes |
| S01E08 | Season 1 / 25 | `S01E25.mkv` | yes |
| S01E09 | Season 1 / 26 | `S01E26.mkv` | yes |
| S01E10 | Season 1 / 36 | `S01E36.mkv` | yes |
| S01E11 | *absent from group* | `S01E11.mkv` (fallback) | yes |
| S01E12 | Season 1 / 38 | `S01E38.mkv` | yes |
| S01E13 | Season 1 / 33 | `S01E33.mkv` | yes |

13 of 13, including the one canonical pair missing from the group, which correctly fell back to
the unprojected number. `organize_tv_episode` / `project_episode`
(`backend/app/core/organizer.py:793`) did exactly what it was designed to do. This is not a
projection bug. It is a correct dereference of a code that was never canonical.

### Season 2: right answer for the wrong reason

For canonical season 2, the same DVD group's projection is the identity over episodes 1-13
(`(2, n) -> Season 2 / n`). So the same corrupt code produced a correct-looking filename, and the
tester saw nothing wrong. The numbers agreed by coincidence, not by understanding.

The missing conjoined naming has the same origin: the references are *already* conjoined, one SRT
per half-hour, so there is only ever one candidate per track and
`multi_episode.decompose_vote_runs` can never observe the two or more contiguous vote runs it
needs. The feature is structurally unreachable against this corpus.

### Season 3: the disc is not TMDB season 3

Canonical seasons behind each group of `The Complete Series`:

```
DVD 'Season 1' -> canonical S1 eps 1-38  (+1 stray from S2)
DVD 'Season 2' -> canonical S2 eps 1-36
DVD 'Season 3' -> canonical S2 eps 37-75      <-- the failing disc
DVD 'Season 4' -> canonical S2 eps 76-108
DVD 'Season 5' -> canonical S3 eps 1-36 + S4 eps 1-3
DVD 'Season 6' -> canonical S4 eps 4-38
```

The boxset's "Season 3" holds **aired-season-2** content. Engram read `SEASON_3` from the import
folder, pinned `detected_season = 3`, and matched against the 2001 revival's references. The logs
show the signature of a corpus that does not contain the content at all: top score 0.008 to 0.077
and a different winner on nearly every chunk (`S03E12`, `S03E08`, `S03E03`, `S03E09`, `S03E02`,
`S03E07`, `S03E11`), with the full-file fallback topping out at 0.09.

There is already a cross-season path for season-unknown discs; it never ran here because the
label pinned a season and nothing ever revisited that decision.

### Bonus: the conjoined hint parked every track

Both jobs 32 and 33 routed **every** track to review as `multi_episode_detected`.
`_conjoined_episode_count(22.3 min, [7, 7, ...])`
(`backend/app/services/matching_coordinator.py:86`) admits n=2 (window 14-5 to 14+10, i.e. 9 to
24 minutes). The vote runs then cannot confirm it, because there is one reference per track, so
`_apply_multi_episode_review` writes:

> This track's runtime suggests about 2 episodes joined together, but the audio match could not
> confirm it (single_episode). Check it before assigning an episode.

on a track that actually holds three segments, while a confident 0.79 single match sat in
`match_details`. 13 of 13 tracks in job 33, 14 of 14 in job 32. The hint count is wrong too:
`_conjoined_episode_count` returns the smallest admissible n, and with a 7-minute roster and a
+10-minute tolerance the windows for n=2 and n=3 overlap.

## TheTVDB: never implemented

The tester originally asked for TheTVDB as a fallback source, specifically for conjoined episodes.
It was never built. There are zero references to tvdb in `backend/app` or `frontend/src`; the only
mention in the repository is `docs/superpowers/specs/2026-09-13-empty-srt-references-design.md`,
which lists "TheTVDB as a metadata source" under **Out of scope**. No issue tracks it.

That omission is load-bearing rather than incidental. The subtitle providers Engram harvests
(addic7ed, tvsubtitles) index by TVDB-style numbering, and the pack counts above (13 / 40 / 13 /
13) are consistent with half-hour numbering rather than TMDB's segment rosters. The reference
corpus is therefore already living in a TVDB-shaped namespace while being labelled with TMDB
season keys. Adding TheTVDB is not a nice-to-have fallback for this class of show; it supplies the
missing *name* for the namespace the matcher has been working in since day one, and it is what
would let a code be tagged with the scheme it belongs to instead of being silently assumed
canonical.

Design note for whoever picks this up: confirm TheTVDB's own default ordering for a segment show
before committing to it as the reconciliation anchor. The claim that it numbers half-hours is
inferred from the pack counts and the providers' indexing, and has not been checked against the
TVDB API in this investigation.

## Proposed fixes

Ordered by value per unit of risk.

### 1. Season corroboration fallback (fixes Season 3)

The cross-season matcher already exists for unknown-season discs, and
`_maybe_pin_converged_season` already pins a season once matching converges. Add the inverse: if
the first N titles of a season-pinned job all return no acceptable match, unpin the season and
re-dispatch cross-season. That alone would have found the aired-S2 content on the "Season 3" disc
automatically.

Stronger variant: when the show has a DVD episode group and the disc label says "Season N", check
whether DVD group "Season N" lives in a different canonical season and search that instead. The
data is a single `fetch_episode_group` call and the plumbing is already in
`app/core/episode_ordering.py`.

### 2. Never project a code that was not verified against the canonical roster (fixes Season 1)

Surgical version: skip the ordering projection when the season's reference-corpus size disagrees
with the canonical roster size (13 vs 38), and log why. A code produced by a 13-reference corpus
has no business being looked up in a 38-entry map.

Real version: reconcile the two namespaces at harvest time (map half-hour *k* to its canonical
segments via TMDB runtimes and air dates, or via TheTVDB per the section above) so that
`matched_episode` becomes a genuine `S01E01-E03`. That *is* the conjoined naming the tester
expected, and it makes the projection correct instead of merely suppressed.

Either way, surface the projected output filename in the review queue next to the canonical code,
so a shift is visible before the move rather than discovered in the library afterwards.

### 3. Stop the false conjoined parking

Gate `conjoined_hint` on the corpus being *able* to confirm it. When
`len(references) != len(roster)` for the season, a multi-episode verdict is unreachable by
construction, so a confident single match should file rather than park. Separately, consider
reporting the largest admissible n rather than the smallest when the windows overlap.

### 4. Episode-group selection is silently arbitrary

`resolve_episode_group_id` sorts type-3 candidates by episode count and picked the 221-episode
`The Complete Series` over the 9-episode `JellyFin` group. Both are valid DVD groups with very
different structures. The choice should be visible to the user, and ideally selectable per show.

### 5. Cache-builder issue (separate repo)

The published pack writes half-hour-numbered references under canonical TMDB season keys. That
belongs as an issue on the subtitle-cache builder, independent of any backend change, because no
backend fix can distinguish the two schemes while the pack does not record which one it used.

## Reproduction notes

Nothing in this investigation required the tester's media. The projection tables above were
rebuilt from the live TMDB API plus the three diagnostic bundles. The one gap worth closing in the
bundle itself: `build_job_detail` does not include `DiscTitle.episode_ordering` /
`episode_group_id`, so the bundle could not confirm that a projection had run. The shift had to be
inferred by reconstructing the map. Adding those two fields to the per-title detail would have
made this a five-minute diagnosis.

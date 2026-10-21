# Multi-part (segment) episode matching: half-hour to segment crosswalk

- **Issue:** [Jsakkos/engram#760](https://github.com/Jsakkos/engram/issues/760) (Hey Arnold! S2 Disc 2, job 100)
- **Date:** 2026-10-20
- **Status:** approved 2026-10-20 (auto-file crosswalked combined matches; hold uncrosswalked divergent matches; maintainer redeploys the nightly builder); implemented on branch `claude/760-multipart-episodes-f0571a`
- **Builds on:** `2026-09-20-subtitle-cache-numbering-scheme-design.md`, which recorded the
  `divergent` marker and explicitly deferred "mapping half-hour *k* to its canonical segments".
  This spec is that deferred piece.

## 1. What actually went wrong

The reporter expected disc track 1 to be `S02E11-E12`, track 2 `S02E13`, track 3 `S02E14-E15`,
and so on. Engram filed them as `S02E06` through `S02E10`, every one at 0.92 to 0.97 confidence.

The matcher was **right about the content and wrong about the namespace**. Evidence from the
job 100 bundle and live lookups:

| Source | Hey Arnold! season 2 |
|---|---|
| TMDB roster | 37 entries, one per ~11-minute segment (E13 "Arnold's Halloween" is a single 23-minute entry) |
| Published subtitle pack, `precomputed/537/S02.index.json` | 20 codes, `S02E01`..`S02E20` |
| Pack manifest marker | `{"scheme": "divergent", "roster_size": 37}` (all five seasons are divergent) |
| IMDb season 2 | 20 half-hours, e.g. E6 = "Ransom/Ms. Perfect", E7 = "Arnold's Halloween" |
| OpenSubtitles search, `parent_tmdb_id=537&season_number=2` | 20 features. `feature_details.title` is the **TMDB** title for that number (E6 = "Harold's Kitty"), but the `release` string names the **IMDb** half-hour actually in the file (`S02E06.Ransom.Ms.Perfect`) |

So the corpus is IMDb half-hour numbering stored under TMDB-looking codes. Track 1 really is
half-hour 6 ("Ransom/Ms. Perfect"), which TMDB calls E11 + E12. Every track was off by the
number of segment pairs before it, which is why the error grows by one per track (6 vs 11,
7 vs 13, 8 vs 14).

`match_details` shows the matcher already knew: `"numbering_scheme": "divergent"`,
`"pack_roster_size": 37`, `"reference_count": 20`. Today that knowledge only switches off the
#200 ordering projection and the conjoined runtime hint. Nothing stops the half-hour number from
being filed as if it were a TMDB coordinate, so the job completed with five wrong files.

Why the pack builder did not catch it: `download_subtitles` validates an OpenSubtitles result
with `_release_matches_episode`, which only reads a title after a hyphen, en dash or colon.
`S02E06.Ransom.Ms.Perfect` uses dots, so it reads as "uninformative" and is accepted under
`S02E06`.

The prior spec said the providers' half-hour numbering "matches no catalogue Engram can name".
For Hey Arnold! it matches IMDb exactly, which fits OpenSubtitles keying episodes on IMDb. That
does not change this design (Engram has no IMDb access at runtime), but it explains the corpus.

The other half of the report ("faster-whisper not installed" on title 814) was a misleading
message for a transient import failure. It was fixed in #779 (merged 2026-10-17) and is out of
scope here.

## 2. Intent

When a reference season is numbered by broadcast half-hours and TMDB numbers it by segment,
translate each half-hour reference into the TMDB segment code(s) it contains, so a track is
filed as `S02E11-E12` (the Plex/Jellyfin range form `episode_codes` already supports) instead
of under a half-hour number that names an unrelated segment.

## 3. Goals, non-goals, anti-goals

**Goals**

1. Hey Arnold! S2 Disc 2 files as `S02E11-E12`, `S02E13`, `S02E14-E15`, `S02E16-E17`,
   `S02E18-E19`.
2. The same mechanism covers other segment-format shows in the pack (Dexter's Laboratory,
   SpongeBob, and the rest of Hey Arnold!) whenever the evidence supports a mapping.
3. The mapping is computed once, at pack build time, from evidence the builder can observe,
   and shipped in the manifest. The runtime only applies it.
4. Older clients keep working against a pack that carries the mapping (no format bump).

**Non-goals**

- Splitting a combined track into one file per segment. The range form is the agreed output.
- Matching the two halves of a track separately (chapter marks, per-half ASR). A half-hour
  corpus has no per-segment reference to match a half against, and for a corpus that IS
  segment-numbered, the existing chunk-vote run detector already finds the boundary.
- Fixing the scraped-SRT path (seasons the pack does not cover, downloaded at runtime with the
  user's own OpenSubtitles credentials or the scrapers). See section 9.
- Mapping a half-hour to segments in a **different** TMDB season. IMDb's S2E19 "Part Time
  Friends/Biosquare" is TMDB S3E32-E33; v1 leaves it unmapped (section 5.3).
- Changing `_release_matches_episode`. Rejecting dotted titles there would strip Hey Arnold!
  out of the pack entirely, because the builder harvests through the same function.

**Anti-goals** (a failure even if it technically works)

- A confident, wrong, auto-filed result. Today's behaviour is the thing being removed; any
  mapping the evidence does not support must leave the reference unmapped rather than guess.
- A mapping that leaks into identity: the fingerprint network and `matched_episode` must only
  ever see real TMDB coordinates.
- A `CACHE_FORMAT_VERSION` bump. Both manifest readers hard-refuse an unknown version, so a bump
  sends every shipped backend back to scraping the moment the nightly publishes.

## 4. Manifest shape

The crosswalk rides on the existing per-season marker. The scheme value stays `divergent`, so a
client that predates this change reads exactly what it reads today.

```json
"season_numbering": {
  "2": {
    "scheme": "divergent",
    "roster_size": 37,
    "crosswalk": {
      "S02E01": "S02E01-E02",
      "S02E05": "S02E09-E10",
      "S02E06": "S02E11-E12",
      "S02E07": "S02E13",
      "S02E20": "S02E36-E37"
    },
    "crosswalk_sources": {"release_title": 18, "air_date_gap": 1, "unmapped": 1}
  }
}
```

- `crosswalk` maps a corpus code (as stored in `SNN.index.json`) to a canonical TMDB code
  produced by `format_episode_code`. A corpus code absent from the map is **unmapped**.
- `crosswalk_sources` is provenance for logs, the validator and diagnostics. Not read for
  behaviour.
- Only `divergent` seasons ever carry a crosswalk. A `tmdb_aired` season needs none.
- An empty mapping is omitted rather than written as `{}`.

Validator (`validate_subtitle_cache.py`) additions, all failures because they mean the builder
emitted something no consumer can trust: crosswalk on a non-divergent season; a key not in the
season's index; a value that `parse_episode_code` rejects or whose season differs from the
key's; two keys whose values overlap; values not strictly increasing in key order. Mapped and
unmapped counts are reported beside the existing divergent-season summary.

## 5. Builder: deriving the crosswalk ("anchored alignment")

One pure module, `app/matcher/numbering_crosswalk.py`, with no I/O. The builder feeds it three
inputs and writes its output into the marker. `_season_numbering_entry` stays the single
implementation both builder scripts share; it gains the corpus code list (it already receives
its length) and, in `build_subtitle_cache` only, an OpenSubtitles search result for the season.

Inputs:

1. **Corpus codes** for the season, in index order.
2. **TMDB roster** from `fetch_season_episodes`: `episode_number`, `name`, `air_date`, `runtime`.
3. **Release strings per corpus episode number**, from one OpenSubtitles `search` call
   (`parent_tmdb_id`, `season_number`, `type=episode`, `languages=en`). Search costs no download
   quota. Optional: absent when credentials are missing, the call fails, or the pack is built
   `--offline` (which already short-circuits the marker to `unknown`).

### 5.1 Broadcast groups (structure from TMDB)

Walk the roster in episode order and group maximal runs of consecutive episodes that share a
non-empty `air_date`. A segment pair that aired as one half-hour shares a date.

A group can hold two broadcasts that aired the same day (Hey Arnold! S3 E30-E33, 44 minutes).
Split a group whose summed runtime is close to a whole multiple *k* ≥ 2 of the season's median
group runtime into *k* consecutive subgroups by cumulative runtime. A group that is not near a
whole multiple is left alone; it simply will not align (5.3), which degrades to "unmapped".
Episodes with no air date form single-episode groups.

Measured on live TMDB data:

| Season | Roster | Groups | Corpus | Groups equal corpus? |
|---|---|---|---|---|
| Dexter's Laboratory S1 | 38 | 13 | 13 | yes |
| Hey Arnold! S2 | 37 | 19 | 20 | no: IMDb's E19 lives in TMDB S3 |
| Hey Arnold! S1 | 38 | 20 | 19 | no |
| SpongeBob S1 | 41 | 20 | (pack) | n/a |

So structure alone fixes Dexter's Laboratory but not the reporter's season. Titles fix the
reporter's season but not Dexter's Laboratory (its releases are `Dexters.Laboratory.S01E01.WEBRip...`
with no title). The algorithm uses both.

### 5.2 Anchors (evidence from release titles)

For each corpus code, take every release string OpenSubtitles returned for that episode number
and read only the text **after** its `SxxEyy` marker (the show name before it must not match a
title). Normalize release text and each TMDB title in the season to lowercase alphanumerics with
separators removed, and find the TMDB titles whose normalized form is a substring of the
normalized release text. Ignore titles whose normalized form is shorter than 4 characters.
When one found title's match lies inside another's, keep the longer.

Matching on the concatenated form is what lets `Mudbowl` match "Mud Bowl" and `Ms.Perfect`
match "Ms. Perfect". Misspellings (`Monkey buisness.Big caeser`) simply find nothing; the gap
fill below recovers them.

A code's **anchor** is the set of TMDB episodes its releases name, when:

- **every** release OpenSubtitles returned for that episode number names a non-empty title set,
  and all of them name the same set. A release that names nothing counts as dissent, not as
  abstention, and
- the set, expanded to the broadcast group(s) containing it, is one run of consecutive episodes.

Unanimity is required because the builder downloads only one release per episode number (the
first in the search response) and does not record which. A feature's uploads can disagree:
Dexter's Laboratory feature 13 holds a half-hour `Dexters.Laboratory.S01E13.WEBRip` and a
mis-uploaded single segment `1x13 - Jurassic Pooch` (TMDB E13). Anchoring on the titled one
would describe a file that was not downloaded. With unanimity, Dexter's Laboratory gets no
anchors and falls through to structure, which maps all 13 correctly.

Expansion matters: `Hall moniter.Harold's bar mitzvah` finds only E29, but E29 aired with E28,
so the anchor is E28-E29. The evidence says "this file contains E29"; the structure says E29
was broadcast with E28.

Finally, anchors must be strictly increasing and non-overlapping in corpus order. If they are
not, drop every anchor that participates in an inversion or overlap rather than guessing which
one is wrong.

### 5.3 Gap fill (structure between anchors)

Anchors split the season into gaps: before the first anchor, between each pair, after the last.
A gap is measured in corpus **slots** (episode numbers), not in codes present, so a half-hour the
pack has no subtitle for still occupies its slot and cannot shift its neighbours. Between anchors
at corpus numbers *a* and *b* the gap holds slots *a*+1..*b*-1; before the first anchor, 1..*a*-1;
after the last anchor, *b*+1..the highest corpus number present.

For each gap, compare its slot count with the broadcast groups that lie wholly inside the TMDB
episode range between the neighbouring anchors' runs.

- Equal counts: slot *k* of the gap maps to group *k*. Codes present in those slots get mapped.
- Unequal counts, or a group straddling an anchor's run: every code in the gap stays unmapped.

With no anchors at all the whole season is one gap, so this is exactly the "groups equal corpus"
alignment, and it is accepted on the same footing as the existing count proxy: equal counts are
evidence, not proof. (A trailing half-hour missing from the pack makes the last gap's slot count
too small and leaves that gap unmapped. That is the safe direction.)

Worked through on Hey Arnold! S2 (live data):

| Corpus | Release | Anchor | Result | Source |
|---|---|---|---|---|
| E01 | `Save the tree.New Teacher` | E1-E2 | `S02E01-E02` | release_title |
| E05 | `Monkey buisness.Big caeser` | none (typos) | `S02E09-E10` | air_date_gap: one code, one group (E9-E10) between E7-E8 and E11-E12 |
| E06 | `Ransom.Ms.Perfect` | E11-E12 | `S02E11-E12` | release_title |
| E07 | `Arnold's Halloween` | E13 | `S02E13` | release_title |
| E08 | `Arnold saves Sid.Hookey` | E14-E15 | `S02E14-E15` | release_title |
| E15 | `Hall moniter.Harold's bar mitzvah` | E29, expanded to E28-E29 | `S02E28-E29` | release_title |
| E19 | `part time friends.Biosquare` | none (titles are in TMDB S3) | unmapped | one code, zero groups between E34-E35 and E36-E37 |
| E20 | `Runaway float.Partners` | E36-E37 | `S02E36-E37` | release_title |

Discs tracks 1 to 5 (corpus E06 to E10) come out as `S02E11-E12`, `S02E13`, `S02E14-E15`,
`S02E16-E17`, `S02E18-E19`: the reporter's expected list exactly.

The release strings and the TMDB roster for this season are captured verbatim as test fixtures
(per the project rule that parsers are validated against live data, not synthetic input).

A throwaway prototype of this algorithm, run on 2026-10-20 against live TMDB rosters and
OpenSubtitles search results:

| Season | Corpus | Mapped by title | Mapped by structure | Unmapped |
|---|---|---|---|---|
| Hey Arnold! S2 | 20 | 18 | 1 (E05) | 1 (E19, lives in TMDB S3) |
| Hey Arnold! S1 | 19 | 17 | 1 (E19) | 1 (E11) |
| Hey Arnold! S3 | 19 | 17 | 0 | 2 (E17, E18) |
| Dexter's Laboratory S1 | 13 | 0 (no unanimous titles) | 13 | 0 |
| SpongeBob S1 | 20 | 19 | 1 (E01, the 3-segment pilot block) | 0 |

Hey Arnold! S3's TMDB E30-E33 aired on one day (44 minutes); the runtime split turns that into
E30-E31 and E32-E33, which is what lets E16 anchor at E30-E31.

**Possible hardening, not in v1:** record the downloaded release string beside each SRT at
harvest time, so anchors can come from the file actually shipped instead of requiring unanimity.
That changes `download_subtitles` (shared with the runtime) and needs a `--refresh` re-harvest to
populate, so it is left as a follow-up.

## 6. Runtime: applying the crosswalk

### 6.1 One chokepoint

A single function resolves "the codes this precomputed season's rows stand for": it reads the
index, and when the season's marker is `divergent` and carries a crosswalk, it returns the
translated code for each row, or a drop marker for an unmapped row. Every reader of precomputed
codes goes through it:

- `EpisodeMatcher._load_precomputed_season`, which also drops the unmapped rows from the
  reference matrix so they are never a vote candidate;
- `precomputed_episode_codes`, and through it `_precomputed_skip_result`,
  `_heal_precomputed_gaps` and `reference_coverage`.

The chokepoint reads the marker through the same manifest resolution `precomputed_numbering`
uses (tmdb_id first, name fallback, id-mismatch refusal), so the codes and the marker always come
from the same show entry.

An unmapped reference is **dropped**, not kept under its corpus code: its corpus code names an
unrelated TMDB segment (IMDb E19 would read as TMDB E19 "Gerald Moves Out"), which is precisely
the mis-file being removed. A track whose true reference was dropped scores weakly against the
rest and goes to review through the existing low-confidence routing. (My call; see section 8.)

### 6.2 No gap healing for a divergent season

Job 100's log shows the heal at work: "Precomputed cache for Hey Arnold! S02 missing 17 of 37
episode(s): S02E21 ... fetching" from Addic7ed. Diffing a half-hour corpus against a segment
roster produces a meaningless gap list, and the scrapers that would fill it index the show by
half-hour too, so anything they return lands in `data/` under the wrong TMDB code and
`_augment_with_downloaded_srts` grafts it into the vote.

For a `divergent` season (crosswalk or not), `_heal_precomputed_gaps` returns the precomputed
result unchanged and `_augment_with_downloaded_srts` grafts nothing, each with a log line naming
the reason. Seasons that are `tmdb_aired`, `unknown` or unmarked keep today's healing.

### 6.3 Combined codes count against every episode they cover

`reference_coverage` (the review page's per-episode reference flags) compares precomputed codes
with roster codes by exact `S02E11` string. It expands a combined code with `episode_parts`, so
`S02E11-E12` marks both E11 and E12 as `precomputed`.

`_combined_code` (matching_coordinator) refuses a code that is itself combined. With crosswalked
references a single long track can vote two of them in sequence (`S02E11-E12` then `S02E13`).
It flattens combined parts first and keeps its existing rules: one season, contiguous result
(`S02E11-E13`), otherwise None.

### 6.4 What the match result says

When the crosswalk was applied, `stamp_numbering` records `numbering_scheme: "tmdb_aired"` (the
codes coming out of the matcher now ARE canonical coordinates) plus `numbering_crosswalk: true`
and `pack_numbering_scheme: "divergent"` for diagnostics. "Applied" is decided by the same
predicate the chokepoint uses (divergent marker with a non-empty crosswalk), so the stamp and the
codes cannot disagree. Consequences, all through existing code:

- `numbering_schemes_agree` returns True, so the #200 ordering projection applies again. The
  organizer already projects every part of a combined code and falls back when the parts land in
  different seasons.
- `_may_contribute_fingerprint` already refuses a combined code, so a crosswalked two-segment
  track never enters the fingerprint network. Single-segment results (`S02E13`) contribute as
  usual.
- The organizer names the file with the range form; roster coverage counts the track against
  both episodes; the DiscDB export publishes `"11-12"`.

### 6.5 The conjoined runtime hint

A 23.6-minute track against a roster of 11-minute segments gets `conjoined_hint = 2` from the
duration pre-filter. Today `_apply_multi_episode_review` parks any hinted track whose numbering
agrees. With the crosswalk applied, the result is already `S02E11-E12`: the hint is explained,
not contradicted.

New rule in `_apply_multi_episode_review`: when the match came through the crosswalk and
`matched_episode` is a combined code with exactly `conjoined_hint` parts, the hint is satisfied
and the track is not parked. Any other count (hint 3, code covers 2) still parks, as today.

## 7. Divergent seasons with no crosswalk

Until the nightly pack carries crosswalks, and for any season the builder cannot map, a
`divergent` season has no crosswalk and the runtime behaves as today: it files the corpus
number. That is the confidently-wrong outcome this issue reports.

**Proposed (needs maintainer decision, section 8):** when the corpus is `divergent` and no
crosswalk applies, route the title to REVIEW with the matcher's code kept as a pre-fill and a
message that says the reference numbering differs from TMDB, instead of auto-filing. This also
covers the gap between shipping the client and the server's next nightly publish.

The new review error code must join `_NON_REMATCHABLE_REVIEW_ERRORS`: re-matching against the
same corpus cannot change the answer, and the auto review-escalation would otherwise re-dispatch
the title and overwrite its `match_details`.

## 8. Decisions

**For the maintainer** (each changes what gets built):

1. **Auto-file crosswalked combined matches?** Recommended: yes. The mapping is pack-level
   evidence and the vote is confident; parking every segment-show track for confirmation would
   turn the reporter's fix into five review clicks per disc. Alternative: always park combined
   results from a crosswalk with the range pre-filled.
2. **Divergent season, no crosswalk: park for review instead of filing the corpus number?**
   Recommended: yes (section 7). Cost: every disc of a divergent, unmappable season goes to
   review, including the time between this client release and the nightly republish.
3. **Rollout.** The builder change only takes effect once the nightly host
   (`192.168.1.122`) runs the new `build_subtitle_cache.py`. That deploy is yours; this PR ships
   the code and a client that reads the field. Confirm that is acceptable, or say if the PR should
   also carry a one-off backfill command for the current pack.

**My calls** (stated so they can be overruled):

- Builder-only derivation. The runtime does not compute its own air-date crosswalk; one place
  decides, and runtime-only would miss the release-title anchors that the reporter's season
  needs.
- Unmapped references are dropped from the vote, not kept under their corpus code.
- Gap healing and scraped-SRT grafting are off for every `divergent` season (6.2).
- Cross-season targets (IMDb S2E19 to TMDB S3E32-E33) are unmapped in v1.
- Minimum normalized title length 4; group-split tolerance chosen from the measured seasons
  (written into the plan with its test cases).
- Fingerprint contribution stays refused for combined codes (existing rule, unchanged).

## 9. Out of scope, worth a follow-up

- **Runtime scraped-SRT path.** A user whose season is not in the pack, downloading with their
  own OpenSubtitles credentials, still gets IMDb-numbered SRTs saved under TMDB codes. The same
  `numbering_crosswalk` module could be applied in `download_subtitles` to save each SRT under its
  translated code. Separate change because it alters on-disk SRT naming.
- **Cross-season crosswalk targets**, using the whole-show TMDB roster for titles not found in
  the season.
- **Retrying a title after a transient ASR import failure** (from #779's write-up).

## 10. Testing

- `numbering_crosswalk` unit tests on captured fixtures: Hey Arnold! S2 (expected map above,
  including E05 by gap fill, E15 by expansion, E19 unmapped), Dexter's Laboratory S1 (all 13 by
  gap fill), plus synthetic edge cases: disagreeing releases, inverted anchors, a straddling
  group, a same-day double broadcast split, no releases, no air dates.
- Builder: `_season_numbering_entry` emits the crosswalk for a divergent season, omits it for
  `tmdb_aired`, `unknown` and `--offline`, and survives an OpenSubtitles search failure (falls back
  to structure only).
- Validator: each new failure, and counts in the summary.
- Runtime: chokepoint translation and row dropping; heal and augment skipped for divergent
  seasons and unchanged otherwise; coverage expansion; `_combined_code` flattening; stamping; the
  hint rule (explained vs unexplained counts); the no-crosswalk divergent routing (if approved).
- An end-to-end matcher-level test that feeds the HA S2 index, marker and a vote for corpus
  `S02E06` and asserts `matched_episode == "S02E11-E12"`, MATCHED, and an organized filename of
  `Hey Arnold! - S02E11-E12.mkv`.
- Old-client compatibility: a manifest with a crosswalk still loads through
  `load_precomputed_manifest` and `precomputed_numbering` unchanged.

# Conjoined Multi-Episode Tracks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop filing conjoined multi-segment cartoon episodes (e.g. The Weekenders, Courage the Cowardly Dog: two 11-minute segments in one 23-minute DVD track) as Extras, and instead route them to human review with both episode codes identified.

**Architecture:** Demote the duration pre-filter from a terminal verdict to an *admission test*, and let the ASR chunk votes make the real call. The matcher already records the video timestamp of every chunk vote (`MatchCoverage.matched_chunks`), so "how many episodes are in this file" becomes an observation over positional vote runs rather than duration arithmetic. Duration only decides whether a track is worth transcribing; the run decomposition decides what it contains.

**Tech Stack:** Python 3.11+, pytest, SQLModel/async SQLAlchemy, faster-whisper + TF-IDF matcher. Backend only, no frontend changes.

---

## Background: why duration alone cannot decide this

The failing gate is `_duration_matches_episode_runtime` in
`backend/app/services/matching_coordinator.py:54`. TMDB catalogues each 11-minute
cartoon *segment* as its own episode, so the season runtimes come back as
`[11] * 16`. The physical DVD track is a 23-minute broadcast half-hour holding two
segments. The accept window is `[rt - 5, rt + 10]` = `[6, 21]`, and 23 fails it for
every entry. `_handle_extras` then early-returns before the file is ever transcribed.

Widening the window is not a fix. A conjoined 3-episode track and a 3-episode
"Play All" are physically identical, so no duration rule can separate them.

Two facts constrain the design:

1. **Play All is already handled elsewhere, and is disc-scoped.** `Analyst._detect_play_all`
   (`backend/app/core/analyst.py:848`) requires a title to be at least
   `analyst_movie_min_duration` (**80 minutes**) *and* within +/-20% of the sum of the
   entire episode cluster. Those titles are deselected before ripping
   (`backend/app/services/identification_coordinator.py:462`), so they never reach
   `_match_single_title`. The duration pre-filter's real job is catching *extras*
   (featurettes), not Play Alls.
2. **Positional vote data already exists.** `MatchCoverage.add_match(start_time, chunk_len, score)`
   (`backend/app/matcher/episode_identification.py:886`) stores the video timestamp of
   every vote, per episode. And `select_chunk_vote` requires the top episode to lead the
   runner-up by `CHUNK_VOTE_MARGIN_RATIO = 1.8` or it **abstains**, so a 30-second chunk
   straddling the seam between two segments transcribes half of each and abstains. The
   seam marks itself.

The resulting signal, over the default 10 scan points:

| Track | Vote sequence (sorted by timestamp) | Verdict |
|---|---|---|
| Single episode | `E01 E01 E01 E01 E01 E01 E01 E01 E01 E01` | 1 run, single |
| Conjoined E01+E02 | `E01 E01 E01 E01 (abstain) E02 E02 E02 E02 E02` | 2 contiguous runs, multi |
| Single ep + ASR noise | `E01 E01 E07 E01 E01 E13 E01 E01 E01 E01` | singletons dropped, single |
| Episode + 12min featurette | `E01 E01 E01 E01 (abstain x6)` | 1 run, sparse coverage |

**Where N stops.** N is bounded by evidence density, not by a magic number, and the
bound is enforced rather than assumed. With `N` evenly spaced scan points split into `R`
runs, an edge run of `k` votes owns `(k - 0.5)/(N - 1)` of the timeline, so the territory
rule can only reject the smallest admissible run when `N > 3R + 1`. Below that it cannot
discriminate at all, so the module refuses the verdict (`insufficient_scan_depth`) instead
of guessing. At the default 10 scan points a confident verdict therefore caps at **two**
episodes; three needs the next lattice level (19).

Note the two caps differ on purpose. The duration admission (Task 1) is permissive at
`MAX_CONJOINED_EPISODES = 3` because its only job is deciding what is worth transcribing;
the vote verdict is stricter because its job is deciding what to claim. Both sit far below
the 80-minute Play All floor, and Play All titles are deselected before ripping anyway.

## Scope

**In scope (this plan):** Tasks 1-4. Conjoined tracks reach ASR, are identified as
multi-episode, and land in REVIEW with both codes as a suggestion.

**Explicitly deferred (separate plan):** multi-episode *naming and organizing*
(`Show - S01E01-E02`), which additionally requires auditing anchored episode-code
regexes at `backend/app/core/discord_notifier.py:196` and
`backend/app/services/disc_contribution_queue.py:42`, both of which silently discard
a compound code today.

**Why Tasks 1-2 cannot ship without Tasks 3-4:** if the gate stops filing conjoined
tracks as extras but nothing consumes the verdict, ASR picks whichever of the two
episodes scores marginally higher and returns it with high confidence. That is a
silent mislabel where E02 vanishes from the library, strictly worse than today's
recoverable "extra". Task 3 is the safety seam; do not merge Tasks 1-2 alone.

## File Structure

- **Create** `backend/app/matcher/multi_episode.py`: pure run-decomposition logic.
  No imports from `app.services` or `app.models`; depends only on stdlib. This keeps it
  unit-testable with synthetic vote lists (no MKV, no audio, no Whisper) and importable
  from both the matcher and the services layer.
- **Create** `backend/tests/unit/test_multi_episode.py`: tests for the above.
- **Modify** `backend/app/services/matching_coordinator.py`: add the conjoined-duration
  admission test (Task 1); make the pre-filter non-terminal and route confirmed
  multi-episode matches to REVIEW (Task 3).
- **Modify** `backend/app/matcher/episode_identification.py`: build the vote list from
  the existing `coverages` and attach the verdict to `match_details` (Task 3).
- **Modify** `backend/app/services/finalization_coordinator.py:223`: register the new
  REVIEW error code as non-rematchable (Task 4).
- **Modify** `backend/tests/unit/test_matching_coordinator.py`: tests for Task 1.

`backend/app/core/curator.py` needs **no changes**: `match_details` already flows
verbatim from `identify_episode` through `MatchResult` to `DiscTitle.match_details`.

---

### Task 1: Conjoined-duration admission test

A pure predicate answering "could this track plausibly be N consecutive episodes?".
It is a *hint* only; Task 2's run decomposition is authoritative. Note the windows for
adjacent N can overlap (for 11-minute runtimes, N=2 gives `[17, 32]` and N=3 gives
`[28, 43]`); returning the smallest N is fine precisely because the votes decide the
real count.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (after line 65, below `_duration_matches_episode_runtime`)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_matching_coordinator.py`. Note `GILMORE_S1` is already
defined at line 137 of that file; reuse it, do not redefine it.

```python
@pytest.mark.unit
class TestConjoinedEpisodeCount:
    """Cartoon discs put two or three ~11-minute segments in one physical track.
    TMDB lists each segment as its own episode, so the track only matches a SUM of
    runtimes, never a single one. This predicate admits such a track to ASR; the
    positional vote runs (app/matcher/multi_episode.py) make the real call.
    """

    WEEKENDERS_S1 = [11] * 16

    def test_two_segment_track_admits_as_two(self):
        # The reported bug: job 60, a 23-minute track of two 11-minute segments.
        assert _conjoined_episode_count(23.0, self.WEEKENDERS_S1) == 2

    def test_three_segment_track_admits_as_three(self):
        # 3 x 11 = 33, window [28, 43].
        assert _conjoined_episode_count(34.0, self.WEEKENDERS_S1) == 3

    def test_single_episode_is_not_conjoined(self):
        # 11 minutes is one segment. Callers test the single-episode window first;
        # this predicate must not claim it.
        assert _conjoined_episode_count(11.0, self.WEEKENDERS_S1) is None

    def test_smallest_n_wins_when_windows_overlap(self):
        # N=2 window [17, 32] and N=3 window [28, 43] overlap at [28, 32].
        # Return the smaller; the vote runs correct it if wrong.
        assert _conjoined_episode_count(30.0, self.WEEKENDERS_S1) == 2

    def test_play_all_track_is_not_conjoined(self):
        # Gilmore t00: 11515s = 191.9min. Above every N<=3 window, so it stays an
        # extra. This is the existing test_play_all_track_is_an_extra invariant.
        assert _conjoined_episode_count(11515 / 60, GILMORE_S1) is None

    def test_four_segments_exceeds_the_cap(self):
        # 4 x 11 = 44. Beyond MAX_CONJOINED_EPISODES: 10 scan points cannot
        # resolve 4 runs (needs ~2 votes/run + seams), so refuse rather than guess.
        assert _conjoined_episode_count(44.0, self.WEEKENDERS_S1) is None

    def test_short_featurette_is_not_conjoined(self):
        # 10 minutes against 22/44-minute episodes: below every sum window.
        assert _conjoined_episode_count(10.0, [22, 44]) is None

    def test_uses_consecutive_runtime_windows(self):
        # A season whose runtimes vary: 22+22 = 44, window [39, 54].
        assert _conjoined_episode_count(45.0, [22, 22, 44, 44]) == 2

    def test_empty_runtimes_never_matches(self):
        assert _conjoined_episode_count(23.0, []) is None
```

Add `_conjoined_episode_count` to the existing import block at line 21 of that file:

```python
from app.services.matching_coordinator import (
    MatchingCoordinator,
    _conjoined_episode_count,
    _duration_matches_episode_runtime,
    episode_curator,
)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_matching_coordinator.py -k Conjoined -v
```

Expected: collection error, `ImportError: cannot import name '_conjoined_episode_count'`.

- [ ] **Step 3: Write the implementation**

Insert into `backend/app/services/matching_coordinator.py` immediately after
`_duration_matches_episode_runtime` (which ends at line 65):

```python
# Cartoon and anthology discs put several short segments in one physical track:
# TMDB catalogues each ~11-minute segment as its own episode, so a 23-minute track
# matches no SINGLE runtime and was filed as an extra un-transcribed (issue #622).
# The cap is set by evidence density, not taste: the positional vote runs that
# actually decide the count need ~2 votes per run plus a seam, and the default scan
# is 10 points, so 3 is the most a default scan can resolve. It also sits far below
# the 80-minute Play All floor (analyst_movie_min_duration), and Play All titles are
# deselected pre-rip anyway (identification_coordinator), so they never arrive here.
MAX_CONJOINED_EPISODES = 3


def _conjoined_episode_count(title_minutes: float, runtimes: list[int]) -> int | None:
    """Smallest ``n`` in 2..MAX for which the track looks like n conjoined episodes.

    Tests the duration against the sum of each run of ``n`` CONSECUTIVE runtimes
    (a conjoined track holds adjacent segments), reusing the same asymmetric padding
    window as the single-episode gate (the recap/credits padding applies once to the
    whole track, not once per segment).

    This is an ADMISSION hint, not a verdict: windows for adjacent ``n`` can overlap,
    and the authoritative count comes from the positional vote runs in
    ``app.matcher.multi_episode``. Returns None when the track is not plausibly a
    small concatenation, i.e. it is a genuine extra.

    Callers must test ``_duration_matches_episode_runtime`` first; a track that is a
    plain single episode is never reported here.
    """
    if not runtimes:
        return None
    for n in range(2, MAX_CONJOINED_EPISODES + 1):
        if n > len(runtimes):
            break
        for i in range(len(runtimes) - n + 1):
            total = sum(runtimes[i : i + n])
            if (
                (total - EPISODE_DURATION_UNDER_TOLERANCE_MIN)
                <= title_minutes
                <= (total + EPISODE_DURATION_OVER_TOLERANCE_MIN)
            ):
                return n
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_matching_coordinator.py -k "Conjoined or DurationMatches" -v
```

Expected: PASS. The pre-existing `TestDurationMatchesEpisodeRuntime` class must stay
green, because this task adds a function and changes no behaviour yet.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "feat(matching): add conjoined-episode duration admission test (#622)"
```

---

### Task 2: Positional vote-run decomposition

The load-bearing piece. Pure function over a list of `(timestamp, episode_code)` votes,
so it tests with no audio, no MKV, and no Whisper.

Four checks turn runs into a verdict:

1. **Minimum run weight**: a code needs at least 2 votes (mirrors the matcher's existing
   `min_vote_count=2`). Drops ASR noise singletons.
2. **Contiguity**: each surviving code's votes must form one solid positional block.
   Interleaved codes are noise, not a second episode.
3. **Coverage**: the runs must jointly account for most of the scan points. This is what
   separates "two conjoined episodes" from "one episode plus a bonus featurette", which
   duration arithmetic cannot do at all.
4. **Territory**: each run must OWN a fair share of the timeline. A recap that wins two
   votes in the first 90 seconds occupies a sliver of the file; a real segment owns
   roughly 1/N of it. This check uses run POSITION, not vote count, because by vote
   count a 2-vote recap and a 2-vote genuine segment are indistinguishable.

**Files:**
- Create: `backend/app/matcher/multi_episode.py`
- Test: `backend/tests/unit/test_multi_episode.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_multi_episode.py`:

```python
"""Unit tests for positional vote-run decomposition.

Root cause these guard against: a DVD track holding two ~11-minute cartoon segments
matched no single TMDB runtime and was filed as an extra without ever being
transcribed (issue #622). Duration cannot distinguish a conjoined 3-episode track
from a 3-episode Play All, but the POSITION of each chunk vote can: conjoined
episodes produce contiguous, balanced runs along the timeline, and the chunk
straddling the seam abstains (see select_chunk_vote's rank+margin rule).

CI-safe: pure functions over synthetic vote lists. No MKV/audio/Whisper.
"""

import json

import pytest

from app.matcher.multi_episode import decompose_vote_runs


def _votes(*spans: tuple[str, list[float]]) -> list[tuple[float, str]]:
    """Build a (start, code) vote list from (code, [timestamps]) pairs."""
    return [(t, code) for code, times in spans for t in times]


@pytest.mark.unit
class TestDecomposeVoteRuns:
    def test_single_episode_is_not_multi(self):
        v = _votes(("S01E01", [120, 260, 400, 540, 680, 820, 960, 1100, 1240, 1330]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "single_episode"
        assert verdict.codes == ("S01E01",)

    def test_conjoined_pair_with_abstained_seam(self):
        # The bug case: two 11-min segments in a 23-min track. The chunk on the
        # seam (~690s) abstains, so it simply does not appear in the vote list.
        v = _votes(
            ("S01E01", [120, 250, 380, 510, 640]),
            ("S01E02", [820, 950, 1080, 1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=11)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02")
        assert verdict.reason == "contiguous_runs"

    def test_runs_are_ordered_by_position_not_episode_number(self):
        # A disc may pair segments in an order TMDB does not number consecutively.
        # Report them in PLAYBACK order so downstream naming reflects the file.
        v = _votes(
            ("S01E04", [120, 250, 380, 510, 640]),
            ("S01E03", [820, 950, 1080, 1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=11)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E04", "S01E03")

    def test_noise_singletons_are_dropped(self):
        # Two spurious one-off votes inside an otherwise solid single episode.
        v = _votes(
            ("S01E01", [120, 250, 510, 640, 820, 1080, 1210, 1330]),
            ("S01E07", [380]),
            ("S01E13", [950]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "single_episode"
        assert verdict.codes == ("S01E01",)

    def test_interleaved_runs_are_rejected(self):
        # Two codes each clear the vote minimum but their spans overlap: this is
        # ASR confusion between similar episodes, not two episodes in one file.
        v = _votes(
            ("S01E01", [120, 640, 1210]),
            ("S01E02", [380, 950, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "interleaved_runs"

    def test_sparse_coverage_is_rejected(self):
        # One 11-min episode plus 12 minutes of un-matchable bonus content: the
        # runs account for only 4 of 10 scan points, so something unexplained
        # occupies the file. Duration arithmetic cannot catch this case.
        v = _votes(("S01E01", [120, 250]), ("S01E02", [380, 510]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "sparse_coverage"

    def test_unbalanced_runs_are_rejected(self):
        # E02 holds 2 of 9 votes against E01's 7, a "next time on" preview
        # bleeding into the tail, not a genuine second episode.
        v = _votes(
            ("S01E01", [120, 250, 380, 510, 640, 770, 900]),
            ("S01E02", [1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_three_conjoined_segments(self):
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [700, 830, 960, 1090]),
            ("S01E03", [1280, 1410, 1540, 1670]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=19)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02", "S01E03")

    def test_recap_at_head_is_not_a_second_episode(self):
        # A "previously on" recap wins two votes for the PRIOR episode at the head.
        # By vote count it is indistinguishable from a real segment (2 vs 6 clears
        # any mean-relative balance rule); by TERRITORY it owns a sliver, so it is
        # rejected. This is the false positive a vote-count balance check admits.
        v = _votes(
            ("S01E04", [120, 250]),
            ("S01E05", [380, 510, 640, 770, 900, 1030]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_three_runs_are_refused_at_default_scan_depth(self):
        # Recap at the head plus a "next time on" preview at the tail around one
        # real episode. At 10 scan points the territory rule cannot discriminate
        # 3 runs at all (it needs N > 3R + 1), so the verdict is refused for want
        # of evidence rather than guessed. Every point votes here deliberately:
        # an abstention gap would let this pass on a rounding margin.
        v = _votes(
            ("S01E04", [0, 100]),
            ("S01E05", [200, 300, 400, 500, 600, 700]),
            ("S01E06", [800, 900]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "insufficient_scan_depth"

    def test_recap_and_preview_rejected_once_depth_supports_it(self):
        # The same shape at the next lattice level (19), where the territory rule
        # IS live: now it rejects on the merits rather than on scan depth.
        v = _votes(
            ("S01E04", [0, 100]),
            ("S01E05", [200 + 100 * i for i in range(15)]),
            ("S01E06", [1700, 1800]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=19)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_genuine_three_segments_accepted_at_sufficient_depth(self):
        # A real 3-segment track at depth 19: each run owns about a third of the
        # timeline, so it survives every guard.
        v = _votes(
            ("S01E01", [0, 100, 200, 300]),
            ("S01E02", [500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500]),
            ("S01E03", [1700, 1800, 1900, 2000]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=19)
        assert verdict.is_multi_episode is True
        assert verdict.reason == "contiguous_runs"

    def test_low_vote_yield_pair_is_still_accepted(self):
        # Near-wordless cartoons are both the target content AND the lowest
        # vote-yield content, so the coverage floor must not reject them. Exactly
        # at MIN_TIMELINE_COVERAGE (5 of 10): accepted, since the bound is strict.
        v = _votes(("S01E01", [120, 250, 380]), ("S01E02", [820, 950]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is True
        assert verdict.reason == "contiguous_runs"

    def test_more_votes_than_scan_points_is_refused(self):
        # A caller that double-counts a chunk across two coverages would otherwise
        # push coverage above 1.0 and silently STRENGTHEN the verdict. Refuse.
        v = _votes(("S01E01", [1, 2, 3]), ("S01E02", [4, 5, 6]))
        verdict = decompose_vote_runs(v, total_scan_points=4)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "invalid_input"

    def test_conjoined_pair_at_default_scan_depth(self):
        # canonical_scan_points snaps to the lattice (10/19/37/73/145), so 10 is
        # the depth this actually runs at. The other pair tests use 11, which no
        # real scan produces.
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [820, 950, 1080, 1210]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is True

    def test_three_segments_at_realistic_scan_depth(self):
        # 12 votes is impossible at 10 scan points; 19 is the next lattice level.
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [700, 830, 960, 1090]),
            ("S01E03", [1280, 1410, 1540, 1670]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=19)
        assert verdict.is_multi_episode is True

    def test_to_dict_is_json_safe_and_playback_ordered(self):
        # to_dict lands in DiscTitle.match_details and is read back by the UI.
        v = _votes(
            ("S01E04", [120, 250, 380, 510]),
            ("S01E03", [820, 950, 1080, 1210]),
        )
        d = decompose_vote_runs(v, total_scan_points=10).to_dict()
        assert json.loads(json.dumps(d)) == d
        assert d["is_multi_episode"] is True
        assert d["codes"] == ["S01E04", "S01E03"]
        assert [r["code"] for r in d["runs"]] == ["S01E04", "S01E03"]
        assert d["runs"][0]["votes"] == 4

    def test_no_votes_is_not_multi(self):
        verdict = decompose_vote_runs([], total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "no_votes"
        assert verdict.codes == ()

    def test_zero_scan_points_is_not_multi(self):
        # Guards the coverage division; a caller with no scan points has no evidence.
        verdict = decompose_vote_runs(_votes(("S01E01", [1, 2])), total_scan_points=0)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "no_votes"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_multi_episode.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'app.matcher.multi_episode'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/matcher/multi_episode.py`:

```python
"""Detect a physical track that concatenates several TMDB episodes.

Cartoon and anthology discs put two or three ~11-minute segments in one DVD title,
while TMDB catalogues each segment as its own episode. Such a track matches no
single TMDB runtime, and the duration pre-filter used to file it as an extra
without ever transcribing it (issue #622).

Duration cannot solve this: a conjoined 3-episode track and a 3-episode "Play All"
are physically identical. The POSITION of each chunk vote can. ``identify_episode``
already records the video timestamp of every vote (``MatchCoverage.add_match``), and
``select_chunk_vote`` abstains when the top two episodes score similarly, so the
chunk straddling the seam between two segments casts no vote and the seam marks
itself. Conjoined episodes therefore appear as contiguous, balanced runs along the
timeline; ASR confusion appears as interleaved singletons.

Pure stdlib by design (no app.models, no app.services): the whole decision is
testable with synthetic vote lists, no MKV or Whisper required.

Known limitation: the span is measured between the first and last VOTE, not across
the scanned range, so a file with a large unexplained head or tail (e.g. a third
segment absent from the TMDB reference set) can still yield a confident two-episode
verdict. Closing it needs the scan offsets, not just their count. Accepted for now
because a positive verdict routes the track to REVIEW rather than naming it, so the
cost is an incomplete suggestion a human corrects, not a wrong filename on disk.
"""

from __future__ import annotations

from dataclasses import dataclass

# A code needs this many votes to count as a run rather than ASR noise. Mirrors
# EpisodeMatcher.min_vote_count so a run is never weaker than an accepted match.
MIN_RUN_VOTES = 2

# The runs must jointly account for this fraction of scan points. Below it, some
# unexplained content occupies the file (bonus features, a third unmatched
# segment), so "N episodes end to end" is the wrong description of the track.
# Kept at half rather than higher because near-wordless cartoons are exactly the
# content this feature targets AND the content that yields the fewest votes.
MIN_TIMELINE_COVERAGE = 0.5

# Each run must own at least this fraction of an equal share of the TIMELINE.
# Deliberately positional rather than a vote-count ratio: by vote count a 2-vote
# recap at the head and a 2-vote genuine segment are identical, but by territory
# the recap owns a sliver and the segment owns roughly 1/N of the file.
MIN_RUN_TERRITORY = 0.5

# A run needs roughly this many scan points before its share of the timeline is
# measurable at all. With N evenly spaced points split into R runs, an edge run of
# k votes owns (k - 0.5)/(N - 1) of the span, so the territory rule can only reject
# the smallest admissible run (k = MIN_RUN_VOTES = 2) when N > 3R + 1. Below that
# the rule is not merely lenient, it is mathematically incapable of discriminating,
# so refuse the verdict rather than return a guess the evidence cannot support.
# At the default 10 scan points this caps a confident verdict at two episodes;
# canonical_scan_points' next lattice level (19) supports three.
MIN_SCAN_POINTS_PER_RUN = 3


@dataclass(frozen=True)
class EpisodeRun:
    """One episode's contiguous block of chunk votes within a single file."""

    code: str
    first_start: float
    last_start: float
    votes: int


@dataclass(frozen=True)
class MultiEpisodeVerdict:
    """Whether a file holds several episodes, and the evidence for it."""

    runs: tuple[EpisodeRun, ...]
    is_multi_episode: bool
    reason: str

    @property
    def codes(self) -> tuple[str, ...]:
        """Episode codes in PLAYBACK order (not TMDB numbering order)."""
        return tuple(r.code for r in self.runs)

    def to_dict(self) -> dict:
        """JSON-safe form for DiscTitle.match_details.

        ``codes`` and ``runs`` are in PLAYBACK order, not TMDB numbering order.
        """
        return {
            "is_multi_episode": self.is_multi_episode,
            "reason": self.reason,
            "codes": list(self.codes),
            "runs": [
                {
                    "code": r.code,
                    "first_start": r.first_start,
                    "last_start": r.last_start,
                    "votes": r.votes,
                }
                for r in self.runs
            ],
        }


def _rejected(runs: tuple[EpisodeRun, ...], reason: str) -> MultiEpisodeVerdict:
    """Build a negative verdict. Only ever False; the accepting path builds inline."""
    return MultiEpisodeVerdict(runs=runs, is_multi_episode=False, reason=reason)


def decompose_vote_runs(
    votes: list[tuple[float, str]],
    total_scan_points: int,
) -> MultiEpisodeVerdict:
    """Decide whether chunk votes describe one episode or several conjoined ones.

    Args:
        votes: ``(chunk_start_seconds, episode_code)`` for every chunk that cast a
            vote. Chunks that abstained are simply absent. Order does not matter.
        total_scan_points: how many chunks were scanned in total, including
            abstentions. Used for the coverage check.

    Returns:
        A ``MultiEpisodeVerdict``. ``is_multi_episode`` is True only when two or
        more codes form contiguous, well-covered, balanced runs. Every rejection
        carries a ``reason`` so the decision is legible in logs and match_details.
    """
    if not votes or total_scan_points <= 0:
        return _rejected((), "no_votes")

    # 1. Group votes by code and drop noise singletons.
    by_code: dict[str, list[float]] = {}
    for start, code in votes:
        by_code.setdefault(code, []).append(start)

    runs = [
        EpisodeRun(
            code=code,
            first_start=min(starts),
            last_start=max(starts),
            votes=len(starts),
        )
        for code, starts in by_code.items()
        if len(starts) >= MIN_RUN_VOTES
    ]
    if not runs:
        return _rejected((), "no_votes")

    # Playback order: a disc may pair segments TMDB does not number consecutively,
    # so position on the timeline is the ordering that reflects the file.
    runs.sort(key=lambda r: r.first_start)
    ordered = tuple(runs)

    if len(ordered) == 1:
        return _rejected(ordered, "single_episode")

    # 2. Contiguity: each run must occupy its own stretch of the timeline. Any
    # overlap means the codes interleave, which is ASR confusion between similar
    # episodes rather than two episodes stored end to end. Adjacent pairs suffice:
    # runs are sorted by first_start, so if every neighbour clears the previous
    # run's last_start, every non-adjacent pair does too (and a nested run is
    # always caught against its immediate predecessor).
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if later.first_start <= earlier.last_start:
            return _rejected(ordered, "interleaved_runs")

    # 3. Coverage: the runs must explain most of what was scanned. A caller that
    # reports more votes than chunks has double-counted; refuse rather than let a
    # broken caller silently strengthen the verdict.
    counted = sum(r.votes for r in ordered)
    if counted > total_scan_points:
        return _rejected(ordered, "invalid_input")
    if total_scan_points <= MIN_SCAN_POINTS_PER_RUN * len(ordered) + 1:
        return _rejected(ordered, "insufficient_scan_depth")

    if counted / total_scan_points < MIN_TIMELINE_COVERAGE:
        return _rejected(ordered, "sparse_coverage")

    # 4. Territory: each run owns the stretch from its midpoint with the previous
    # run to its midpoint with the next. A recap or a "next time on" preview wins
    # votes but owns almost no timeline, so this rejects what a vote-count ratio
    # cannot: by vote count a 2-vote recap and a 2-vote real segment are identical.
    edges = [ordered[0].first_start]
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        edges.append((earlier.last_start + later.first_start) / 2)
    edges.append(ordered[-1].last_start)
    span = edges[-1] - edges[0]
    # `not span > 0` rather than `span <= 0`: a NaN timestamp makes every
    # comparison False, so it would otherwise pass contiguity, pass this guard,
    # fail every territory test, and return a confident verdict serializing to
    # invalid JSON.
    if not span > 0:
        return _rejected(ordered, "degenerate_span")
    fair_share = 1.0 / len(ordered)
    for i in range(len(ordered)):
        if (edges[i + 1] - edges[i]) / span < fair_share * MIN_RUN_TERRITORY:
            return _rejected(ordered, "unbalanced_runs")

    return MultiEpisodeVerdict(runs=ordered, is_multi_episode=True, reason="contiguous_runs")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_multi_episode.py -v
```

Expected: PASS, 19 tests.

- [ ] **Step 5: Lint**

```bash
cd backend && uv run ruff check app/matcher/multi_episode.py tests/unit/test_multi_episode.py && uv run ruff format --check app/matcher/multi_episode.py tests/unit/test_multi_episode.py
```

Expected: `All checks passed!` and no reformat needed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/multi_episode.py backend/tests/unit/test_multi_episode.py
git commit -m "feat(matcher): decompose chunk votes into positional episode runs (#622)"
```

---

### Task 3: Wire the verdict through matching

Three edits: the matcher publishes the verdict, the pre-filter stops being terminal,
and any track that is or might be multi-episode goes to REVIEW instead of being
auto-organized under one of its codes.

**Note the two functions.** The duration pre-filter lives in `_run_match_single_file`
(starts line 697) but the MATCHED handling lives in `_match_single_file_inner` (starts
line 1078). They are DIFFERENT functions: `_run_match_single_file` calls the inner one
at line 889. A local variable will not carry between them, so the conjoined hint is
passed as a new parameter.

**Files:**
- Modify: `backend/app/matcher/episode_identification.py:1911-1916` (the `match_stats` dict)
- Modify: `backend/app/services/matching_coordinator.py:831-849` (the pre-filter branch)
- Modify: `backend/app/services/matching_coordinator.py:889` and `:1078-1086` (pass the hint)
- Modify: `backend/app/services/matching_coordinator.py:1472` (post-match routing)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Publish the verdict from the matcher**

In `backend/app/matcher/episode_identification.py`, add the import next to the other
matcher imports at the top of the file:

```python
from app.matcher.multi_episode import decompose_vote_runs
```

Then replace lines 1911-1916 (beginning `# Prepare detailed stats for return`) with:

```python
            # Prepare detailed stats for return
            # Positional vote runs: a track holding two conjoined ~11-minute
            # segments votes for E01 across the first half and E02 across the
            # second, with the seam chunk abstaining. coverages already carries
            # every vote's timestamp, so this costs no extra transcription.
            # extract_season_episode returns (None, None) for a reference name it
            # cannot parse, so filter before formatting: an unguarded
            # "S{:02d}".format(None) raises ValueError and would abort the match.
            positional_votes: list[tuple[float, str]] = []
            for cov in coverages.values():
                cov_season, cov_episode = extract_season_episode(cov.episode_name)
                if cov_season is None or cov_episode is None:
                    continue
                cov_code = f"S{cov_season:02d}E{cov_episode:02d}"
                positional_votes.extend((c["start"], cov_code) for c in cov.matched_chunks)

            multi_verdict = decompose_vote_runs(positional_votes, len(scan_points))
            multi_detail = multi_verdict.to_dict()

            # decompose_vote_runs normalizes against the VOTED span, so a stretch
            # of the file that never voted is invisible to it (a third segment
            # missing from the TMDB reference set, say). Record the unexplained
            # head and tail here, where the scan offsets are in scope, so a
            # reviewer looking at a two-code suggestion for a three-segment file
            # has a signal that something is unaccounted for. Under-listing is
            # otherwise silent: a short code list looks complete.
            if positional_votes and scan_points:
                multi_detail["head_gap_seconds"] = round(
                    min(v[0] for v in positional_votes) - scan_points[0], 1
                )
                multi_detail["tail_gap_seconds"] = round(
                    scan_points[-1] - max(v[0] for v in positional_votes), 1
                )

            if multi_verdict.is_multi_episode:
                logger.info(
                    f"[Matcher] {video_file}: chunk votes describe "
                    f"{len(multi_verdict.runs)} conjoined episodes "
                    f"{multi_verdict.codes}, routing to review"
                )

            match_stats = {
                "matches_found": matches_found_count,
                "matches_rejected": matches_rejected_count,
                "total_chunks": len(scan_points),
                "multi_episode": multi_detail,
            }
```

`extract_season_episode` is already imported and used in this file (see line 1852).
No change is needed in `backend/app/core/curator.py`: `match_stats` is merged into
`best_match["match_details"]`, which flows verbatim into `DiscTitle.match_details`.

- [ ] **Step 2: Make the duration pre-filter non-terminal**

In `backend/app/services/matching_coordinator.py`, inside `_run_match_single_file`,
initialize the hint BEFORE the `try:` that opens the duration pre-filter (the `try:`
just after the `# 4. Duration pre-filter` comment block, around line 812), so it is in
scope after the `except`:

```python
        conjoined_hint: int | None = None
```

Then replace lines 831-849 (the `if runtimes and title_duration:` branch) with:

```python
                if runtimes and title_duration:
                    title_minutes = title_duration / 60
                    if not _duration_matches_episode_runtime(title_minutes, runtimes):
                        # Before filing this as an extra, check whether it looks
                        # like several conjoined episodes (cartoon segment discs).
                        # If so it is ADMITTED to matching, not filed: the
                        # positional vote runs decide what it actually holds.
                        conjoined_hint = _conjoined_episode_count(title_minutes, runtimes)
                        if conjoined_hint:
                            logger.info(
                                f"[MATCH] Title {title_id} (Job {job_id}): duration "
                                f"{title_minutes:.0f}min matches no single episode runtime "
                                f"but fits ~{conjoined_hint} conjoined episodes. Proceeding "
                                f"with matching; vote runs will confirm."
                            )
                        else:
                            # Re-fetch under a fresh session: _handle_extras writes.
                            async with async_session() as session:
                                job = await session.get(DiscJob, job_id)
                                title = await session.get(DiscTitle, title_id)
                                if job and title:
                                    handled = await self._handle_extras(
                                        job_id,
                                        title_id,
                                        title,
                                        job,
                                        file_path,
                                        title_minutes,
                                        runtimes,
                                        session,
                                    )
                                    if handled:
                                        return
```

- [ ] **Step 3: Pass the hint into the inner function**

Still in `backend/app/services/matching_coordinator.py`, change the call at line 889 from:

```python
            await self._match_single_file_inner(
                job_id, title_id, file_path, num_points, min_vote_count, advisory=advisory
            )
```

to:

```python
            await self._match_single_file_inner(
                job_id,
                title_id,
                file_path,
                num_points,
                min_vote_count,
                advisory=advisory,
                conjoined_hint=conjoined_hint,
            )
```

And add the parameter to `_match_single_file_inner`'s signature (line 1078-1086),
keeping the existing docstring and appending the new paragraph to it:

```python
    async def _match_single_file_inner(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        advisory: bool = False,
        conjoined_hint: int | None = None,
    ) -> None:
```

Append this paragraph to that method's existing docstring:

```
        ``conjoined_hint`` is the episode count the duration pre-filter admitted this
        track on (see ``_conjoined_episode_count``), or None for an ordinary track. A
        hinted track is parked for review even when the vote runs cannot confirm it,
        because ASR still returns one confident episode and naming the file after it
        would silently drop the rest.
```

- [ ] **Step 4: Route multi-episode tracks to REVIEW**

Add the error-code constant next to `RIP_FAILURE_ERROR_CODES` (line 197 area):

```python
# A track that is, or might be, several conjoined episodes. Parked for a human
# because Engram cannot yet NAME a multi-episode file (S01E01-E02 organizing is a
# separate change): auto-organizing it under one of its codes would silently lose
# the others.
MULTI_EPISODE_ERROR_CODE = "multi_episode_detected"
```

Then, in `_match_single_file_inner`, immediately BEFORE the existing line
`if title.state == TitleState.MATCHED and title.match_source != "engram_chromaprint":`
(line 1472), insert:

```python
                # A track the chunk votes show is several conjoined episodes must
                # not be auto-organized under a single code: the other episodes
                # would vanish from the library. The same applies to a track the
                # DURATION admitted as conjoined whose votes could NOT confirm it
                # (too few scan points for the run count, sparse coverage) -- ASR
                # still returns one confident episode, so the silent-drop risk is
                # identical. Park both for a human; only the message differs.
                multi_detail = {}
                if title.match_details:
                    try:
                        multi_detail = (
                            json.loads(title.match_details) or {}
                        ).get("multi_episode") or {}
                    except (json.JSONDecodeError, TypeError):
                        multi_detail = {}
                confirmed_multi = bool(multi_detail.get("is_multi_episode"))
                if confirmed_multi or conjoined_hint:
                    details = json.loads(title.match_details) if title.match_details else {}
                    codes = multi_detail.get("codes") or []
                    title.state = TitleState.REVIEW
                    details["error"] = MULTI_EPISODE_ERROR_CODE
                    if confirmed_multi:
                        details["message"] = (
                            f"This track appears to contain {len(codes)} episodes "
                            f"({', '.join(codes)}). Engram cannot name a combined file "
                            "yet. Assign one episode, or mark it as an Extra."
                        )
                    else:
                        details["message"] = (
                            f"This track's runtime suggests about {conjoined_hint} "
                            "episodes joined together, but the audio match could not "
                            f"confirm it ({multi_detail.get('reason', 'no verdict')}). "
                            "Check it before assigning an episode."
                        )
                    title.match_details = json.dumps(details)
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): routed to review as "
                        f"multi-episode (confirmed={confirmed_multi}, "
                        f"hint={conjoined_hint}, codes={codes})"
                    )
```

- [ ] **Step 5: Write the tests**

Append to `backend/tests/unit/test_matching_coordinator.py`:

```python
@pytest.mark.unit
class TestConjoinedAdmission:
    """The duration pre-filter must ADMIT a plausibly-conjoined track to matching
    instead of filing it as an extra (issue #622), and must still file a genuine
    extra. These cover the branch decision only; the wiring is exercised by the
    integration path.
    """

    WEEKENDERS_S1 = [11] * 16

    def test_conjoined_track_is_admitted_not_filed(self):
        # 23min against [11]*16: no single runtime matches, but 2 x 11 does, so
        # the track proceeds to ASR instead of reaching _handle_extras.
        assert _duration_matches_episode_runtime(23.0, self.WEEKENDERS_S1) is False
        assert _conjoined_episode_count(23.0, self.WEEKENDERS_S1) == 2

    def test_featurette_still_files_as_extra(self):
        # Neither a single runtime nor any sum: must still reach _handle_extras.
        assert _duration_matches_episode_runtime(6.0, self.WEEKENDERS_S1) is False
        assert _conjoined_episode_count(6.0, self.WEEKENDERS_S1) is None


@pytest.mark.unit
class TestMultiEpisodeReviewRouting:
    """A track that is, or might be, several conjoined episodes must land in REVIEW
    rather than MATCHED. Auto-organizing it under one code silently loses the rest,
    which is the failure this whole change exists to prevent.
    """

    def _title(self, details: dict | None):
        return SimpleNamespace(
            state=TitleState.MATCHED,
            match_details=json.dumps(details) if details is not None else None,
            match_source="engram",
        )

    def test_confirmed_multi_episode_goes_to_review(self):
        from app.services.matching_coordinator import MULTI_EPISODE_ERROR_CODE

        details = {
            "multi_episode": {
                "is_multi_episode": True,
                "reason": "contiguous_runs",
                "codes": ["S01E01", "S01E02"],
            }
        }
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=2)
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        assert "S01E01, S01E02" in parsed["message"]

    def test_admitted_but_unconfirmed_goes_to_review(self):
        # The votes could not confirm it (insufficient scan depth). ASR still
        # returned one confident episode, so naming the file would drop the rest.
        from app.services.matching_coordinator import MULTI_EPISODE_ERROR_CODE

        details = {
            "multi_episode": {
                "is_multi_episode": False,
                "reason": "insufficient_scan_depth",
                "codes": [],
            }
        }
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        assert "insufficient_scan_depth" in parsed["message"]

    def test_ordinary_single_episode_is_untouched(self):
        details = {"multi_episode": {"is_multi_episode": False, "reason": "single_episode"}}
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.MATCHED
        assert "error" not in json.loads(title.match_details)

    def test_malformed_match_details_does_not_raise(self):
        title = SimpleNamespace(
            state=TitleState.MATCHED, match_details="not json", match_source="engram"
        )
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.MATCHED
```

For those tests to run, the routing logic in Step 4 must be extracted into a
module-level helper rather than being inlined, so it is testable without a DB. Define
it next to `MULTI_EPISODE_ERROR_CODE`:

```python
def _apply_multi_episode_review(title, conjoined_hint: int | None) -> bool:
    """Park a conjoined (or possibly-conjoined) track in REVIEW. Returns True if it did.

    A track the chunk votes show is several conjoined episodes must not be
    auto-organized under a single code: the other episodes would vanish from the
    library. The same applies to a track the DURATION admitted as conjoined whose
    votes could NOT confirm it (too few scan points for the run count, sparse
    coverage) -- ASR still returns one confident episode, so the silent-drop risk is
    identical. Only the reviewer-facing message differs.
    """
    multi_detail = {}
    if title.match_details:
        try:
            multi_detail = (json.loads(title.match_details) or {}).get("multi_episode") or {}
        except (json.JSONDecodeError, TypeError):
            multi_detail = {}
    confirmed_multi = bool(multi_detail.get("is_multi_episode"))
    if not (confirmed_multi or conjoined_hint):
        return False

    try:
        details = json.loads(title.match_details) if title.match_details else {}
    except (json.JSONDecodeError, TypeError):
        details = {}
    codes = multi_detail.get("codes") or []
    title.state = TitleState.REVIEW
    details["error"] = MULTI_EPISODE_ERROR_CODE
    if confirmed_multi:
        details["message"] = (
            f"This track appears to contain {len(codes)} episodes "
            f"({', '.join(codes)}). Engram cannot name a combined file yet. "
            "Assign one episode, or mark it as an Extra."
        )
    else:
        details["message"] = (
            f"This track's runtime suggests about {conjoined_hint} episodes joined "
            "together, but the audio match could not confirm it "
            f"({multi_detail.get('reason', 'no verdict')}). "
            "Check it before assigning an episode."
        )
    title.match_details = json.dumps(details)
    return True
```

Then Step 4's inlined block becomes just:

```python
                if _apply_multi_episode_review(title, conjoined_hint):
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): routed to review "
                        f"as multi-episode (hint={conjoined_hint})"
                    )
```

Add `_apply_multi_episode_review` to the test file's existing import block from
`app.services.matching_coordinator`, and make sure `SimpleNamespace` and `json` are
imported in that test file (both already are, at lines 9 and 11).

- [ ] **Step 6: Run the matching and matcher unit tests**

```bash
cd backend && uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_multi_episode.py tests/unit/test_episode_identification.py tests/unit/test_chunk_vote_gate.py tests/unit/test_curator.py -v
```

Expected: PASS. Watch specifically that `test_play_all_track_is_an_extra` and
`test_short_featurette_is_an_extra` stay green: they are the invariants this task
could plausibly break.

Then lint:

```bash
cd backend && uv run ruff check app tests && uv run ruff format --check app tests
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/matcher/episode_identification.py backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "feat(matching): admit conjoined tracks to ASR and park them for review (#622)"
```

**Deferred, deliberately:** `insufficient_scan_depth` semantically means "retry
deeper" rather than "no", and the machinery exists (`identify_episode` takes
`num_points` on the 10/19/37/73/145 lattice, already used by the deep re-match path).
This task does NOT escalate. The consequence is that a genuine THREE-segment track is
never confirmed at the default depth; it is parked for review via the
`conjoined_hint` path instead, which is safe but not automatic. The reported bug
(#622) is the two-segment case, which is fully handled at depth 10. Escalation
belongs with the multi-episode naming work.

### Task 4: Register the review code as non-rematchable

Without this, auto-escalation re-runs the matcher on the parked title and overwrites
`match_details`, destroying the multi-episode message the user needs to see.

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py:223`
- Test: `backend/tests/unit/test_auto_review_escalation.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_auto_review_escalation.py`:

```python
@pytest.mark.unit
def test_multi_episode_review_is_not_rematchable():
    """A track parked because it holds several conjoined episodes must not be
    re-matched: a denser ASR pass cannot change what the file contains, and the
    rerun overwrites the match_details message the reviewer needs (#622).
    """
    from app.services.finalization_coordinator import _is_rematchable_review

    title = SimpleNamespace(
        state=TitleState.REVIEW,
        is_extra=False,
        match_details=json.dumps(
            {
                "error": "multi_episode_detected",
                "multi_episode": {"is_multi_episode": True, "codes": ["S01E01", "S01E02"]},
            }
        ),
    )
    assert _is_rematchable_review(title) is False
```

If `SimpleNamespace`, `json`, or `TitleState` are not already imported in that file,
add them:

```python
import json
from types import SimpleNamespace

from app.models.disc_job import TitleState
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_auto_review_escalation.py -k multi_episode -v
```

Expected: FAIL, `assert True is False`.

- [ ] **Step 3: Write the implementation**

In `backend/app/services/finalization_coordinator.py`, change line 223 from:

```python
_NON_REMATCHABLE_REVIEW_ERRORS = {"file_exists", "subtitle_download_failed"} | set(
    RIP_FAILURE_ERROR_CODES
)
```

to:

```python
_NON_REMATCHABLE_REVIEW_ERRORS = {
    "file_exists",
    "subtitle_download_failed",
    # A conjoined multi-episode track: re-matching cannot change what the file
    # holds, and the rerun would overwrite the reviewer-facing message (#622).
    MULTI_EPISODE_ERROR_CODE,
} | set(RIP_FAILURE_ERROR_CODES)
```

`MULTI_EPISODE_ERROR_CODE` comes from the same module as `RIP_FAILURE_ERROR_CODES`;
extend the existing import of `RIP_FAILURE_ERROR_CODES` in this file to include it.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_auto_review_escalation.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/finalization_coordinator.py backend/tests/unit/test_auto_review_escalation.py
git commit -m "fix(review): keep conjoined-episode reviews out of auto-rematch (#622)"
```

---

### Task 5: Full-suite verification and changelog

- [ ] **Step 1: Run the backend unit tier**

```bash
cd backend && uv run pytest tests/unit/ -q
```

Expected: all pass. Note from prior sessions: `-p no:logging` speeds this tier up but
breaks roughly ten `caplog` tests, so do not add it here.

- [ ] **Step 2: Run the pipeline tier**

```bash
cd backend && uv run pytest tests/pipeline/ -q
```

Expected: all pass. `tests/pipeline/test_play_all_detection.py` is the one to watch,
it covers the disc-scoped Play All path this plan deliberately does not touch.

If it errors with `no such table`, the worktree's `engram.db` is a 0-byte stub; run
`uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"`
first.

- [ ] **Step 3: Lint and format**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

- [ ] **Step 4: Add the changelog entry**

Add under `## [Unreleased]` in `CHANGELOG.md`, in a `### Fixed` subsection:

```markdown
- Cartoon and anthology discs that pack two or three short segments into one track
  (The Weekenders, Courage the Cowardly Dog) are no longer silently filed as Extras.
  Engram now recognizes a conjoined track from the position of its speech matches and
  parks it for review with the episodes it contains listed. (#622)
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for conjoined multi-episode detection (#622)"
```

---

## Manual verification

Automated tests cover the decision logic but not the ASR path end to end. Before
opening the PR, confirm against a real conjoined disc (the reporter's case is
The Weekenders Volume 1) with exactly one backend running:

1. Rip or re-match one 23-minute track.
2. Grep the log for `fits ~2 conjoined episodes`: the admission fired.
3. Grep for `chunk votes describe 2 conjoined episodes`: the runs confirmed it.
4. Confirm the track lands in REVIEW naming both codes, and is **not** in Extras.
5. Confirm a genuine featurette on the same disc still lands in Extras.

Stop the backend and any `makemkvcon` children before opening the PR.

## Follow-on work (not this plan)

- **Multi-episode naming and organizing.** `Show - S01E01-E02` output, plus the anchored
  episode-code regexes at `backend/app/core/discord_notifier.py:196` and
  `backend/app/services/disc_contribution_queue.py:42`, which silently discard a
  compound code today. Until this lands, conjoined tracks stop at REVIEW by design.
- **`analyst_tv_min_duration` is 18 minutes** (`backend/app/models/app_config.py:91`).
  A disc exposing *individual* 11-minute segments falls below the TV range and never
  forms an episode cluster at all. Same family of bug, different layer; worth its own
  issue.

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

**Where N stops.** N is bounded by evidence density, not by a magic number: a run needs
at least 2 votes plus a seam, so `N_max ~= num_points / 3`. At the default 10 scan points
that is `N <= 3`, which is also comfortably below the 80-minute Play All floor for any
realistic segment length (11-minute segments would need N=8 to reach it).

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
4. **Balance**: each run must hold a fair share of the votes. A run of 2 votes at the tail
   against a run of 7 is a "next time on" preview bleeding in, not a second episode.

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
        verdict = decompose_vote_runs(v, total_scan_points=14)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02", "S01E03")

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
"""

from __future__ import annotations

from dataclasses import dataclass

# A code needs this many votes to count as a run rather than ASR noise. Mirrors
# EpisodeMatcher.min_vote_count so a run is never weaker than an accepted match.
MIN_RUN_VOTES = 2

# The runs must jointly account for this fraction of scan points. Below it, some
# unexplained content occupies the file (bonus features, a third unmatched
# segment), so "N episodes end to end" is the wrong description of the track.
MIN_TIMELINE_COVERAGE = 0.6

# Each run must hold at least this fraction of an equal share of the votes.
# Conjoined segments are near-equal length, so a lopsided run is a recap or a
# "next time on" preview bleeding across the boundary, not a real episode.
MIN_RUN_BALANCE = 0.5


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
        """JSON-safe form for DiscTitle.match_details."""
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


def _verdict(runs: tuple[EpisodeRun, ...], reason: str) -> MultiEpisodeVerdict:
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
        return _verdict((), "no_votes")

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
        return _verdict((), "no_votes")

    # Playback order: a disc may pair segments TMDB does not number consecutively,
    # so position on the timeline is the ordering that reflects the file.
    runs.sort(key=lambda r: r.first_start)
    ordered = tuple(runs)

    if len(ordered) == 1:
        return _verdict(ordered, "single_episode")

    # 2. Contiguity: each run must occupy its own stretch of the timeline. Any
    # overlap means the codes interleave, which is ASR confusion between similar
    # episodes rather than two episodes stored end to end.
    for earlier, later in zip(ordered, ordered[1:]):
        if later.first_start <= earlier.last_start:
            return _verdict(ordered, "interleaved_runs")

    # 3. Coverage: the runs must explain most of what was scanned.
    counted = sum(r.votes for r in ordered)
    if counted / total_scan_points < MIN_TIMELINE_COVERAGE:
        return _verdict(ordered, "sparse_coverage")

    # 4. Balance: no run may be a sliver against the others.
    fair_share = counted / len(ordered)
    if any(r.votes < fair_share * MIN_RUN_BALANCE for r in ordered):
        return _verdict(ordered, "unbalanced_runs")

    return MultiEpisodeVerdict(
        runs=ordered, is_multi_episode=True, reason="contiguous_runs"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_multi_episode.py -v
```

Expected: PASS, 10 tests.

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
and a confirmed multi-episode match goes to REVIEW instead of being auto-organized
under one of its two codes.

**Files:**
- Modify: `backend/app/matcher/episode_identification.py:1910-1916` (the `match_stats` dict)
- Modify: `backend/app/services/matching_coordinator.py:788-806` (the pre-filter branch)
- Modify: `backend/app/services/matching_coordinator.py` (post-match, near the `MATCHED` commit at ~line 1425)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Publish the verdict from the matcher**

In `backend/app/matcher/episode_identification.py`, add the import near the other
matcher imports at the top of the file:

```python
from app.matcher.multi_episode import decompose_vote_runs
```

Then replace the `match_stats` assignment (currently lines 1910-1916, beginning
`# Prepare detailed stats for return`) with:

```python
            # Prepare detailed stats for return
            # Positional vote runs: a track holding two conjoined ~11-minute
            # segments votes for E01 across the first half and E02 across the
            # second, with the seam chunk abstaining. coverages already carries
            # every vote's timestamp, so this needs no extra transcription.
            # extract_season_episode returns (None, None) for a reference name it
            # cannot parse, so filter before formatting, because an unguarded
            # "S{:02d}".format(None) would raise ValueError and abort the match.
            positional_votes = []
            for cov in coverages.values():
                _s, _e = extract_season_episode(cov.episode_name)
                if _s is None or _e is None:
                    continue
                _code = f"S{_s:02d}E{_e:02d}"
                positional_votes.extend((chunk["start"], _code) for chunk in cov.matched_chunks)
            multi_verdict = decompose_vote_runs(positional_votes, len(scan_points))
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
                "multi_episode": multi_verdict.to_dict(),
            }
```

`extract_season_episode` is already imported and used in this file (see line 1852).
No change is needed in `backend/app/core/curator.py`: `match_stats` is merged into
`best_match["match_details"]`, which flows verbatim into `DiscTitle.match_details`.

- [ ] **Step 2: Make the duration pre-filter non-terminal**

In `backend/app/services/matching_coordinator.py`, replace the body of the
`if runtimes and title_duration:` branch (currently lines 788-806) with:

```python
                if runtimes and title_duration:
                    title_minutes = title_duration / 60
                    if not _duration_matches_episode_runtime(title_minutes, runtimes):
                        # Before filing this as an extra, check whether it looks
                        # like several conjoined episodes (cartoon segment discs).
                        # If so it is ADMITTED to matching, not filed: the
                        # positional vote runs decide what it actually holds.
                        conjoined_n = _conjoined_episode_count(title_minutes, runtimes)
                        if conjoined_n:
                            logger.info(
                                f"[MATCH] Title {title_id} (Job {job_id}): duration "
                                f"{title_minutes:.0f}min matches no single episode runtime "
                                f"but fits ~{conjoined_n} conjoined episodes. Proceeding "
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

- [ ] **Step 3: Route a confirmed multi-episode match to REVIEW**

In `backend/app/services/matching_coordinator.py`, add the error-code constant next to
`RIP_FAILURE_ERROR_CODES` (line 197):

```python
# A track the vote runs showed holds several conjoined episodes. Parked for a human
# because Engram cannot yet NAME a multi-episode file (S01E01-E02 organizing is a
# separate change): auto-organizing it under one of its codes would silently lose
# the other episode.
MULTI_EPISODE_ERROR_CODE = "multi_episode_detected"
```

Then, in `_match_single_title`, immediately before the existing
`if title.state == TitleState.MATCHED and title.match_source != "engram_chromaprint":`
line (~1425), insert:

```python
                # A track the chunk votes show is several conjoined episodes must
                # not be auto-organized under a single code, because the other episode(s)
                # would vanish from the library. Park it for a human instead.
                _multi = {}
                if title.match_details:
                    try:
                        _multi = (json.loads(title.match_details) or {}).get(
                            "multi_episode"
                        ) or {}
                    except (json.JSONDecodeError, TypeError):
                        _multi = {}
                if _multi.get("is_multi_episode"):
                    _codes = _multi.get("codes") or []
                    title.state = TitleState.REVIEW
                    _md = json.loads(title.match_details)
                    _md["error"] = MULTI_EPISODE_ERROR_CODE
                    _md["message"] = (
                        f"This track appears to contain {len(_codes)} episodes "
                        f"({', '.join(_codes)}). Engram cannot name a combined file "
                        "yet. Assign one episode, or mark it as an Extra."
                    )
                    title.match_details = json.dumps(_md)
```

- [ ] **Step 4: Write the failing test**

Append to `backend/tests/unit/test_matching_coordinator.py`:

```python
@pytest.mark.unit
class TestConjoinedAdmission:
    """The duration pre-filter must ADMIT a plausibly-conjoined track to matching
    instead of filing it as an extra (issue #622), and must still file a genuine
    extra. _handle_extras is stubbed; only the branch decision is under test.
    """

    def test_conjoined_track_is_not_filed_as_extra(self):
        # 23min against [11]*16: no single runtime matches, but 2 x 11 does.
        assert _duration_matches_episode_runtime(23.0, [11] * 16) is False
        assert _conjoined_episode_count(23.0, [11] * 16) == 2

    def test_featurette_still_files_as_extra(self):
        # Neither a single runtime nor any sum: this must still reach _handle_extras.
        assert _duration_matches_episode_runtime(6.0, [11] * 16) is False
        assert _conjoined_episode_count(6.0, [11] * 16) is None
```

- [ ] **Step 5: Run the full matching + matcher unit tests**

```bash
cd backend && uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_multi_episode.py tests/unit/test_episode_identification.py tests/unit/test_chunk_vote_gate.py -v
```

Expected: PASS. Watch specifically that `test_play_all_track_is_an_extra` and
`test_short_featurette_is_an_extra` stay green: they are the invariants this task
could plausibly break.

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/episode_identification.py backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "feat(matching): admit conjoined tracks to ASR and park them for review (#622)"
```

---

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

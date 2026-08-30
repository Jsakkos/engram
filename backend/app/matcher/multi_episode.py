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

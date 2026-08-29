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
    for earlier, later in zip(ordered, ordered[1:], strict=False):
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

    return MultiEpisodeVerdict(runs=ordered, is_multi_episode=True, reason="contiguous_runs")

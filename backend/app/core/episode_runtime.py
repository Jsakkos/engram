"""Duration rules shared by the pre-rip short-title filter and the post-rip extras check.

Both decide "is this track plausibly an episode of this season?" from its length.
They live together so the two gates cannot drift: a track the pre-rip filter keeps
must be judged by the same windows when it reaches matching.
"""

from dataclasses import dataclass

# Duration pre-filter tolerances (minutes). DVD/Blu-ray episode tracks run LONGER
# than TMDB's nominal runtime: the physical track includes the "previously on"
# recap, full end credits, and "next time" preview that the broadcast-slot runtime
# figure omits. So the accept window is asymmetric — tight below an episode runtime,
# lenient above it — mirroring analyst.py's movie tolerances. A symmetric window
# centered on TMDB's underestimate wrongly rejected the season's longest real
# episodes (Gilmore Girls S01E09 "Rory's Dance": disc 49.8min vs TMDB 44min) and
# dumped them to Extras un-transcribed (bug report job 41).
EPISODE_DURATION_UNDER_TOLERANCE_MIN = 5
EPISODE_DURATION_OVER_TOLERANCE_MIN = 10


def duration_matches_episode_runtime(title_minutes: float, runtimes: list[int]) -> bool:
    """True if a track's duration is plausibly an episode for this season.

    Asymmetric window: a track may fall up to ``UNDER`` minutes short of an episode
    runtime or run up to ``OVER`` minutes past it (DVD recap + credits padding).
    """
    return any(
        (rt - EPISODE_DURATION_UNDER_TOLERANCE_MIN)
        <= title_minutes
        <= (rt + EPISODE_DURATION_OVER_TOLERANCE_MIN)
        for rt in runtimes
    )


# Cartoon and anthology discs put several short segments in one physical track:
# TMDB catalogues each ~11-minute segment as its own episode, so a 23-minute track
# matches no SINGLE runtime and was filed as an extra un-transcribed (issue #622).
# The cap is set by evidence density, not taste: the positional vote runs that
# actually decide the count need ~2 votes per run plus a seam, and the default scan
# is 10 points, so 3 is the most a default scan can resolve. It also sits far below
# the 80-minute Play All floor (analyst_movie_min_duration), and Play All titles are
# deselected pre-rip anyway (identification_coordinator), so they never arrive here.
MAX_CONJOINED_EPISODES = 3


def conjoined_episode_count(title_minutes: float, runtimes: list[int]) -> int | None:
    """Smallest ``n`` in 2..MAX for which the track looks like n conjoined episodes.

    Tests the duration against the sum of each run of ``n`` CONSECUTIVE runtimes,
    since a conjoined track holds adjacent segments, reusing the same asymmetric
    padding window as the single-episode gate (the recap/credits padding applies
    once to the whole track, not once per segment).

    This is an ADMISSION hint, not a verdict: windows for adjacent ``n`` can overlap,
    and the authoritative count comes from the positional vote runs in
    ``app.matcher.multi_episode``. Returns None when the track is not plausibly a
    small concatenation, i.e. it is a genuine extra.

    Callers must test ``duration_matches_episode_runtime`` first; a track that is a
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


# --- Pre-rip short-title filter ------------------------------------------------
#
# Skipping a real episode before the rip is expensive (the disc is ejected after
# the rip, so recovering it means finding and re-inserting the disc), while
# ripping an extra is cheap (some rip time; the post-rip extras check still files
# or discards it). So the filter skips only when EVERY available signal agrees a
# track is short, and does nothing when it has no reliable reference.

# Fewer tracks than this and there is no "rest of the disc" to compare against.
SHORT_FILTER_MIN_TITLES = 3

# Tracks whose durations sit within this distance of a group's running mean join
# the group. Matches the analyst's TV cluster variance (analyst_tv_duration_variance).
DISC_GROUP_TOLERANCE_SECONDS = 120

# With TMDB corroborating, the disc's own episode group only has to confirm the
# track is well under an episode, so a group of two and half its length suffice.
DISC_RATIO_WITH_TMDB = 0.5
DISC_MIN_GROUP_WITH_TMDB = 2

# Disc evidence alone has nothing to cross-check it, so it must be stronger: a
# bigger group (three tracks, the analyst's TV cluster size) and a shorter track.
DISC_RATIO_ALONE = 0.4
DISC_MIN_GROUP_ALONE = 3


@dataclass(frozen=True)
class ShortTitle:
    """A track the pre-rip filter would skip, with a human-readable reason."""

    index: int
    reason: str


def _fmt_min(seconds: float) -> str:
    return f"{round(seconds / 60)}m"


def _duration_groups(titles: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Group (index, seconds) pairs whose durations sit close together.

    Sorted greedy pass: a track joins the current group while it stays within the
    tolerance of the group's mean, mirroring the analyst's TV clustering.
    """
    groups: list[list[tuple[int, int]]] = []
    for item in sorted(titles, key=lambda t: t[1]):
        if groups:
            current = groups[-1]
            mean = sum(d for _, d in current) / len(current)
            if abs(item[1] - mean) <= DISC_GROUP_TOLERANCE_SECONDS:
                current.append(item)
                continue
        groups.append([item])
    return groups


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _episode_reference_seconds(
    titles: list[tuple[int, int]], runtimes: list[int], min_group: int
) -> float | None:
    """Typical episode length on this disc, or None when the disc gives no answer.

    Not a plain median of every track: a late-season disc with two episodes and six
    featurettes has a featurette-length median. Instead, take groups of
    similar-length tracks and:

    * with TMDB runtimes, prefer the largest group whose length is a plausible
      episode (single or conjoined), so featurettes cannot outvote episodes;
    * otherwise the largest group overall, ties to the longer one. When TMDB
      disagrees with the disc (TMDB lists 22-min episodes but the disc splits them
      into 11-min segments) this is the group that keeps the segments safe.
    """
    groups = [g for g in _duration_groups(titles) if len(g) >= min_group]
    if not groups:
        return None

    def rank(group: list[tuple[int, int]]) -> tuple[int, float]:
        return (len(group), _median([d for _, d in group]))

    if runtimes:
        episodic = [
            g
            for g in groups
            if duration_matches_episode_runtime(_median([d for _, d in g]) / 60, runtimes)
            or conjoined_episode_count(_median([d for _, d in g]) / 60, runtimes)
        ]
        if episodic:
            return rank(max(episodic, key=rank))[1]
    return rank(max(groups, key=rank))[1]


def find_short_titles(
    titles: list[tuple[int, int]],
    runtimes: list[int] | None,
    exclude: set[int] | None = None,
) -> list[ShortTitle]:
    """Tracks on a TV disc too short to be an episode, safe to skip before ripping.

    ``titles`` are ``(title_index, duration_seconds)`` pairs; ``runtimes`` are the
    season's TMDB episode runtimes in minutes (None or empty when unknown, or when
    the identity is not trusted enough to use them); ``exclude`` holds indices
    already decided (Play All rows), which neither count as candidates nor shape
    the disc's episode length.

    Two signals, and a track is skipped only when every signal that is available
    agrees:

    * **TMDB:** shorter than the season's SHORTEST runtime minus the under-tolerance.
      Lower bound only: long tracks are double-length pilots or conjoined
      segments, never extras to skip. (No conjoined check is needed here: n >= 2
      runtimes sum to at least twice the shortest, so a track below the shortest
      can never be a concatenation.)
    * **Disc:** shorter than a fraction of the disc's typical episode length.

    With neither signal available nothing is skipped. If the rule would skip every
    candidate, nothing is skipped: "no track here is an episode" is far likelier a
    wrong reference than a disc of nothing but bonus features.
    """
    exclude = exclude or set()
    candidates = [(i, d) for i, d in titles if i not in exclude and d and d > 0]
    if len(candidates) < SHORT_FILTER_MIN_TITLES:
        return []

    clean_runtimes = [r for r in (runtimes or []) if r and r > 0]
    tmdb_floor_min = (
        min(clean_runtimes) - EPISODE_DURATION_UNDER_TOLERANCE_MIN if clean_runtimes else None
    )

    if clean_runtimes:
        ratio, min_group = DISC_RATIO_WITH_TMDB, DISC_MIN_GROUP_WITH_TMDB
    else:
        ratio, min_group = DISC_RATIO_ALONE, DISC_MIN_GROUP_ALONE
    reference = _episode_reference_seconds(candidates, clean_runtimes, min_group)

    if tmdb_floor_min is None and reference is None:
        return []

    short: list[ShortTitle] = []
    for index, seconds in candidates:
        parts: list[str] = []
        if tmdb_floor_min is not None:
            if seconds / 60 >= tmdb_floor_min:
                continue
            parts.append(f"under the {min(clean_runtimes)}m TMDB episode runtime")
        if reference is not None:
            if seconds >= ratio * reference:
                continue
            parts.append(
                f"under {round(ratio * 100)}% of this disc's ~{_fmt_min(reference)} episodes"
            )
        short.append(
            ShortTitle(index, f"Short track ({_fmt_min(seconds)}): " + " and ".join(parts))
        )

    if len(short) >= len(candidates):
        return []
    return short

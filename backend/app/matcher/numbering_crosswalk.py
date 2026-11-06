"""Map a half-hour-numbered reference corpus onto TMDB's segment numbering.

Segment-format cartoons are catalogued twice over. TMDB lists each ~11-minute
(or ~7-minute) segment as its own episode: Hey Arnold! season 2 has 37. The
subtitle providers Engram harvests index the 22-minute broadcast half-hours those
segments were aired in, the way IMDb does: 20 for the same season. The published
pack stores those half-hours under TMDB-looking codes, so pack ``S02E06`` is
IMDb's "Ransom/Ms. Perfect", which TMDB calls E11 and E12. Filing the matcher's
code as a TMDB coordinate put every track of a disc under an unrelated segment
(#760).

The pack already marks such seasons ``divergent``. This module says how to
translate them: for each corpus code, the TMDB code(s) it holds, or nothing when
the evidence does not support an answer. Two kinds of evidence are combined
("anchored alignment", spec 2026-10-20):

1. **Anchors** from OpenSubtitles release names, which often carry the real
   titles (``S02E06.Ransom.Ms.Perfect``). An anchor needs every upload for that
   episode number to name the same titles, because the builder downloads only one
   of them and a feature's uploads can disagree.
2. **Structure** from TMDB air dates: segments that aired together on one day were
   one broadcast half-hour. Between two anchors, if the number of corpus slots
   equals the number of broadcast groups, they align in order.

Pure, no I/O: shared by both pack builders (which derive and publish the map) and
the runtime (which only applies a published one).
"""

import re
import statistics
from dataclasses import dataclass

from app.core.episode_codes import format_episode_code, parse_episode_code
from app.matcher.numbering_scheme import SCHEME_DIVERGENT

# A normalized title shorter than this is too likely to occur by accident inside
# release noise ("WEBRip", "x264") or another title to count as evidence.
MIN_TITLE_CHARS = 4

# How close a same-day group's runtime must be to a whole multiple of the
# season's median broadcast length (as a fraction of that median) before it is
# treated as several broadcasts aired back to back (Hey Arnold! S3 E30-E33).
GROUP_SPLIT_TOLERANCE = 0.25

# A segment is a short. TMDB lists Hey Arnold! segments at 11 minutes and
# Dexter's Laboratory's at 7; a sitcom aired two a night is still 22-minute
# broadcasts, not segments.
SEGMENT_MAX_MINUTES = 15

SOURCE_RELEASE_TITLE = "release_title"
SOURCE_AIR_DATE_GAP = "air_date_gap"
SOURCE_UNMAPPED = "unmapped"

_SE_MARKER_RE = re.compile(r"s\d{1,2}e\d{1,3}", re.IGNORECASE)


@dataclass(frozen=True)
class RosterEpisode:
    episode_number: int
    name: str
    air_date: str
    runtime: int


@dataclass(frozen=True)
class Crosswalk:
    # Corpus code -> canonical TMDB code. A corpus code absent here is unmapped.
    mapping: dict[str, str]
    # Provenance counts, keyed SOURCE_RELEASE_TITLE / SOURCE_AIR_DATE_GAP / SOURCE_UNMAPPED.
    sources: dict[str, int]


def _normalize(text: str) -> str:
    """Lowercase alphanumerics with every separator removed.

    Concatenating is what lets ``Mudbowl`` match "Mud Bowl" and ``Ms.Perfect``
    match "Ms. Perfect": release names use dots, underscores and spaces freely.
    """
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def _split_group(group: list[RosterEpisode], parts: int) -> list[list[RosterEpisode]] | None:
    """Split a same-day group into ``parts`` consecutive runs of near-equal runtime."""
    total = sum(e.runtime for e in group)
    target = total / parts
    runs: list[list[RosterEpisode]] = [[]]
    elapsed = 0
    for episode in group:
        if len(runs) < parts and runs[-1] and elapsed >= target * len(runs):
            runs.append([])
        runs[-1].append(episode)
        elapsed += episode.runtime
    return runs if len(runs) == parts and all(runs) else None


def broadcast_groups(roster: list[RosterEpisode]) -> list[list[int]]:
    """Group the roster into broadcast half-hours, as lists of episode numbers.

    Maximal runs of consecutive episodes sharing a non-empty air date. A run whose
    runtime is near a whole multiple k >= 2 of the median group runtime is split
    into k runs. Episodes without an air date stand alone.
    """
    ordered = sorted(roster, key=lambda e: e.episode_number)
    raw: list[list[RosterEpisode]] = []
    for episode in ordered:
        if raw and episode.air_date and raw[-1][-1].air_date == episode.air_date:
            raw[-1].append(episode)
        else:
            raw.append([episode])
    if not raw:
        return []

    median = statistics.median(sum(e.runtime for e in g) for g in raw)
    groups: list[list[int]] = []
    for group in raw:
        total = sum(e.runtime for e in group)
        k = round(total / median) if median else 1
        if k >= 2 and len(group) >= k and abs(total - k * median) <= GROUP_SPLIT_TOLERANCE * median:
            split = _split_group(group, k)
            if split:
                groups.extend([e.episode_number for e in run] for run in split)
                continue
        groups.append([e.episode_number for e in group])
    return groups


def is_segmented_season(roster: list[RosterEpisode]) -> bool:
    """Whether TMDB catalogues this season as segments aired in shared half-hours.

    The pack's ``divergent`` marker only says the subtitle corpus and the roster
    disagree on how many episodes the season holds. That is also true of an
    ordinary season the pack is merely missing a few episodes of, and treating
    such a season as half-hour numbered (no gap healing, a review hold, a
    structure-derived crosswalk) would mis-file it. A season counts as
    segmented only when TMDB's own roster says so: most broadcast groups hold
    two or more episodes, and those episodes are segment-length.
    """
    groups = broadcast_groups(roster)
    if len(groups) < 2:
        return False
    shared = sum(1 for g in groups if len(g) >= 2)
    runtimes = [e.runtime for e in roster if e.runtime]
    if not runtimes or len(runtimes) < len(roster) / 2:
        return False
    return shared * 2 >= len(groups) and statistics.median(runtimes) <= SEGMENT_MAX_MINUTES


def _titles_named(release: str, titles: list[tuple[int, str]]) -> frozenset[int]:
    """TMDB episode numbers whose titles appear in ``release`` after its SxxEyy marker.

    Text before the marker is the show name ("Hey.Arnold.S02E06...") and must not
    count. When one found title lies inside another found title, only the longer
    one counts ("Mermaid Man and Barnacle Boy" inside "... II").
    """
    marker = _SE_MARKER_RE.search(release)
    text = _normalize(release[marker.end() :] if marker else release)
    spans = []
    for number, title in titles:
        at = text.find(title)
        if at >= 0:
            spans.append((at, at + len(title), number))
    kept = [
        s
        for s in spans
        if not any(
            o != s and o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0])
            for o in spans
        )
    ]
    return frozenset(number for _, _, number in kept)


def _anchors(
    corpus_numbers: list[int],
    releases_by_episode: dict[int, list[str]],
    roster: list[RosterEpisode],
    group_of: dict[int, int],
) -> dict[int, tuple[int, int]]:
    """Corpus number -> (first group index, last group index) its releases name."""
    titles = [
        (e.episode_number, t) for e in roster if len(t := _normalize(e.name)) >= MIN_TITLE_CHARS
    ]
    anchors: dict[int, tuple[int, int]] = {}
    for number in corpus_numbers:
        releases = releases_by_episode.get(number) or []
        named = {_titles_named(r, titles) for r in releases}
        # Unanimity: a release naming nothing is dissent, not abstention.
        if len(named) != 1:
            continue
        episodes = next(iter(named))
        if not episodes:
            continue
        indices = sorted({group_of[e] for e in episodes})
        if indices != list(range(indices[0], indices[-1] + 1)):
            continue
        anchors[number] = (indices[0], indices[-1])

    # Every pair, not just neighbours: with anchors 1->E5, 2->E6, 3->E1, 4->E2 the
    # neighbour check flags only (2, 3) and keeps 1->E5 and 4->E2, still inverted.
    ordered = sorted(anchors)
    bad: set[int] = set()
    for i, earlier in enumerate(ordered):
        for later in ordered[i + 1 :]:
            if anchors[later][0] <= anchors[earlier][1]:
                bad.update((earlier, later))
    return {n: span for n, span in anchors.items() if n not in bad}


def derive_crosswalk(
    season: int,
    corpus_codes: list[str],
    roster: list[RosterEpisode],
    releases_by_episode: dict[int, list[str]] | None,
) -> Crosswalk:
    """Translate each corpus code of ``season`` into the TMDB code it holds, if knowable."""
    corpus: dict[int, str] = {}
    for code in corpus_codes:
        parsed = parse_episode_code(code)
        if parsed and parsed[0] == season and len(parsed[1]) == 1:
            corpus[parsed[1][0]] = code

    groups = broadcast_groups(roster)
    group_of = {episode: i for i, g in enumerate(groups) for episode in g}
    anchors = (
        _anchors(sorted(corpus), releases_by_episode or {}, roster, group_of) if groups else {}
    )

    assigned: dict[int, tuple[int, int, str]] = {
        n: (first, last, SOURCE_RELEASE_TITLE) for n, (first, last) in anchors.items()
    }

    # Gap fill between anchors, counted in corpus SLOTS so a half-hour the pack has
    # no subtitle for still holds its place.
    if corpus and groups:
        bounds: list[tuple[int, int | None, int | None]] = [(0, None, -1)]
        bounds += [(n, first, last) for n, (first, last) in sorted(anchors.items())]
        bounds.append((max(corpus) + 1, len(groups), None))
        for (left, _lf, left_last), (right, right_first, _rl) in zip(
            bounds, bounds[1:], strict=False
        ):
            slots = list(range(left + 1, right))
            gap_groups = list(range(left_last + 1, right_first))
            if slots and len(slots) == len(gap_groups):
                for slot, group_index in zip(slots, gap_groups, strict=True):
                    if slot in corpus:
                        assigned[slot] = (group_index, group_index, SOURCE_AIR_DATE_GAP)

    mapping: dict[str, str] = {}
    sources = {SOURCE_RELEASE_TITLE: 0, SOURCE_AIR_DATE_GAP: 0, SOURCE_UNMAPPED: 0}
    for number in sorted(corpus):
        if number not in assigned:
            sources[SOURCE_UNMAPPED] += 1
            continue
        first, last, source = assigned[number]
        episodes = [e for g in groups[first : last + 1] for e in g]
        mapping[corpus[number]] = format_episode_code(season, episodes)
        sources[source] += 1
    return Crosswalk(mapping=mapping, sources=sources)


def is_segmented_marker(marker: dict | None) -> bool:
    """True when a season marker says the corpus is half-hour numbered segments.

    The single runtime test for "no gap healing, no scraped-SRT grafting, hold an
    untranslated match for review". It needs the builder's ``segmented`` flag, not
    just ``divergent``: a pack merely missing episodes of an ordinary season is
    also ``divergent``, and its codes are real TMDB coordinates.
    """
    return (
        isinstance(marker, dict)
        and marker.get("scheme") == SCHEME_DIVERGENT
        and marker.get("segmented") is True
    )


def crosswalk_applies(marker: dict | None) -> bool:
    """True when a season marker is ``divergent`` and carries a non-empty crosswalk."""
    if not isinstance(marker, dict) or marker.get("scheme") != SCHEME_DIVERGENT:
        return False
    crosswalk = marker.get("crosswalk")
    return isinstance(crosswalk, dict) and bool(crosswalk)


def translate_codes(codes: list[str], marker: dict | None) -> list[str | None] | None:
    """Translate precomputed index codes through the marker's crosswalk.

    Returns None when no crosswalk applies (codes are used as they are). Otherwise
    one entry per input code: the TMDB code, or None for a reference to drop. A
    crosswalk that would drop every code (built against a different index) is
    treated as absent rather than emptying the corpus.
    """
    if not crosswalk_applies(marker):
        return None
    crosswalk = marker["crosswalk"]
    translated = [
        crosswalk.get(code) if isinstance(crosswalk.get(code), str) else None for code in codes
    ]
    if not any(translated):
        return None
    return translated

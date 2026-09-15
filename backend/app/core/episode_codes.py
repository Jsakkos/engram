"""Canonical parsing/formatting for episode codes, including multi-episode tracks.

A ``DiscTitle.matched_episode`` is normally one episode (``"S01E07"``), but a
single DVD track can legitimately contain several TMDB episodes — segment-format
cartoons, which pack three ~7min segments into a 22min broadcast slot, are the
common case. Those tracks are represented with the Plex/Jellyfin multi-episode
form ``"S01E01-E03"``: one canonical string, one file, several episodes.

Everything that reads an episode code should go through here rather than
re-implementing ``S(\\d+)E(\\d+)``, so a multi-episode code is either understood
or *explicitly* reduced to its first part — never silently truncated.
"""

import re

# Full-code form: a season, a first episode, and any number of "-E07"/"E07" tails.
# Anchored — a bare prefix match is exactly the silent-truncation bug this module
# exists to prevent. Non-codes ("extra", "skip", None) simply do not match.
_FULL_CODE_RE = re.compile(r"^\s*S(\d{1,3})((?:-?E\d{1,4})+)\s*$", re.IGNORECASE)
# Each episode token, with the separator that preceded it. A leading hyphen means
# "…through this episode" (Plex/Jellyfin range form); no hyphen means "and also
# this episode".
_EPISODE_PART_RE = re.compile(r"(-?)E(\d{1,4})", re.IGNORECASE)


def parse_episode_code(code: str | None) -> tuple[int, list[int]] | None:
    """``"S01E01-E03"`` → ``(1, [1, 2, 3])``. ``None`` when the code isn't an episode.

    Accepts the padded and unpadded forms the matcher can emit ("S1E14"), the
    hyphenated RANGE form ("S01E01-E03" — E01 through E03, the form Plex and
    Jellyfin document and that hand-organized libraries use), and the run-on
    form ("S01E01E03" — E01 and E03, nothing in between). A range that runs
    backwards is treated as a bare addition rather than silently inverted.
    Duplicates collapse, so "S01E02-E02" normalizes to a single episode.
    """
    match = _FULL_CODE_RE.match(code or "")
    if not match:
        return None
    season = int(match.group(1))
    episodes: list[int] = []
    for part in _EPISODE_PART_RE.finditer(match.group(2)):
        number = int(part.group(2))
        is_range = bool(part.group(1)) and bool(episodes)
        start = episodes[-1] + 1 if is_range and number > episodes[-1] else number
        for n in range(start, number + 1) if start <= number else (number,):
            if n not in episodes:
                episodes.append(n)
    if not episodes:
        return None
    return season, episodes


def format_episode_code(season: int, episodes: list[int] | tuple[int, ...]) -> str:
    """``(1, [1, 2, 3])`` → ``"S01E01-E03"``; ``(1, [1, 3])`` → ``"S01E01E03"``.

    A contiguous run uses the compact hyphenated range — the canonical string, the
    label the review UI shows, and the name the organizer writes are then all the
    same thing. A gapped set uses the run-on form, because a hyphenated range
    would claim an episode the track doesn't contain.
    """
    if not episodes:
        raise ValueError("format_episode_code requires at least one episode number")
    head, *rest = episodes
    out = f"S{season:02d}E{head:02d}"
    if not rest:
        return out
    if list(rest) == list(range(head + 1, head + 1 + len(rest))):
        return f"{out}-E{rest[-1]:02d}"
    return out + "".join(f"E{episode:02d}" for episode in rest)


def normalize_episode_code(code: str | None) -> str:
    """Canonicalize to zero-padded ``SxxExx[-Exx…]``; pass non-codes through.

    Padded/unpadded and hyphen/run-on variants of the same assignment collapse to
    one key, so collision detection and roster coverage can compare by equality.
    """
    parsed = parse_episode_code(code)
    if not parsed:
        return (code or "").upper()
    season, episodes = parsed
    return format_episode_code(season, episodes)


def episode_parts(code: str | None) -> list[str]:
    """Every single-episode code a (possibly multi-episode) code claims.

    ``"S01E01-E02"`` → ``["S01E01", "S01E02"]``. A non-code yields ``[]``. Use
    this wherever an assignment is checked *per episode* (roster coverage,
    collision detection) so a combined track occupies all of its slots.
    """
    parsed = parse_episode_code(code)
    if not parsed:
        return []
    season, episodes = parsed
    return [f"S{season:02d}E{episode:02d}" for episode in episodes]


def is_multi_episode(code: str | None) -> bool:
    """True when the code claims more than one episode."""
    parsed = parse_episode_code(code)
    return bool(parsed and len(parsed[1]) > 1)


def first_episode(code: str | None) -> tuple[int, int] | None:
    """``(season, first_episode_number)`` — for consumers that can't express a range."""
    parsed = parse_episode_code(code)
    if not parsed:
        return None
    return parsed[0], parsed[1][0]

"""How a harvested subtitle-reference season is numbered.

The subtitle providers Engram harvests and TMDB do not always number a season
the same way. For a segment-format show TMDB catalogues each ~7-minute short
(38 entries for Dexter's Laboratory season 1) while the providers index the
22-minute broadcast half-hours those shorts were assembled into (13). The
reference corpus is stored under canonical TMDB season keys either way, so a
code that comes out of the matcher looks like a TMDB coordinate whether or not
it is one. That stays invisible until something dereferences the code, and the
#200 ordering projection is the only thing that does, which is how a track
matched as S01E01 came to be filed as S01E19.mkv.

The published cache records which scheme each season was harvested in so no
consumer has to infer it. This module is the single definition of that
vocabulary, imported by both builder scripts and by the runtime, so the code
that writes the marker and the code that reads it cannot drift.

Deliberately NOT a value here: "tvdb". TheTVDB numbers Dexter's Laboratory by
segment in both its official order (38/108/36/38, identical to TMDB) and its
DVD order (39 for season 1), so the corpus's 13/40/13/13 is not TVDB numbering
and the providers' scheme matches no catalogue Engram can name. Rationale:
docs/superpowers/specs/2026-09-20-subtitle-cache-numbering-scheme-design.md.
"""

# The corpus is numbered the same way the canonical TMDB roster is, so a code
# from it IS a TMDB coordinate and may be dereferenced.
SCHEME_TMDB_AIRED = "tmdb_aired"

# The corpus and the roster disagree on how many episodes the season holds, so
# the corpus is numbered in some other scheme. Which one is not knowable here.
SCHEME_DIVERGENT = "divergent"

# Not enough information to say. An offline pack, a missing TMDB key, an
# unresolved tmdb_id, or a transient roster-lookup failure all land here. Kept
# distinct from DIVERGENT so a consumer falls back to its own heuristic rather
# than reading "we did not look" as "we looked and they disagree".
SCHEME_UNKNOWN = "unknown"

VALID_SCHEMES = frozenset({SCHEME_TMDB_AIRED, SCHEME_DIVERGENT, SCHEME_UNKNOWN})


def _usable_count(value) -> bool:
    """A count is usable only if it is a positive, genuine int.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(True, int)`` is
    True and an accidental boolean would otherwise compare as 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def derive_numbering_scheme(reference_count, roster_size) -> str:
    """Classify a season from its harvested size and its canonical roster size.

    Equal counts are a proxy for "same numbering scheme", not a proof: a season
    could coincidentally hold as many broadcast half-hours as canonical
    segments. It is the same proxy the runtime heuristic uses, computed once at
    build time against a known roster rather than per match against whatever the
    duration pre-filter happened to fetch.

    ``fetch_season_details`` returns 0, not None, for its no-key and
    transient-failure paths, so a non-positive roster must mean UNKNOWN. A TMDB
    outage during a nightly build must never brand healthy seasons DIVERGENT.
    """
    if not _usable_count(reference_count) or not _usable_count(roster_size):
        return SCHEME_UNKNOWN
    return SCHEME_TMDB_AIRED if reference_count == roster_size else SCHEME_DIVERGENT

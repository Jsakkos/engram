"""Re-validate title indices after extraction moves from the disc to its backup.

``DiscTitle`` rows are created from the *disc* scan and carry ``title_index``,
which is what the rip command passes to MakeMKV. Extraction now runs against
``file:<backup>``, so a re-scan may enumerate differently. A backup is a byte
copy and almost always enumerates identically, but "almost always" is not a
contract, and ripping the wrong index files an episode under another episode's
name with no error anywhere: the mis-file is invisible until someone browses
their library months later.

So the reconciliation here is deliberately timid. It maps an index only when
exactly one backup title can be it, and reports AMBIGUOUS otherwise. AMBIGUOUS
is meant to park the job for human review, never to trigger a guess and never
to fall back to the drive, which may already be ejected.

Design notes
------------

**Identity signal.** A title is identified by ``(source_filename, segment_map)``
plus a duration that agrees within a tolerance. ``source_filename`` (MakeMKV
TINFO attr 16, e.g. ``00001.m2ts``) and ``segment_map`` (attr 26, e.g.
``1,2,3``) name actual objects inside the disc structure, so a byte-identical
backup reproduces them exactly while the enumeration position may shift. Both
are routinely empty (DVDs, and any row scanned before those fields existed), so
duration is not merely a tiebreaker: for a disc with no per-title metadata it is
the only signal there is, and it must still be able to tell two titles apart.

**Duration is compared, never hashed.** An earlier draft bucketed the duration
into a hash key (``duration // tolerance``). That cannot express "within N
seconds": bucketing puts 2600 and 2601 in different buckets whenever the
boundary falls between them, so a one-second MakeMKV rounding difference read as
a different title. Instead the ``(source_filename, segment_map)`` pair is the
only hashed part of the key, and duration is checked with an explicit
``abs(a - b) <= tolerance`` against each candidate that shares that pair.

**Not ``output_index``.** ``DiscTitle.output_index`` is the disc-native ``_tNN``
number and is a better *rip target* than ``title_index`` (see
``ripping_helpers.expected_native_index``), but it is a poor *identity* signal
here. It is nullable on legacy rows and wherever MakeMKV supplied no suggested
filename, and the scan side does not carry a comparable field at all:
``TitleInfo`` holds the raw suggested filename (``disc_title``), from which the
number is only recovered by re-parsing. Worst of all it is derived from
MakeMKV's own numbering, which is exactly the thing a re-enumeration would
change, so it cannot corroborate itself. ``source_filename``/``segment_map``
name disc structure instead, which is what a byte copy preserves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loguru import logger

# MakeMKV rounds durations, so titles within this many seconds are the same title.
_DURATION_TOLERANCE_S = 2


class ReconcileOutcome(StrEnum):
    """How a backup re-scan lined up with the disc scan."""

    IDENTICAL = "identical"
    REMAPPED = "remapped"
    AMBIGUOUS = "ambiguous"


@dataclass
class ReconcileResult:
    outcome: ReconcileOutcome
    # {old title_index: new title_index}. Empty for IDENTICAL.
    remap: dict[int, int] = field(default_factory=dict)
    reason: str | None = None


def _identity_key(source_filename: str | None, segment_map: str | None) -> tuple[str, str]:
    """The hashable part of a title's identity, independent of enumeration order.

    Duration is deliberately excluded: it needs a tolerance, and a tolerance
    cannot be expressed as a hash bucket.
    """
    return (
        (source_filename or "").strip().lower(),
        (segment_map or "").strip(),
    )


def _same_duration(a: int, b: int) -> bool:
    return abs(a - b) <= _DURATION_TOLERANCE_S


def _is_identical(db_titles: list[Any], scanned_titles: list[Any]) -> bool:
    """True when the backup enumerated exactly what the disc scan did.

    Index and duration alone are not enough: two adjacent titles of similar
    length that swapped position would satisfy both checks pair-for-pair while
    holding each other's content. So whenever BOTH sides of a pair carry
    identity metadata (``source_filename``/``segment_map``), that metadata
    must also agree before the pair counts as identical. Only a pair with NO
    metadata on either side (e.g. a DVD scan where neither field was ever
    populated) skips that comparison and relies on index+duration alone, which
    is the case this fast path exists for.

    A pair where exactly one side carries metadata and the other doesn't is
    treated as NOT identical, on purpose: a backup re-scan losing metadata the
    disc scan had (or gaining metadata it lacked) means the two enumerations
    disagree about what there is to compare, which is itself a sign they may
    not correspond. Falling through to the fingerprint pass below is safe here
    because that pass keys strictly on matching metadata; a one-sided pair
    will fail to find a candidate there and correctly resolve to AMBIGUOUS
    rather than a silent (and possibly wrong) IDENTICAL.
    """
    if len(db_titles) != len(scanned_titles):
        return False
    for db, sc in zip(db_titles, scanned_titles, strict=True):
        if db.title_index != sc.index or not _same_duration(
            db.duration_seconds, sc.duration_seconds
        ):
            return False
        db_key = _identity_key(db.source_filename, db.segment_map)
        sc_key = _identity_key(
            getattr(sc, "source_filename", None), getattr(sc, "segment_map", None)
        )
        db_has_meta = db_key != ("", "")
        sc_has_meta = sc_key != ("", "")
        if db_has_meta and sc_has_meta:
            if db_key != sc_key:
                return False
        elif db_has_meta != sc_has_meta:
            return False
        # else: neither side has metadata; index+duration (already checked
        # above) is all there is, exactly as before this fix.
    return True


def reconcile_titles(db_titles: list[Any], scanned_titles: list[Any]) -> ReconcileResult:
    """Line up stored ``DiscTitle`` rows against a fresh scan of the backup.

    Returns IDENTICAL (reuse the stored indices), REMAPPED (with a
    ``{old: new}`` mapping to apply), or AMBIGUOUS (park the job for review;
    the ``reason`` is written to be shown to a person).

    Both inputs are sorted by index internally, so callers need not pre-sort
    either list (and a future change to either query's ORDER BY cannot change
    this function's answer).
    """
    db_titles = sorted(db_titles, key=lambda t: t.title_index)
    scanned_titles = sorted(scanned_titles, key=lambda t: t.index)

    if not scanned_titles:
        return ReconcileResult(
            ReconcileOutcome.AMBIGUOUS,
            reason=(
                "The backup was scanned but no titles were found in it, so there is "
                "nothing to rip from. The backup may be incomplete."
            ),
        )
    if not db_titles:
        return ReconcileResult(
            ReconcileOutcome.AMBIGUOUS,
            reason=(
                "No titles were recorded for this disc, so the backup scan cannot be "
                "checked against anything."
            ),
        )

    # Fast path: the overwhelmingly common case, where the byte copy enumerates
    # exactly as the disc did. Checked before any fingerprinting so that a disc
    # with no per-title metadata at all still short-circuits cleanly.
    if _is_identical(db_titles, scanned_titles):
        return ReconcileResult(ReconcileOutcome.IDENTICAL)

    buckets: dict[tuple[str, str], list[Any]] = {}
    for sc in scanned_titles:
        key = _identity_key(getattr(sc, "source_filename", None), getattr(sc, "segment_map", None))
        buckets.setdefault(key, []).append(sc)

    remap: dict[int, int] = {}
    claimed: dict[int, int] = {}  # new index -> the stored index that claimed it
    for db in db_titles:
        key = _identity_key(db.source_filename, db.segment_map)
        candidates = [
            sc
            for sc in buckets.get(key, [])
            if _same_duration(db.duration_seconds, sc.duration_seconds)
        ]
        if not candidates:
            return ReconcileResult(
                ReconcileOutcome.AMBIGUOUS,
                reason=(
                    f"Track {db.title_index} ({db.duration_seconds}s) was not found in the "
                    f"backup. The backup may be incomplete or may not match this disc."
                ),
            )
        if len(candidates) > 1:
            return ReconcileResult(
                ReconcileOutcome.AMBIGUOUS,
                reason=(
                    f"Track {db.title_index} ({db.duration_seconds}s) matches "
                    f"{len(candidates)} tracks in the backup that look identical, so "
                    f"there is no way to tell which one to rip."
                ),
            )
        winner = candidates[0].index
        # Two stored rows can each be an unambiguous match for the SAME backup
        # title (two rows one second apart both land inside one title's
        # tolerance). Neither claim is wrong on its own, so only this check
        # catches it.
        if winner in claimed:
            return ReconcileResult(
                ReconcileOutcome.AMBIGUOUS,
                reason=(
                    f"Tracks {claimed[winner]} and {db.title_index} both match the same "
                    f"track in the backup, so they cannot be told apart."
                ),
            )
        claimed[winner] = db.title_index
        remap[db.title_index] = winner

    logger.info(f"Backup re-scan remapped {len(remap)} title indices")
    return ReconcileResult(ReconcileOutcome.REMAPPED, remap=remap)

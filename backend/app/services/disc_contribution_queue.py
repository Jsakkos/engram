"""Whole-disc layout contribution enqueue (Phase C client write path).

When a disc job reaches COMPLETED, we capture the disc's content hash plus its
full title→assignment mapping and append it to the local ``disc_contributions``
table. A later uploader (Phase C-B2) drains that table to ``POST /v1/contribute-
disc``; once enough independent users contribute the same disc with the same
mapping, the network promotes it so future inserts skip audio matching.

This module is the PRODUCER half only — it never uploads. It is best-effort:
a failure here must never break job completion.
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.disc_job import ContentType, DiscJob, DiscTitle, TitleState
from app.models.fingerprint import DiscContribution

# Maps DiscTitle.match_source (the internal label) onto the server's documented
# disc-contribution source set. Mirrors matching_coordinator._MATCH_SOURCE_TO_CONTRIB
# but ALSO carries "network_disc" through unchanged: a row whose identity came
# from the network must stay identifiable as such so the anti-feedback skip can
# see it (a bare .get(..., "engram_asr") default would wrongly relabel it).
_DISC_MATCH_SOURCE_TO_CONTRIB: dict[str, str] = {
    "engram": "engram_asr",
    "engram_chromaprint": "engram_chromaprint_corroboration",
    "discdb": "engram_discdb",
    "ai_llm": "engram_asr",
    "user": "user_review",
    "network_disc": "network_disc",
}

# Assignments that carry disc identity (vs. extras/discarded which are layout only).
_REAL_ASSIGNMENTS = ("episode", "main_movie")

_EPISODE_RE = re.compile(r"^S(\d+)E(\d+)$", re.IGNORECASE)


def _map_source(match_source: str | None) -> str:
    """Map a DiscTitle.match_source onto the server's documented value set.

    network_disc is preserved (not relabeled) so the anti-feedback skip works.
    """
    return _DISC_MATCH_SOURCE_TO_CONTRIB.get(match_source or "", "engram_asr")


def _derive_assignment(job: DiscJob, title: DiscTitle) -> tuple[str, int | None, int | None]:
    """Classify a title into (assignment, season, episode).

    - is_extra → "extra"
    - TV episode (matched_episode matches S<d>E<d>, not extra) → "episode"
    - MOVIE kept main feature (content_type MOVIE, not extra, organized) → "main_movie"
    - anything else (unmatched / skipped / discarded) → "discarded"

    Note on the TV/MOVIE asymmetry: a TV title keeps "episode" purely from its
    SxxExx code, regardless of organize state — the code itself is the identity.
    A MOVIE main feature has no code to key on, so it additionally requires the
    kept/COMPLETED (organized) state before we call it "main_movie". This asymmetry
    is intentional and conservative: without a code, organize state is the only
    signal that the title is the feature rather than a discarded/extra track.

    Note on multi-episode codes: a combined code like "S01E01E02" does NOT match
    the single-episode _EPISODE_RE and falls through to "discarded". That is a
    deliberate, safe under-count — better to drop the row than to emit a wrong
    single-episode assignment (e.g. claiming the title is only S01E01).
    """
    if title.is_extra:
        return "extra", None, None

    code = (title.matched_episode or "").strip()
    m = _EPISODE_RE.match(code)
    if m and code.lower() != "extra":
        return "episode", int(m.group(1)), int(m.group(2))

    # Movie main feature: the kept, organized (COMPLETED) non-extra title on a
    # MOVIE job. Movies have no episode code (matched_episode is None), so they
    # never hit the episode branch above. Extras are already routed out.
    if job.content_type == ContentType.MOVIE and title.state == TitleState.COMPLETED:
        return "main_movie", None, None

    return "discarded", None, None


def build_title_rows(job: DiscJob, titles: list[DiscTitle]) -> list[dict]:
    """Build the per-title layout rows for the contribution payload.

    Includes ALL titles (episode/main_movie/extra/discarded) so the server sees
    the full disc layout — the digest is over the identity-bearing rows but the
    full set is the contribution.
    """
    rows: list[dict] = []
    for title in titles:
        assignment, season, episode = _derive_assignment(job, title)
        rows.append(
            {
                "title_index": title.title_index,
                "duration_seconds": title.duration_seconds,
                "size_bytes": title.file_size_bytes,
                "assignment": assignment,
                "season": season,
                "episode": episode,
                "match_confidence": float(title.match_confidence or 0.0),
                "match_source": _map_source(title.match_source),
            }
        )
    return rows


async def enqueue_disc_contribution(
    session: AsyncSession,
    job: DiscJob,
    titles: list[DiscTitle],
    *,
    contributions_enabled: bool,
    pseudonym: str | None,
) -> None:
    """Append a whole-disc contribution row if the disc is eligible.

    Best-effort: any failure is logged and swallowed so job completion is never
    broken. Skips silently (debug log) when a gate fails or the disc has nothing
    worth contributing.
    """
    label = f"job {job.id}"
    try:
        # --- Gates ------------------------------------------------------------
        if not contributions_enabled:
            logger.debug(f"Skipping disc contribution for {label}: contributions disabled")
            return
        if not pseudonym:
            logger.debug(f"Skipping disc contribution for {label}: no pseudonym")
            return
        if not getattr(job, "content_hash", None):
            logger.debug(f"Skipping disc contribution for {label}: no content_hash")
            return
        try:
            disc_hash = bytes.fromhex(job.content_hash)
        except (TypeError, ValueError):
            logger.debug(f"Skipping disc contribution for {label}: unparseable content_hash")
            return
        if not getattr(job, "tmdb_id", None):
            logger.debug(f"Skipping disc contribution for {label}: no tmdb_id")
            return
        try:
            tmdb_id_val = int(job.tmdb_id)
        except (TypeError, ValueError):
            logger.debug(f"Skipping disc contribution for {label}: non-int tmdb_id")
            return
        if job.content_type not in (ContentType.TV, ContentType.MOVIE):
            logger.debug(f"Skipping disc contribution for {label}: content_type {job.content_type}")
            return

        # --- Build rows -------------------------------------------------------
        rows = build_title_rows(job, titles)
        real_rows = [r for r in rows if r["assignment"] in _REAL_ASSIGNMENTS]

        # Require at least one identity-bearing assignment — otherwise there is
        # nothing identified worth contributing.
        if not real_rows:
            logger.debug(f"Skipping disc contribution for {label}: no real assignments")
            return

        # Anti-feedback: if EVERY real assignment came from the network, this
        # disc's identity/mapping was handed to us by the network — re-contributing
        # it would create a feedback loop (mirrors the server's exclusion). A disc
        # with at least one independently-matched (asr/discdb/user) real assignment
        # still enqueues.
        if all(r["match_source"] == "network_disc" for r in real_rows):
            logger.debug(
                f"Skipping disc contribution for {label}: all real assignments from network_disc"
            )
            return

        # --- Insert -----------------------------------------------------------
        season = (
            getattr(job, "detected_season", None) if job.content_type == ContentType.TV else None
        )
        titles_json = json.dumps(rows)

        # Idempotency guard. A job can transition COMPLETED → COMPLETED again (the
        # state machine treats a same-state transition as success and re-fires its
        # terminal callbacks), so this enqueue can run twice for one disc. Mirror
        # the server's dedup key — (pseudonym, disc_content_hash, titles_json) — and
        # skip if an identical row already exists. Keying on titles_json (not the
        # hash alone) is deliberate: a CORRECTED mapping (e.g. the user re-reviewed
        # an episode assignment) produces a different titles_json and is NEW evidence
        # that MUST still enqueue. Best-effort, like the rest of this function.
        existing = (
            await session.execute(
                select(DiscContribution)
                .where(DiscContribution.pseudonym == pseudonym)
                .where(DiscContribution.disc_content_hash == disc_hash)
                .where(DiscContribution.titles_json == titles_json)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.debug(
                f"Skipping disc contribution for {label}: already queued for this disc+mapping"
            )
            return

        row = DiscContribution(
            disc_content_hash=disc_hash,
            tmdb_id=tmdb_id_val,
            content_type=job.content_type.value,  # "tv" | "movie"
            season=season,
            titles_json=titles_json,
            pseudonym=pseudonym,
            upload_status=None,
        )
        session.add(row)
        logger.info(
            f"Queued disc contribution for {label} "
            f"(tmdb={tmdb_id_val}, type={job.content_type.value}, season={season}, "
            f"titles={len(rows)}, real={len(real_rows)})"
        )
    except Exception as e:  # noqa: BLE001 — best-effort; never break completion
        logger.warning(f"Failed to enqueue disc contribution for {label}: {e}", exc_info=True)

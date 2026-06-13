"""Network disc-identification read path (walk-away Phase C, client side).

Given a disc's content hash, query the companion fingerprint server's
``GET /v1/identify-disc`` endpoint. On a crowd-promoted hit the server returns
an identity (tmdb_id + content type) and a per-title episode mapping, letting a
disc the network has already seen be identified with ZERO audio matching.

**Best-effort semantics.** This is a supplementary signal layered onto the
existing classification pipeline. Every failure mode — network error, HTTP
error, malformed JSON, a garbage/odd-length content hash, or a miss — returns
``None`` and NEVER raises, so classification proceeds with TMDB/AI/heuristics
exactly as before when the network is unreachable or unhelpful.

**Hash encoding.** ``content_hash`` is the uppercase-hex MD5 string stored on
the job (TheDiscDB-compatible fingerprint). The server keys discs on the RAW
MD5 bytes, base64url-encoded WITHOUT padding. We therefore decode the hex back
to the 16 raw bytes and re-encode: ``base64.urlsafe_b64encode(raw).rstrip(b"=")``.
This MUST match how the WRITE path (uploader) encodes ``disc_content_hash`` so a
disc we contributed can later be looked up.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from loguru import logger

from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL

# Assignments that represent an actual episode/feature placement (everything
# else — "extra"/"discarded" — is not an episode mapping and is never applied).
_MAPPABLE_ASSIGNMENTS = {"episode", "main_movie"}

# assignment string -> DiscDbTitleMapping.title_type vocabulary.
_ASSIGNMENT_TITLE_TYPE = {"episode": "Episode", "main_movie": "MainMovie"}

# Verification tolerances for binding a network title to a scanned title.
# MakeMKV version drift can shuffle title indices, so we never trust the
# network's title_index blindly — we re-bind by physical signature.
_DURATION_TOLERANCE_SECONDS = 2
_SIZE_TOLERANCE_FRACTION = 0.01  # +-1%


@dataclass
class NetworkDiscTitle:
    """One title's crowd-promoted assignment from the disc network."""

    title_index: int
    duration_seconds: int
    size_bytes: int
    assignment: str  # "episode" | "main_movie" | "extra" | "discarded"
    season: int | None = None
    episode: int | None = None
    match_confidence: float = 0.0
    match_source: str = ""


@dataclass
class NetworkDiscSignal:
    """Crowd-promoted identity + title mapping for a recognized disc."""

    tmdb_id: int
    content_type: str  # "tv" | "movie"
    season: int | None
    tier: str  # "candidate" | "confirmed" | "canonical"
    unique_contributors: int
    mean_confidence: float
    titles: list[NetworkDiscTitle]


def _resolve_base_url(server_url: str | None) -> str:
    """Effective base origin, mirroring RemoteIdentifyBackend.

    Blank/None -> the packaged default. Trailing slash trimmed so the
    ``/v1/identify-disc`` suffix joins cleanly.
    """
    base = (server_url or "").strip() or DEFAULT_FINGERPRINT_SERVER_URL
    return base.rstrip("/")


def _encode_hash(content_hash: str) -> str:
    """Uppercase-hex MD5 -> raw bytes -> base64url WITHOUT padding.

    Raises ValueError for a malformed (odd-length / non-hex) string; callers
    treat that as a miss.
    """
    raw = bytes.fromhex(content_hash)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _parse_signal(data: object) -> NetworkDiscSignal | None:
    """Parse a ``/v1/identify-disc`` body into a signal, or None on miss/garbage."""
    if not isinstance(data, dict):
        return None
    disc = data.get("disc")
    if not isinstance(disc, dict):
        # miss ({"disc": null}) or missing key
        return None
    try:
        tmdb_id = int(disc["tmdb_id"])
        content_type = str(disc["content_type"])
        tier = str(disc["tier"])
    except (KeyError, TypeError, ValueError):
        # Required identity fields absent/garbage -> treat as a miss.
        return None

    season = disc.get("season")
    season = int(season) if isinstance(season, int) else None

    titles: list[NetworkDiscTitle] = []
    for t in disc.get("titles") or []:
        if not isinstance(t, dict):
            continue
        try:
            ep_season = t.get("season")
            ep_episode = t.get("episode")
            titles.append(
                NetworkDiscTitle(
                    title_index=int(t["title_index"]),
                    duration_seconds=int(t["duration_seconds"]),
                    size_bytes=int(t["size_bytes"]),
                    assignment=str(t["assignment"]),
                    season=int(ep_season) if isinstance(ep_season, int) else None,
                    episode=int(ep_episode) if isinstance(ep_episode, int) else None,
                    match_confidence=float(t.get("match_confidence", 0.0)),
                    match_source=str(t.get("match_source", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Skip an individual malformed title rather than failing the disc.
            continue

    return NetworkDiscSignal(
        tmdb_id=tmdb_id,
        content_type=content_type,
        season=season,
        tier=tier,
        unique_contributors=int(disc.get("unique_contributors", 0) or 0),
        mean_confidence=float(disc.get("mean_confidence", 0.0) or 0.0),
        titles=titles,
    )


async def identify_disc_via_network(
    content_hash: str,
    server_url: str | None,
    *,
    timeout: float = 5.0,
) -> NetworkDiscSignal | None:
    """Look up a disc by content hash on the fingerprint network.

    Best-effort: returns the parsed :class:`NetworkDiscSignal` on a hit, or
    ``None`` for a miss or ANY error (network/HTTP/JSON/malformed hash). Never
    raises — classification must proceed regardless.
    """
    if not content_hash:
        return None
    try:
        fp = _encode_hash(content_hash)
    except ValueError as e:
        # Malformed content hash (odd-length / non-hex). Not actionable.
        logger.debug(f"Network disc identify: invalid content hash {content_hash!r}: {e}")
        return None

    base = _resolve_base_url(server_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base}/v1/identify-disc", params={"hash": fp})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(f"Network disc identify failed (best-effort, ignoring): {e}")
        return None

    return _parse_signal(data)


def network_titles_to_mappings(
    net_titles: list[NetworkDiscTitle],
    scanned_titles: list,
):
    """Convert network titles into verified ``DiscDbTitleMapping``s.

    Only ``episode`` / ``main_movie`` assignments are considered (``extra`` /
    ``discarded`` are skipped). Each candidate is bound to an actually-scanned
    title whose ``duration_seconds`` is within +-2s AND ``size_bytes`` within
    +-1% — this re-derives the title index from on-disc evidence and guards
    against MakeMKV-version drift renumbering titles. A network title that no
    scanned title verifies is dropped. The emitted mapping carries the SCANNED
    title's index/duration/size and ``source="network_disc"``.

    ``scanned_titles`` items must expose ``index``, ``duration_seconds`` and
    ``size_bytes`` (TitleInfo from the analyst scan).
    """
    from app.core.discdb_classifier import DiscDbTitleMapping

    mappings: list[DiscDbTitleMapping] = []
    used_indices: set[int] = set()

    for net in net_titles:
        if net.assignment not in _MAPPABLE_ASSIGNMENTS:
            continue

        match = None
        for scanned in scanned_titles:
            if scanned.index in used_indices:
                continue
            if abs(scanned.duration_seconds - net.duration_seconds) > _DURATION_TOLERANCE_SECONDS:
                continue
            ref_size = net.size_bytes
            if ref_size > 0:
                allowed = ref_size * _SIZE_TOLERANCE_FRACTION
                if abs(scanned.size_bytes - ref_size) > allowed:
                    continue
            elif scanned.size_bytes != 0:
                # Network reported no size; require the scanned size to be 0 too
                # rather than blindly accepting any size.
                continue
            match = scanned
            break

        if match is None:
            continue

        used_indices.add(match.index)
        mappings.append(
            DiscDbTitleMapping(
                index=match.index,
                title_type=_ASSIGNMENT_TITLE_TYPE[net.assignment],
                episode_title="",
                season=net.season,
                episode=net.episode,
                duration_seconds=match.duration_seconds,
                size_bytes=match.size_bytes,
                source="network_disc",
            )
        )

    return mappings

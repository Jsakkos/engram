"""Analyst - Disc Identification and Classification Engine.

Analyzes disc structure to determine content type (TV/Movie) using heuristics,
optionally enhanced by TMDB lookup signals.
"""

import logging
import re
from dataclasses import dataclass, field

from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)

# Generic Windows/disc placeholder volume labels that carry no meaningful title information.
# Normalized form: uppercased with underscores and spaces removed.
_GENERIC_VOLUME_LABELS: frozenset[str] = frozenset(
    {
        "LOGICALVOLUMEID",
        "VIDEOTS",
        "BDMV",
        "DISC",
        "DVD",
        "DVDVIDEO",
        "DVDVOLUME",
        "DVDDISC",
        "BDROM",
        "BLURAYDISC",
        "BLURAY",
        "BD",
        "NOLABEL",
        "UNTITLED",
        "VOLUME",
        "NEWVOLUME",
    }
)

# Studio name prefixes commonly found on disc volume labels.
# Stripped before title parsing to avoid TMDB matching the studio name instead.
_STUDIO_PREFIXES: tuple[str, ...] = (
    "MARVEL_STUDIOS_",
    "WARNER_BROS_",
    "WARNER_BROTHERS_",
    "UNIVERSAL_",
    "PARAMOUNT_",
    "DISNEY_",
    "20TH_CENTURY_",
    "TWENTIETH_CENTURY_",
    "COLUMBIA_",
    "LIONSGATE_",
    "MGM_",
    "DREAMWORKS_",
    "NEW_LINE_",
    "TOUCHSTONE_",
    "MIRAMAX_",
)


def _title_tokens(s: str) -> set[str]:
    """Tokenize a title into lowercased words, dropping punctuation and 1-char tokens."""
    return {w.lower() for w in re.sub(r"[^\w\s]", "", s).split() if len(w) > 1}


def _collapsed(s: str) -> str:
    """Lowercase a title with all non-alphanumeric characters removed.

    Lets us recognize that a separator-less volume label and a spaced title are
    the same name: "Breakingbad" and "Breaking Bad" both collapse to "breakingbad".
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _is_generic_disc_name(name: str) -> bool:
    """True if a DINFO disc name is a generic placeholder (e.g. 'Blu-ray disc')."""
    return re.sub(r"[^A-Z0-9]", "", name.upper()) in _GENERIC_VOLUME_LABELS


def _names_are_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Return True if two title strings refer to the same title.

    Two acceptance paths:
      1. Word-token Jaccard >= threshold (handles punctuation / word-order noise).
      2. Whitespace/punctuation-insensitive equality (handles a separator-less
         volume label vs. a spaced title, e.g. "Breakingbad" == "Breaking Bad").

    Path 2 is conservative: it only matches names that are identical once spacing
    and punctuation are removed, so it never makes unrelated titles match
    ("breakingbad" != "friends").
    """
    a_tok, b_tok = _title_tokens(a), _title_tokens(b)
    if not a_tok or not b_tok:
        return True  # Can't compare — allow override
    if len(a_tok & b_tok) / len(a_tok | b_tok) >= threshold:
        return True
    a_collapsed, b_collapsed = _collapsed(a), _collapsed(b)
    return bool(a_collapsed) and a_collapsed == b_collapsed


@dataclass
class TitleInfo:
    """Information about a single title on a disc."""

    index: int
    duration_seconds: int
    size_bytes: int
    chapter_count: int
    name: str = ""
    video_resolution: str = ""
    source_filename: str = ""  # e.g., "00001.m2ts" (MakeMKV TINFO attr 16)
    segment_count: int = 0  # MakeMKV TINFO attr 25
    segment_map: str = ""  # e.g., "1,2,3" (MakeMKV TINFO attr 26)
    disc_title: str = ""  # e.g., "Show - Season 3_t00.mkv" (MakeMKV TINFO attr 27)


@dataclass
class DiscAnalysisResult:
    """Result of analyzing a disc's content."""

    content_type: ContentType
    titles: list[TitleInfo] = field(default_factory=list)
    detected_name: str | None = None
    detected_season: int | None = None
    confidence: float = 0.0
    needs_review: bool = False
    review_reason: str | None = None
    tmdb_id: int | None = None
    tmdb_name: str | None = None
    classification_source: str = "heuristic"
    play_all_title_indices: list[int] = field(default_factory=list)
    is_ambiguous_movie: bool = False


# Main-feature selection tuning. A movie disc only needs review when 2+ titles
# genuinely qualify as the feature (alternate versions / obfuscation); long bonus
# tracks must not be mistaken for the feature.
MOVIE_MIN_FEATURE_DURATION = 80 * 60  # floor (seconds) for a title to be feature-eligible
MOVIE_RUNTIME_TOLERANCE_MIN = 10  # minutes a title may fall short of the TMDB runtime
MOVIE_EXTENDED_ALLOWANCE_MIN = 45  # minutes a title may exceed runtime (extended/director's cut)
MOVIE_FALLBACK_PROXIMITY = 0.75  # no-runtime: candidate if >= 75% of the longest eligible title
MOVIE_CANDIDATE_BITRATE_FLOOR = 0.4  # drop candidates below 40% of the top candidate's bitrate


@dataclass
class MainFeatureDecision:
    """Outcome of choosing the main feature among a movie disc's titles."""

    feature_index: int | None
    extra_indices: list[int] = field(default_factory=list)
    candidate_indices: list[int] = field(default_factory=list)
    needs_review: bool = False
    review_reason: str | None = None


def _bitrate(title: TitleInfo) -> float:
    """Bytes per second — a proxy for video quality used to tell features from docs."""
    if not title.duration_seconds:
        return 0.0
    return (title.size_bytes or 0) / title.duration_seconds


def select_movie_main_feature(
    titles: list[TitleInfo],
    runtime_minutes: int | None,
    *,
    min_feature_duration: int = MOVIE_MIN_FEATURE_DURATION,
) -> MainFeatureDecision:
    """Pick the main feature among a movie disc's titles.

    Uses the movie's TMDB runtime when available to identify the feature, falling
    back to duration proximity. A low-bitrate filter rejects long documentaries
    that happen to sit near the runtime. Review is only required when 2+ titles
    remain plausible features (theatrical vs. extended cut, or copy-protection
    playlist duplication); everything else is tagged as an extra.
    """
    if not titles:
        return MainFeatureDecision(feature_index=None)

    eligible = [t for t in titles if (t.duration_seconds or 0) >= min_feature_duration]

    # No title clears the feature floor — pick the longest and move on (no review).
    if not eligible:
        feature = max(titles, key=lambda t: t.duration_seconds or 0)
        return _single_feature(titles, feature)

    if runtime_minutes and runtime_minutes > 0:
        low = (runtime_minutes - MOVIE_RUNTIME_TOLERANCE_MIN) * 60
        high = (runtime_minutes + MOVIE_EXTENDED_ALLOWANCE_MIN) * 60
        candidates = [t for t in eligible if low <= t.duration_seconds <= high]
        if not candidates:
            # Runtime wildly off (e.g. wrong TMDB id) — ignore it and use duration.
            candidates = _proximity_candidates(eligible)
    else:
        candidates = _proximity_candidates(eligible)

    candidates = _filter_by_bitrate(candidates)
    if not candidates:
        candidates = [max(eligible, key=lambda t: t.duration_seconds or 0)]

    if len(candidates) == 1:
        return _single_feature(titles, candidates[0])

    candidate_indices = [t.index for t in candidates]
    extras = [t.index for t in titles if t.index not in candidate_indices]
    return MainFeatureDecision(
        feature_index=None,
        extra_indices=extras,
        candidate_indices=candidate_indices,
        needs_review=True,
        review_reason=(
            "Multiple feature-length titles found. "
            "Please select the correct version (theatrical, extended, etc.)."
        ),
    )


def _single_feature(titles: list[TitleInfo], feature: TitleInfo) -> MainFeatureDecision:
    """Build a no-review decision with one feature and everything else as extras."""
    return MainFeatureDecision(
        feature_index=feature.index,
        extra_indices=[t.index for t in titles if t.index != feature.index],
        candidate_indices=[feature.index],
    )


def _proximity_candidates(eligible: list[TitleInfo]) -> list[TitleInfo]:
    """Feature candidates by duration proximity to the longest eligible title."""
    longest = max(eligible, key=lambda t: t.duration_seconds or 0)
    threshold = longest.duration_seconds * MOVIE_FALLBACK_PROXIMITY
    return [t for t in eligible if (t.duration_seconds or 0) >= threshold]


def _filter_by_bitrate(candidates: list[TitleInfo]) -> list[TitleInfo]:
    """Drop candidates whose bitrate is far below the best — likely low-quality docs."""
    if len(candidates) <= 1:
        return candidates
    top = max(_bitrate(t) for t in candidates)
    if top <= 0:
        return candidates
    return [t for t in candidates if _bitrate(t) >= top * MOVIE_CANDIDATE_BITRATE_FLOOR]


class DiscAnalyst:
    """Analyzes disc structure to classify content type."""

    def __init__(self, config=None):
        """Initialize analyst with optional configuration.

        Args:
            config: AppConfig instance. If None, loads from database.
        """
        self._config = config

    def _get_config(self):
        """Get config, loading from database if not provided."""
        if self._config is None:
            from app.services.config_service import get_config_sync

            self._config = get_config_sync()
        return self._config

    def set_config(self, config) -> None:
        """Set config from an async caller to avoid blocking get_config_sync()."""
        self._config = config

    def analyze(
        self,
        titles: list[TitleInfo],
        volume_label: str = "",
        tmdb_signal=None,
        name_hint: str | None = None,
    ) -> DiscAnalysisResult:
        """Analyze a list of titles to determine content type.

        Args:
            titles: List of title information from MakeMKV
            volume_label: The disc's volume label (e.g., "THE_OFFICE_S1")
            tmdb_signal: Optional TmdbSignal from TMDB lookup
            name_hint: Override for detected_name when a better source than the volume
                label is available (e.g., MakeMKV's DINFO disc name). Bypasses the
                _names_are_similar guard so the TMDB name flows through cleanly.

        Returns:
            Analysis result with content type and confidence
        """
        logger.info(f"Analyzing disc: '{volume_label}' with {len(titles)} titles")
        if tmdb_signal:
            logger.info(f"TMDB signal: {tmdb_signal}")

        if not titles:
            return DiscAnalysisResult(
                content_type=ContentType.UNKNOWN,
                titles=[],
                needs_review=True,
                review_reason="No titles found on disc",
            )

        # Log title durations for debugging
        durations_str = ", ".join([f"{t.duration_seconds // 60}min" for t in titles[:10]])
        if len(titles) > 10:
            durations_str += f", ... ({len(titles) - 10} more)"
        logger.info(f"Title durations: {durations_str}")

        # Try to extract show name, season, and disc from volume label
        label_name, detected_season, detected_disc = self._parse_volume_label(volume_label)
        # name_hint (e.g. from MakeMKV DINFO) overrides the volume-label-parsed name
        detected_name = name_hint if name_hint else label_name

        # If we found a season pattern (S01D02), it's very likely a TV show
        is_likely_tv = detected_season is not None
        if is_likely_tv:
            logger.info(f"Volume label indicates TV (season {detected_season})")

        # Use TMDB name only if it's semantically related to the parsed name.
        # name_hint already comes from a trusted source (DINFO), so bypass the guard for it.
        effective_name = detected_name
        if tmdb_signal and tmdb_signal.tmdb_name:
            if (
                name_hint
                or detected_name is None
                or _names_are_similar(detected_name, tmdb_signal.tmdb_name)
            ):
                effective_name = tmdb_signal.tmdb_name
            else:
                logger.warning(
                    f"TMDB name '{tmdb_signal.tmdb_name}' is dissimilar to parsed name "
                    f"'{detected_name}' — ignoring TMDB name override"
                )

        # ALWAYS check for movie first (content overrides label)
        movie_result = self._detect_movie(titles)
        logger.info(f"Movie detection result: {movie_result}")

        # Check for TV show (cluster of similar-duration titles)
        tv_result = self._detect_tv_show(titles)
        if tv_result:
            logger.info(
                f"TV show detected with {tv_result['confidence']:.1%} confidence "
                f"({tv_result['episode_count']} episodes)"
            )

        # Detect Play All titles (run once, used by all TV return paths)
        play_all = self._detect_play_all(titles, tv_result)

        # CONFLICT RESOLUTION: If both are detected, decided which one to trust.
        if movie_result and not movie_result.get("ambiguous") and tv_result:
            # We have a valid movie AND a valid TV show.
            # This is almost always a TV disc with a "Play All" feature (the "Movie").
            logger.info(
                "Conflict: Movie & TV detected. Preferring TV (assuming 'Play All' feature)."
            )
            result = self._tv_result(
                titles, effective_name, detected_season, play_all, tv_result["confidence"]
            )
            return self._apply_tmdb_signal(result, tmdb_signal)

        # CONFLICT RESOLUTION 2: Movie detected + no TV cluster, but label says TV
        # AND we found a Play All candidate via fallback — the "movie" is the Play All.
        if movie_result and not movie_result.get("ambiguous") and is_likely_tv and play_all:
            logger.info(
                "Conflict: Movie detected but label indicates TV and Play All found. "
                "Preferring TV (movie is likely the 'Play All' concatenation)."
            )
            # Moderate-high confidence: label + Play All detection
            result = self._tv_result(titles, effective_name, detected_season, play_all, 0.75)
            return self._apply_tmdb_signal(result, tmdb_signal)

        # No conflict: Clear Movie
        if movie_result:
            if not movie_result.get("ambiguous"):
                logger.info(f"Movie detected with {movie_result['confidence']:.1%} confidence")
                result = DiscAnalysisResult(
                    content_type=ContentType.MOVIE,
                    titles=titles,
                    detected_name=effective_name,
                    confidence=movie_result["confidence"],
                )
                return self._apply_tmdb_signal(result, tmdb_signal)

            # If ambiguous movie (e.g. multiple long titles), we'll hold onto it
            # and see if TV detection makes more sense.
            logger.info(f"Ambiguous movie detected: {movie_result.get('reason')}")

        # No conflict: Clear TV
        if tv_result:
            result = self._tv_result(
                titles, effective_name, detected_season, play_all, tv_result["confidence"]
            )
            return self._apply_tmdb_signal(result, tmdb_signal)

        # If we have an ambiguous movie result and NO TV result, return the ambiguous movie result
        if movie_result and movie_result.get("ambiguous"):
            result = DiscAnalysisResult(
                content_type=ContentType.MOVIE,
                titles=titles,
                detected_name=effective_name,
                confidence=0.0,
                needs_review=True,
                review_reason=movie_result["reason"],
                is_ambiguous_movie=True,
            )
            return self._apply_tmdb_signal(result, tmdb_signal)

        # If volume label indicates TV (has season pattern) but heuristics didn't detect it,
        # trust the volume label with moderate confidence
        if is_likely_tv:
            logger.info(
                f"Volume label indicates TV show (season {detected_season}), trusting label"
            )
            # Moderate confidence based on volume label
            result = self._tv_result(titles, effective_name, detected_season, play_all, 0.7)
            return self._apply_tmdb_signal(result, tmdb_signal)

        # No heuristic result — if TMDB has a signal, use it directly
        if tmdb_signal and tmdb_signal.content_type != ContentType.UNKNOWN:
            logger.info(
                f"Heuristics inconclusive, using TMDB signal: "
                f"{tmdb_signal.content_type.value} ({tmdb_signal.confidence:.0%})"
            )
            # For TMDB-only TV classification, use fallback Play All detection
            tmdb_play_all = play_all if tmdb_signal.content_type == ContentType.TV else []
            return DiscAnalysisResult(
                content_type=tmdb_signal.content_type,
                titles=titles,
                detected_name=effective_name,
                detected_season=detected_season,
                confidence=tmdb_signal.confidence,
                tmdb_id=tmdb_signal.tmdb_id,
                tmdb_name=tmdb_signal.tmdb_name,
                classification_source="tmdb",
                play_all_title_indices=tmdb_play_all,
            )

        # Ambiguous - needs human review
        reason = self._get_ambiguity_reason(titles)
        logger.info(f"Unable to classify disc: {reason}")
        return DiscAnalysisResult(
            content_type=ContentType.UNKNOWN,
            titles=titles,
            detected_name=effective_name,
            detected_season=detected_season,
            needs_review=True,
            review_reason=reason,
        )

    def _tv_result(
        self,
        titles: list[TitleInfo],
        effective_name: str | None,
        detected_season: int | None,
        play_all: list[int],
        confidence: float,
    ) -> DiscAnalysisResult:
        """Build a TV DiscAnalysisResult with the given confidence."""
        return DiscAnalysisResult(
            content_type=ContentType.TV,
            titles=titles,
            detected_name=effective_name,
            detected_season=detected_season,
            confidence=confidence,
            play_all_title_indices=play_all,
        )

    def _apply_tmdb_signal(self, result: DiscAnalysisResult, tmdb_signal) -> DiscAnalysisResult:
        """Apply TMDB signal to a heuristic result.

        If TMDB agrees with the heuristic, boost confidence.
        If TMDB disagrees, override with appropriate confidence.
        If no TMDB signal, return the heuristic result unchanged.
        """
        if tmdb_signal is None or tmdb_signal.content_type == ContentType.UNKNOWN:
            result.classification_source = "heuristic"
            return result

        # Same-name collision (item 1): two real same-name TMDB shows. Don't commit
        # an id — force review and let the user pick via the existing re-identify UI.
        if tmdb_signal.ambiguous_identity:
            result.tmdb_id = None
            result.tmdb_name = tmdb_signal.tmdb_name
            result.content_type = tmdb_signal.content_type
            result.classification_source = "tmdb"
            result.needs_review = True
            cands = tmdb_signal.candidates or []
            listed = "; ".join(f"{c['name']} ({c['year']}, #{c['tmdb_id']})" for c in cands)
            result.review_reason = (
                f'Multiple shows match "{tmdb_signal.tmdb_name}" on TMDB: {listed}. '
                f"Pick the correct one."
            )
            return result

        # Always propagate TMDB metadata
        result.tmdb_id = tmdb_signal.tmdb_id
        result.tmdb_name = tmdb_signal.tmdb_name

        heuristic_type = result.content_type
        tmdb_type = tmdb_signal.content_type

        if heuristic_type == tmdb_type:
            # Agreement — boost confidence
            result.confidence = min(0.95, result.confidence + 0.1)
            result.classification_source = "tmdb+heuristic"
            logger.info(
                f"TMDB confirms heuristic ({heuristic_type.value}), "
                f"boosted confidence to {result.confidence:.0%}"
            )
        elif heuristic_type == ContentType.UNKNOWN:
            # Heuristic has no answer, use TMDB
            result.content_type = tmdb_type
            result.confidence = tmdb_signal.confidence
            result.classification_source = "tmdb"
            result.needs_review = False
            result.review_reason = None
            logger.info(
                f"TMDB resolves unknown to {tmdb_type.value} ({tmdb_signal.confidence:.0%})"
            )
        else:
            # Disagreement — TMDB and heuristic conflict
            override_confidence = tmdb_signal.confidence * 0.8

            if result.confidence >= 0.75:
                # Strong heuristic evidence — do NOT let TMDB override content type.
                # Keep the heuristic classification and flag for user review.
                logger.info(
                    f"TMDB suggests {tmdb_type.value} but strong heuristic "
                    f"({heuristic_type.value} at {result.confidence:.0%}) — "
                    f"keeping heuristic, flagging for review"
                )
                result.classification_source = "heuristic"
                result.needs_review = True
                result.review_reason = (
                    f"TMDB suggests {tmdb_type.value} but heuristics strongly "
                    f"suggest {heuristic_type.value} ({result.confidence:.0%}). "
                    f"Please verify."
                )
            else:
                # Weak heuristic — TMDB override is reasonable
                logger.info(
                    f"TMDB overrides heuristic: {heuristic_type.value} -> "
                    f"{tmdb_type.value} (confidence {override_confidence:.0%})"
                )
                result.content_type = tmdb_type
                result.confidence = override_confidence
                result.classification_source = "tmdb"

                # If the override confidence is not strong, flag for review
                if override_confidence < 0.6:
                    result.needs_review = True
                    result.review_reason = (
                        f"TMDB suggests {tmdb_type.value} but heuristics suggest "
                        f"{heuristic_type.value}. Low confidence override "
                        f"({override_confidence:.0%})."
                    )
                else:
                    # Clear any previous needs_review from ambiguous movie detection
                    # when TMDB gives a confident override
                    if result.needs_review and not result.review_reason:
                        result.needs_review = False

        # Use TMDB name if similar enough to the heuristic name (same guard as analyze())
        if tmdb_signal.tmdb_name:
            if result.detected_name is None or _names_are_similar(
                result.detected_name, tmdb_signal.tmdb_name
            ):
                result.detected_name = tmdb_signal.tmdb_name

        return result

    def _detect_movie(self, titles: list[TitleInfo]) -> dict | None:
        """Detect if the disc contains a movie."""
        long_titles = [
            t for t in titles if t.duration_seconds >= self._get_config().analyst_movie_min_duration
        ]
        logger.info(
            f"Found {len(long_titles)} movie-length titles (> {self._get_config().analyst_movie_min_duration}s)"
        )

        if len(long_titles) == 1:
            # Single long title - high confidence movie
            main_title = long_titles[0]
            total_duration = sum(t.duration_seconds for t in titles)
            dominance = main_title.duration_seconds / total_duration if total_duration else 0

            # If there's only one movie-length title, classify as movie
            # even with low dominance (lots of bonus features)
            confidence = (
                0.9 if dominance >= self._get_config().analyst_movie_dominance_threshold else 0.75
            )
            return {"confidence": confidence, "main_title": main_title}

        if len(long_titles) > 3:
            # Too many feature-length titles - likely multi-movie disc or compilation
            # Don't rip automatically, force human review
            return {
                "confidence": 0.0,
                "ambiguous": True,
                "reason": f"Found {len(long_titles)} feature-length titles. This may be a multi-movie disc or compilation. Please review and select which title(s) to rip.",
            }

        if len(long_titles) >= 2:
            # 2-3 long titles found - could be theatrical vs extended cut
            # Force review to select correct version
            return {
                "confidence": 0.0,
                "ambiguous": True,
                "reason": "Multiple feature-length titles found. Please select correct version (theatrical, extended, etc.).",
            }

        return None

    def _detect_tv_show(self, titles: list[TitleInfo]) -> dict | None:
        """Detect if the disc contains TV episodes.

        TV is detected if 3+ titles share a duration within ±2 minutes
        AND are within typical TV episode duration range (18-70 minutes).
        """
        if len(titles) < self._get_config().analyst_tv_min_cluster_size:
            return None

        # Don't skip TV detection just because there's a movie-length title.
        # It could be a "Play All" title on a TV disc.

        # Filter to only TV-length titles
        tv_length_titles = [
            t
            for t in titles
            if self._get_config().analyst_tv_min_duration
            <= t.duration_seconds
            <= self._get_config().analyst_tv_max_duration
        ]

        if len(tv_length_titles) < self._get_config().analyst_tv_min_cluster_size:
            return None

        # Group titles by approximate duration (within variance)
        clusters: list[list[TitleInfo]] = []

        for title in tv_length_titles:
            placed = False
            for cluster in clusters:
                # Check if this title fits in the cluster
                cluster_avg = sum(t.duration_seconds for t in cluster) / len(cluster)
                if (
                    abs(title.duration_seconds - cluster_avg)
                    <= self._get_config().analyst_tv_duration_variance
                ):
                    cluster.append(title)
                    placed = True
                    break

            if not placed:
                clusters.append([title])

        # Find the largest cluster
        largest_cluster = max(clusters, key=len) if clusters else []

        if len(largest_cluster) >= self._get_config().analyst_tv_min_cluster_size:
            confidence = min(0.95, 0.5 + len(largest_cluster) * 0.1)
            return {
                "confidence": confidence,
                "episode_count": len(largest_cluster),
                "episode_indices": [t.index for t in largest_cluster],
            }

        return None

    def _detect_play_all(self, titles: list[TitleInfo], tv_result: dict | None) -> list[int]:
        """Identify 'Play All' concatenation titles on TV discs.

        A Play All title has duration roughly equal to the sum of all episode-cluster
        titles. It must also be feature-length (>80 min).

        Args:
            titles: All titles on the disc
            tv_result: Result from _detect_tv_show (must contain 'episode_indices')

        Returns:
            List of title indices that are Play All concatenations
        """
        if not tv_result or "episode_indices" not in tv_result:
            # No episode cluster — try fallback using TV-range titles
            return self._detect_play_all_fallback(titles)

        episode_indices = set(tv_result["episode_indices"])
        episode_total = sum(t.duration_seconds for t in titles if t.index in episode_indices)

        if episode_total == 0:
            return []

        play_all = []
        min_duration = self._get_config().analyst_movie_min_duration

        for t in titles:
            if t.index in episode_indices:
                continue
            # Must be feature-length
            if t.duration_seconds < min_duration:
                continue
            # Check if duration is close to the episode total (within ±20%)
            ratio = t.duration_seconds / episode_total
            if 0.8 <= ratio <= 1.2:
                play_all.append(t.index)
                logger.info(
                    f"Detected 'Play All' title {t.index} "
                    f"({t.duration_seconds // 60}min ≈ {episode_total // 60}min episode total)"
                )

        return play_all

    def _detect_play_all_fallback(self, titles: list[TitleInfo]) -> list[int]:
        """Detect Play All when no episode cluster is available.

        Used for label-fallback and TMDB-only TV classification paths.
        Computes total of all TV-range titles and checks for a matching long title.
        """
        config = self._get_config()
        tv_range_titles = [
            t
            for t in titles
            if config.analyst_tv_min_duration
            <= t.duration_seconds
            <= config.analyst_tv_max_duration
        ]

        if len(tv_range_titles) < 2:
            return []

        tv_total = sum(t.duration_seconds for t in tv_range_titles)
        tv_indices = {t.index for t in tv_range_titles}

        play_all = []
        for t in titles:
            if t.index in tv_indices:
                continue
            if t.duration_seconds < config.analyst_movie_min_duration:
                continue
            ratio = t.duration_seconds / tv_total
            if 0.8 <= ratio <= 1.2:
                play_all.append(t.index)
                logger.info(
                    f"Detected 'Play All' title {t.index} (fallback) "
                    f"({t.duration_seconds // 60}min ≈ {tv_total // 60}min TV total)"
                )

        return play_all

    @staticmethod
    def _parse_volume_label(label: str) -> tuple[str | None, int | None, int | None]:
        """Parse show name, season, and disc number from volume label.

        Examples:
            "THE_OFFICE_S1D2" -> ("The Office", 1, 2)
            "THE_OFFICE_S01D02" -> ("The Office", 1, 2)
            "FIREFLY_DISC1" -> ("Firefly", None, 1)
            "BREAKING_BAD_SEASON_2" -> ("Breaking Bad", 2, None)
        """
        if not label:
            return None, None, None

        # Reject generic Windows/disc placeholder labels (e.g. LOGICAL_VOLUME_ID, VIDEO_TS)
        normalized_check = re.sub(r"[_\s]", "", label).upper()
        if normalized_check in _GENERIC_VOLUME_LABELS:
            logger.info(
                f"Volume label '{label}' is a generic placeholder — treating as unlabeled disc"
            )
            return None, None, None

        # Strip studio name prefixes (e.g. MARVEL_STUDIOS_WANDAVISION -> WANDAVISION)
        upper_label = label.upper()
        for prefix in _STUDIO_PREFIXES:
            if upper_label.startswith(prefix) and len(upper_label) > len(prefix):
                label = label[len(prefix) :]
                logger.info(f"Stripped studio prefix '{prefix.rstrip('_')}' from volume label")
                break

        # Clean up the label
        original = label.upper().replace("_", " ")
        label = original

        # Try to extract season AND disc from combined pattern (S01D02, S1D1, etc.)
        season_disc_match = re.search(r"S(\d+)\s*D(\d+)", label)
        if season_disc_match:
            season = int(season_disc_match.group(1))
            disc = int(season_disc_match.group(2))
            label = re.sub(r"S\d+\s*D\d+", "", label)
            logger.info(f"Parsed volume label '{original}': season={season}, disc={disc}")
        else:
            # Try to extract season number alone
            season = None
            disc = None

            season_patterns = [
                r"S(\d+)",
                r"SEASON\s*(\d+)",
                r"SERIES\s*(\d+)",
            ]

            for pattern in season_patterns:
                match = re.search(pattern, label)
                if match:
                    season = int(match.group(1))
                    label = re.sub(pattern, "", label)
                    break

            # Try to extract disc number
            disc_patterns = [
                r"D(\d+)",
                r"DISC\s*(\d+)",
                r"DISK\s*(\d+)",
            ]

            for pattern in disc_patterns:
                match = re.search(pattern, label)
                if match:
                    disc = int(match.group(1))
                    label = re.sub(pattern, "", label)
                    break

        # Check for "NameNumber" pattern (e.g. SOUTHPARK6 -> Season 6)
        # Only if we haven't found a season yet
        if season is None:
            # Look for number at the end of the string
            name_num_match = re.search(r"^([a-zA-Z\s]+)(\d+)$", label.strip())
            if name_num_match:
                # It's ambiguous (could be IronMan2), but often it's Season X for TV.
                # verification comes from duration analysis later.
                possible_season = int(name_num_match.group(2))
                # Heuristic: Seasons are usually 1-30. If it's 2000+, it's a year.
                if 0 < possible_season < 100:
                    season = possible_season
                    label = name_num_match.group(1)  # Remove the number from name
                    logger.info(f"Parsed implicit season from label '{original}': season={season}")

        # Remove common disc indicators that aren't disc numbers
        label = re.sub(r"\b(DVD|BLURAY|BD)\s*\d*\b", "", label)
        label = label.strip()

        # Convert to title case
        name = label.title() if label else None

        return name, season, disc

    # Pattern for catalog-number-style labels like BBCDVD1550, MGMHV1234, FHED3456
    _CATALOG_PATTERN = re.compile(r"^[A-Z]{2,6}\d{3,}$")

    @staticmethod
    def _looks_like_catalog_number(label: str) -> bool:
        """Detect catalog-number labels (e.g. BBCDVD1550, FHED3456).

        These are publisher catalog codes, not human-readable titles.
        The parsed 'name' from such labels is garbage and should not be
        used as detected_title.
        """
        normalized = re.sub(r"[_\s]", "", label).upper()
        return bool(DiscAnalyst._CATALOG_PATTERN.match(normalized))

    @staticmethod
    def _parse_disc_name(disc_name: str) -> tuple[str | None, int | None]:
        """Parse MakeMKV's DINFO disc name into (show_title, season).

        MakeMKV provides disc names like:
          "Star Trek: Strange New Worlds - Season 3 (Disc 1)"
          "The Office - Season 2"
          "Supernatural Season 11 Disc 2"   # space- or dash-separated disc, no parens
          "Inception"

        The trailing disc indicator is accepted with or without parentheses or a
        dash ("(Disc 1)", "Disc 1", "- Disc 1"), so the season is recovered even
        when it sits ahead of a bare "Disc N" suffix.

        Returns (show_title, season), either may be None if not found.
        """
        if not disc_name:
            return None, None

        name = disc_name.strip()
        season: int | None = None

        # Strip a trailing disc indicator with OR without parentheses/dash/colon:
        # "(Disc 1)", "Disc 1", "- Disc 1", ": Disc 1", "Disk 1", "Disc1".
        name = re.sub(
            r"\s*[-–:]?\s*\(?\s*Dis[ck]\s*\d+\s*\)?\s*$", "", name, flags=re.IGNORECASE
        ).strip()

        # Extract a trailing "- Season N" / ": Season N" / "Season N" suffix. The
        # dash/colon variant is tried first so the separator is consumed cleanly
        # (e.g. "Breaking Bad: Season 2" -> "Breaking Bad"). Colons *inside* a title
        # are untouched because these patterns are anchored to the end of the string.
        m = re.search(r"\s*[-–:]\s*Season\s+(\d+)\s*$", name, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            name = name[: m.start()].strip()
        else:
            m = re.search(r"\s+Season\s+(\d+)\s*$", name, re.IGNORECASE)
            if m:
                season = int(m.group(1))
                name = name[: m.start()].strip()

        # Trim any leftover trailing separator punctuation (e.g. a dangling colon).
        name = name.rstrip(" :-–").strip()

        # Reject empty, too-short, or generic placeholder results.
        if not name or len(name) < 2 or _is_generic_disc_name(name):
            return None, None

        return name, season

    def _get_ambiguity_reason(self, titles: list[TitleInfo]) -> str:
        """Generate a human-readable reason for ambiguity."""
        long_titles = [
            t for t in titles if t.duration_seconds >= self._get_config().analyst_movie_min_duration
        ]

        if len(long_titles) >= 2:
            return f"Multiple long titles found ({len(long_titles)} titles > 80 min). Could be multi-movie disc or special features."

        if len(titles) < self._get_config().analyst_tv_min_cluster_size:
            return f"Only {len(titles)} title(s) found. Not enough to determine TV/Movie."

        durations = [t.duration_seconds // 60 for t in titles]
        return f"Inconsistent title durations ({min(durations)}-{max(durations)} min). Unable to classify."

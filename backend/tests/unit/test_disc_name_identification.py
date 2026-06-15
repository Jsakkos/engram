"""Unit tests for DINFO disc-name extraction and TMDB fallback identification."""

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo, _abbreviation_matches, _names_are_similar
from app.core.extractor import MakeMKVExtractor
from app.core.tmdb_classifier import TmdbSignal
from app.models.disc_job import ContentType

# ---------------------------------------------------------------------------
# Extractor: DINFO parsing
# ---------------------------------------------------------------------------


def _make_extractor() -> MakeMKVExtractor:
    from pathlib import Path

    return MakeMKVExtractor(makemkv_path=Path("makemkvcon64"))


SAMPLE_MAKEMKV_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.17.7 linux(x64-release) started"
CINFO:2,0,"Star Trek: Strange New Worlds - Season 3 (Disc 1)"
CINFO:33,0,"Blu-ray disc"
TINFO:0,2,0,"Title 1"
TINFO:0,9,0,"0:47:50"
TINFO:0,10,0,"12.90 GB"
TINFO:0,8,0,"5"
TINFO:0,16,0,"00800.m2ts"
TINFO:0,19,0,"1920x1080"
TINFO:0,25,0,"3"
TINFO:0,26,0,"800,801,802"
TINFO:0,27,0,"Star Trek- Strange New Worlds - Season 3 (Disc 1)_t00.mkv"
TINFO:1,2,0,"Title 2"
TINFO:1,9,0,"0:49:14"
TINFO:1,10,0,"12.67 GB"
TINFO:1,8,0,"5"
TINFO:1,16,0,"00801.m2ts"
TINFO:1,19,0,"1920x1080"
TINFO:1,25,0,"3"
TINFO:1,26,0,"803,804,805"
TINFO:1,27,0,"Star Trek- Strange New Worlds - Season 3 (Disc 1)_t01.mkv"
"""


def test_parse_disc_info_extracts_cinfo_disc_name():
    extractor = _make_extractor()
    titles, disc_name = extractor._parse_disc_info(SAMPLE_MAKEMKV_OUTPUT)

    assert disc_name == "Star Trek: Strange New Worlds - Season 3 (Disc 1)"


def test_parse_disc_info_extracts_tinfo_27_disc_title():
    extractor = _make_extractor()
    titles, disc_name = extractor._parse_disc_info(SAMPLE_MAKEMKV_OUTPUT)

    assert len(titles) == 2
    assert titles[0].disc_title == "Star Trek- Strange New Worlds - Season 3 (Disc 1)_t00.mkv"
    assert titles[1].disc_title == "Star Trek- Strange New Worlds - Season 3 (Disc 1)_t01.mkv"


def test_parse_disc_info_no_cinfo_returns_empty_string():
    extractor = _make_extractor()
    output_without_dinfo = "\n".join(
        line for line in SAMPLE_MAKEMKV_OUTPUT.splitlines() if not line.startswith("CINFO")
    )
    titles, disc_name = extractor._parse_disc_info(output_without_dinfo)

    assert disc_name == ""
    assert len(titles) == 2


# ---------------------------------------------------------------------------
# DiscAnalyst._parse_disc_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "disc_name,expected_title,expected_season",
    [
        (
            "Star Trek: Strange New Worlds - Season 3 (Disc 1)",
            "Star Trek: Strange New Worlds",
            3,
        ),
        ("The Office - Season 2", "The Office", 2),
        ("Arrested Development Season 4", "Arrested Development", 4),
        ("Inception", "Inception", None),
        ("Star Trek: Strange New Worlds - Season 3", "Star Trek: Strange New Worlds", 3),
        # Space-separated "Disc N" without parentheses (issue #303): the disc
        # suffix must be stripped so the trailing "Season N" is recognized.
        ("Supernatural Season 11 Disc 2", "Supernatural", 11),
        ("The Office Season 2 Disc 4", "The Office", 2),
        # Dash-separated disc indicator (also supported, per the regex comment).
        ("Supernatural Season 11 - Disc 2", "Supernatural", 11),
        # Disc-only name: clean the title, no season.
        ("Firefly Disc 1", "Firefly", None),
        # Colon-separated "Title: Season N: Disc M" (some Blu-ray DINFO names).
        ("Breaking Bad: Season 2: Disc 1", "Breaking Bad", 2),
        # Generic placeholder disc names carry no title.
        ("Blu-ray disc", None, None),
        ("", None, None),
        ("  ", None, None),
    ],
)
def test_parse_disc_name(disc_name, expected_title, expected_season):
    title, season = DiscAnalyst._parse_disc_name(disc_name)
    assert title == expected_title
    assert season == expected_season


# ---------------------------------------------------------------------------
# Analyst: TMDB name adopted when corroborated by label OR DINFO disc title
# ---------------------------------------------------------------------------


def _tv_titles(count: int = 6, duration: int = 2870) -> list[TitleInfo]:
    return [
        TitleInfo(index=i, duration_seconds=duration, size_bytes=int(13e9), chapter_count=5)
        for i in range(count)
    ]


def test_analyst_without_disc_title_keeps_garbled_label_name():
    """Without a DINFO disc_title, an extra-leading-words TMDB name (Star Trek: ...) is not corroborated by the concatenated label, so the garbled label name is kept."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=99966,
        tmdb_name="Star Trek: Strange New Worlds",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "STRANGENEWWORLDS_SEASON3", tmdb_signal=tmdb)

    # TMDB ID is still propagated even without a disc_title
    assert result.tmdb_id == 99966
    # But detected_name comes from garbled volume label
    # Deferred case: collapsed "strangenewworlds" != "startrekstrangenewworlds"
    assert result.detected_name == "Strangenewworlds"
    # Fix 3: the concatenated label does not corroborate, so the disc goes to review.
    assert result.needs_review is True
    assert result.review_reason is not None


def test_analyst_with_disc_title_adopts_tmdb_name():
    """With a DINFO disc_title that corroborates the TMDB name, the authoritative TMDB name flows through cleanly."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=99966,
        tmdb_name="Star Trek: Strange New Worlds",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "STRANGENEWWORLDS_SEASON3",
        tmdb_signal=tmdb,
        disc_title="Star Trek: Strange New Worlds",
    )

    assert result.detected_name == "Star Trek: Strange New Worlds"
    assert result.tmdb_id == 99966
    assert result.needs_review is False
    assert result.content_type == ContentType.TV


def test_analyst_drops_movie_tmdb_id_when_heuristic_keeps_tv():
    """A TMDB *movie* match must NOT stamp its id/name onto a disc the heuristic
    keeps as TV: TMDB ids are namespace-scoped, so a movie id dereferenced as a
    TV id downstream (subtitle/roster lookups) resolves to an unrelated show.

    Regression (Mad Men S3): label "MADMEN3" matched the obscure movie
    "Two Madmen" (id 52163); that id in the TV namespace is the unrelated Greek
    show "O Hristos xanastavronetai", which poisoned the subtitle download.
    """
    tmdb = TmdbSignal(
        content_type=ContentType.MOVIE,  # disagrees with the strong TV heuristic
        confidence=0.85,
        tmdb_id=12345,
        tmdb_name="Some Film",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "STRANGENEWWORLDS_SEASON3",
        tmdb_signal=tmdb,
        disc_title="Some Film",
    )

    # Heuristic stays TV...
    assert result.content_type == ContentType.TV
    # ...but the cross-namespace movie id/name is dropped, not propagated.
    assert result.tmdb_id is None
    assert result.tmdb_name is None


def test_analyst_movie_name_does_not_corrupt_tv_detected_name():
    """The garbage movie NAME must not overwrite a TV disc's detected_name even
    when it fuzzily matches the spaceless volume label ('Madmen' ~ 'Two Madmen').
    The clean DINFO disc title is kept instead (Mad Men S3 regression)."""
    tmdb = TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=0.70,
        tmdb_id=52163,
        tmdb_name="Two Madmen",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "MADMEN3", tmdb_signal=tmdb, disc_title="Mad Men")

    assert result.content_type == ContentType.TV
    assert result.detected_name == "Mad Men"
    assert result.tmdb_id is None


def test_analyst_adopts_tmdb_name_for_concatenated_label():
    """BREAKINGBADS2 -> 'Breakingbad' must be corrected to TMDB 'Breaking Bad'."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "BREAKINGBADS2", tmdb_signal=tmdb)

    assert result.detected_name == "Breaking Bad"
    assert result.detected_season == 2
    assert result.tmdb_id == 1396


def test_analyst_adopts_tmdb_name_when_disc_title_corroborates():
    """A clean DINFO disc title corroborates the TMDB name as well."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "BREAKINGBADS2",
        tmdb_signal=tmdb,
        disc_title="Breaking Bad",
    )

    assert result.detected_name == "Breaking Bad"
    assert result.tmdb_id == 1396


def test_analyst_keeps_base_name_when_tmdb_uncorroborated():
    """A spurious TMDB name matching neither on-disc signal must not override."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=999,
        tmdb_name="Some Unrelated Show",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "BREAKINGBADS2",
        tmdb_signal=tmdb,
        disc_title="Breaking Bad",
    )

    # Neither "Breakingbad" nor "Breaking Bad" matches "Some Unrelated Show",
    # so the DINFO-preferred base name is kept rather than the TMDB name.
    assert result.detected_name == "Breaking Bad"
    # Fix 3: an uncorroborated TMDB name now escalates to review.
    assert result.needs_review is True
    assert result.review_reason is not None


# ---------------------------------------------------------------------------
# _run_classification integration: disc_name → TMDB fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_classification_uses_disc_name_when_label_fails(monkeypatch):
    """When the volume label gives a garbled TMDB miss, disc_name gets a hit."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())  # real defaults — gives numeric threshold values
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = _tv_titles()

    # Mock config: TMDB enabled, DiscDB disabled, AI disabled
    # Set numeric analyst thresholds explicitly so the analyst's >= comparisons work.
    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    snw_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=99966,
        tmdb_name="Star Trek: Strange New Worlds",
    )

    call_count = {"n": 0}

    def fake_classify_from_tmdb(name: str, api_key: str):
        call_count["n"] += 1
        if name == "Strangenewworlds":
            return None  # label-derived name fails
        if name == "Star Trek: Strange New Worlds":
            return snw_signal  # disc-name-derived name succeeds
        return None

    mock_job = MagicMock()
    mock_job.volume_label = "STRANGENEWWORLDS_SEASON3"
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch("app.core.tmdb_classifier.classify_from_tmdb", side_effect=fake_classify_from_tmdb),
        patch("app.matcher.tmdb_client.fetch_season_episode_runtimes", return_value=[]),
    ):
        analysis = await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="Star Trek: Strange New Worlds - Season 3 (Disc 1)",
        )

    assert analysis.detected_name == "Star Trek: Strange New Worlds"
    assert analysis.tmdb_id == 99966
    assert analysis.detected_season == 3
    assert call_count["n"] == 2  # once for garbled label, once for disc name


@pytest.mark.asyncio
async def test_run_classification_uses_disc_name_when_label_resolves(monkeypatch):
    """DINFO corrects a garbled-but-resolved label (BREAKINGBADS2 -> Breaking Bad)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = _tv_titles()

    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    bb_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
    )

    call_count = {"n": 0}

    def fake_classify_from_tmdb(name: str, api_key: str):
        call_count["n"] += 1
        if name == "Breakingbad":
            return bb_signal  # label-derived name resolves (via TMDB variation)
        return None

    mock_job = MagicMock()
    mock_job.volume_label = "BREAKINGBADS2"
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch("app.core.tmdb_classifier.classify_from_tmdb", side_effect=fake_classify_from_tmdb),
        patch("app.matcher.tmdb_client.fetch_season_episode_runtimes", return_value=[]),
    ):
        analysis = await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="Breaking Bad: Season 2: Disc 1",
        )

    assert analysis.detected_name == "Breaking Bad"
    assert analysis.detected_season == 2
    assert analysis.tmdb_id == 1396
    assert call_count["n"] == 1  # only the label query; no disc-name fallback call


@pytest.mark.asyncio
async def test_run_classification_reresolves_tv_when_label_matches_movie(monkeypatch):
    """A volume-label match that returns a MOVIE for a clearly-TV disc is
    re-resolved from the DINFO disc name to the correct TV show.

    Mad Men S3 regression: "MADMEN3" -> label name "Madmen" -> TMDB movie
    "Two Madmen". The disc name "Mad Men Season 3" resolves to the real TV show,
    so the disc auto-identifies (no manual title, no poisoned movie id).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = _tv_titles()

    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    madmen_movie = TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=0.70,
        tmdb_id=52163,
        tmdb_name="Two Madmen",
    )
    madmen_tv = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1104,
        tmdb_name="Mad Men",
    )

    call_count = {"n": 0}

    def fake_classify_from_tmdb(name: str, api_key: str):
        call_count["n"] += 1
        if name == "Madmen":
            return madmen_movie  # garbage cross-namespace movie match
        if name == "Mad Men":
            return madmen_tv  # clean disc-name lookup -> real TV show
        return None

    mock_job = MagicMock()
    mock_job.volume_label = "MADMEN3"
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch("app.core.tmdb_classifier.classify_from_tmdb", side_effect=fake_classify_from_tmdb),
        patch("app.matcher.tmdb_client.fetch_season_episode_runtimes", return_value=[]),
    ):
        analysis = await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="Mad Men Season 3- Disc 3",
        )

    assert analysis.content_type == ContentType.TV
    assert analysis.tmdb_id == 1104
    assert analysis.detected_name == "Mad Men"
    assert analysis.detected_season == 3
    # The movie/TV conflict is gone, so the disc no longer needs manual review.
    assert analysis.needs_review is False
    assert call_count["n"] == 2  # label query (movie) + disc-name re-resolve (tv)


@pytest.mark.asyncio
async def test_run_classification_skips_redundant_reresolve_after_disc_name_fallback(monkeypatch):
    """When the disc-name fallback already queried the disc title (and got a
    movie), the cross-namespace re-resolve must NOT query the identical title
    again — it would issue the same network round-trip for the same result."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = _tv_titles()

    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    a_movie = TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=0.70,
        tmdb_id=52163,
        tmdb_name="Two Madmen",
    )

    queried: list[str] = []

    def fake_classify_from_tmdb(name: str, api_key: str):
        queried.append(name)
        if name == "Mad Men":
            return a_movie  # disc-name fallback resolves to a movie
        return None  # label name misses

    mock_job = MagicMock()
    mock_job.volume_label = "MADMEN"  # no season in the label -> label lookup misses
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch("app.core.tmdb_classifier.classify_from_tmdb", side_effect=fake_classify_from_tmdb),
        patch("app.matcher.tmdb_client.fetch_season_episode_runtimes", return_value=[]),
    ):
        await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="Mad Men Season 3- Disc 3",
        )

    # "Mad Men" must be queried exactly once (the fallback), not re-queried by the
    # cross-namespace re-resolve block.
    assert queried.count("Mad Men") == 1


# ---------------------------------------------------------------------------
# Fix 1: abbreviation / initialism corroboration (DS9 ↔ Deep Space Nine)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,full_name",
    [
        ("DS9", "Star Trek: Deep Space Nine"),  # number-word Nine -> 9, drop "Star Trek:"
        ("Ds9", "Star Trek: Deep Space Nine"),  # case-insensitive
        ("TNG", "Star Trek: The Next Generation"),  # colon-split -> "The Next Generation" -> T-N-G
    ],
)
def test_abbreviation_matches_positive(label, full_name):
    assert _abbreviation_matches(label, full_name) is True


@pytest.mark.parametrize(
    "label,full_name",
    [
        ("DS9", "Star Trek: The Next Generation"),  # ds9 != tng / stng
        ("HOUSE", "Star Trek: Deep Space Nine"),  # has vowels, no digit -> not abbrev-shaped
        ("STRANGENEWWORLDS", "Star Trek: Strange New Worlds"),  # too long (>5) -> not abbrev
        ("D", "Deep Space Nine"),  # single char -> rejected
    ],
)
def test_abbreviation_matches_negative(label, full_name):
    assert _abbreviation_matches(label, full_name) is False


def test_names_are_similar_uses_abbreviation_path():
    assert _names_are_similar("Ds9", "Star Trek: Deep Space Nine") is True


def test_analyst_adopts_tmdb_name_for_abbreviated_label():
    """DS9S1D1 -> 'Ds9' must corroborate and adopt TMDB 'Star Trek: Deep Space Nine'."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "DS9S1D1", tmdb_signal=tmdb, disc_title="DS9S1D1")

    assert result.detected_name == "Star Trek: Deep Space Nine"
    assert result.tmdb_id == 580
    assert result.content_type == ContentType.TV


# ---------------------------------------------------------------------------
# Fix 3: uncorroborated identity escalates to review
# ---------------------------------------------------------------------------


def test_analyst_escalates_review_when_tmdb_uncorroborated():
    """A TMDB name matching neither on-disc signal -> needs_review with a candidate."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=999,
        tmdb_name="Some Unrelated Show",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(), "BREAKINGBADS2", tmdb_signal=tmdb, disc_title="Breaking Bad"
    )

    assert result.needs_review is True
    assert result.review_reason is not None
    assert "Some Unrelated Show" in result.review_reason
    assert "Breaking Bad" in result.review_reason
    assert "999" in result.review_reason
    # The base name is kept as the suggestion; TMDB id still attached.
    assert result.detected_name == "Breaking Bad"


def test_analyst_no_review_when_corroborated():
    """A corroborated name (DS9 via abbreviation) must NOT trigger review."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.70,
        tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "DS9S1D1", tmdb_signal=tmdb, disc_title="DS9S1D1")

    assert result.needs_review is False


@pytest.mark.asyncio
async def test_run_classification_fetches_runtimes_and_keeps_pilot(monkeypatch):
    """DS9 S1D1: caller fetches expected runtimes so the 90-min pilot is kept."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.app_config import AppConfig
    from app.services.identification_coordinator import IdentificationCoordinator

    coordinator = IdentificationCoordinator.__new__(IdentificationCoordinator)
    analyst = DiscAnalyst()
    analyst.set_config(AppConfig())
    coordinator._analyst = analyst
    coordinator._get_discdb_mappings = MagicMock(return_value=[])
    coordinator._set_discdb_mappings = MagicMock()

    titles = [
        TitleInfo(index=0, duration_seconds=5429, size_bytes=int(2e9), chapter_count=18),
        TitleInfo(index=1, duration_seconds=2718, size_bytes=int(1e9), chapter_count=8),
        TitleInfo(index=2, duration_seconds=2715, size_bytes=int(1e9), chapter_count=8),
    ]

    mock_config = MagicMock()
    mock_config.tmdb_api_key = "fake-key"
    mock_config.ai_identification_enabled = False
    mock_config.ai_api_key = None
    mock_config.discdb_enabled = False
    mock_config.analyst_movie_min_duration = 80 * 60
    mock_config.analyst_tv_duration_variance = 2 * 60
    mock_config.analyst_tv_min_cluster_size = 3
    mock_config.analyst_tv_min_duration = 18 * 60
    mock_config.analyst_tv_max_duration = 70 * 60
    mock_config.analyst_movie_dominance_threshold = 0.6

    ds9_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=580,
        tmdb_name="Star Trek: Deep Space Nine",
    )

    runtime_calls: list[tuple] = []

    def fake_runtimes(show_id, season_number):
        runtime_calls.append((show_id, season_number))
        return [90, 45, 45, 45, 45]

    mock_job = MagicMock()
    mock_job.volume_label = "DS9S1D1"
    mock_job.detected_season = None
    mock_job.content_hash = None
    mock_job.discdb_slug = None
    mock_job.discdb_disc_slug = None
    mock_job.discdb_mappings_json = None
    mock_job.play_all_indices_json = None

    mock_session = AsyncMock()

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=mock_config)),
        patch("app.core.features.DISCDB_ENABLED", False),
        patch(
            "app.core.tmdb_classifier.classify_from_tmdb",
            side_effect=lambda name, api_key: ds9_signal,
        ),
        patch(
            "app.matcher.tmdb_client.fetch_season_episode_runtimes",
            side_effect=fake_runtimes,
        ),
    ):
        analysis = await coordinator._run_classification(
            mock_job,
            job_id=1,
            titles=titles,
            session=mock_session,
            disc_name="DS9S1D1",
        )

    assert ("580", 1) in runtime_calls
    assert 0 not in analysis.play_all_title_indices
    assert analysis.detected_name == "Star Trek: Deep Space Nine"

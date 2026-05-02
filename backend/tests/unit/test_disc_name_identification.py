"""Unit tests for DINFO disc-name extraction and TMDB fallback identification."""

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
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
        ("", None, None),
        ("  ", None, None),
    ],
)
def test_parse_disc_name(disc_name, expected_title, expected_season):
    title, season = DiscAnalyst._parse_disc_name(disc_name)
    assert title == expected_title
    assert season == expected_season


# ---------------------------------------------------------------------------
# Analyst: name_hint bypasses _names_are_similar guard
# ---------------------------------------------------------------------------


def _tv_titles(count: int = 6, duration: int = 2870) -> list[TitleInfo]:
    return [
        TitleInfo(index=i, duration_seconds=duration, size_bytes=int(13e9), chapter_count=5)
        for i in range(count)
    ]


def test_analyst_without_name_hint_gives_garbled_name():
    """Without name_hint the analyst parses the volume label and gets a garbled name."""
    tmdb = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=99966,
        tmdb_name="Star Trek: Strange New Worlds",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(_tv_titles(), "STRANGENEWWORLDS_SEASON3", tmdb_signal=tmdb)

    # TMDB ID is propagated even without name_hint
    assert result.tmdb_id == 99966
    # But detected_name comes from garbled volume label
    assert result.detected_name == "Strangenewworlds"


def test_analyst_with_name_hint_uses_correct_name():
    """With name_hint the analyst uses it directly; TMDB name flows through cleanly."""
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
        name_hint="Star Trek: Strange New Worlds",
    )

    assert result.detected_name == "Star Trek: Strange New Worlds"
    assert result.tmdb_id == 99966
    assert result.needs_review is False
    assert result.content_type == ContentType.TV


def test_analyst_name_hint_still_propagates_tmdb_id_on_type_conflict():
    """Even when heuristic and TMDB disagree on type, tmdb_id is set."""
    tmdb = TmdbSignal(
        content_type=ContentType.MOVIE,  # disagrees with heuristic TV
        confidence=0.85,
        tmdb_id=12345,
        tmdb_name="Some Film",
    )
    analyst = DiscAnalyst()
    result = analyst.analyze(
        _tv_titles(),
        "STRANGENEWWORLDS_SEASON3",
        tmdb_signal=tmdb,
        name_hint="Some Film",
    )

    assert result.tmdb_id == 12345


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

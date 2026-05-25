"""Unit tests for IdentificationCoordinator._run_classification branch routing.

The classifier backends (DiscDB / TMDB / AI) and the analyst are stubbed so the
test isolates the merge/override logic: which signal wins, how low-confidence
DiscDB supplements, and the AI re-query fallback. The method does not touch the
DB session, so a transient DiscJob and session=None are sufficient.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.core.analyst import DiscAnalysisResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator


def _make_coord(analyst):
    coord = IdentificationCoordinator(analyst, MagicMock(), MagicMock(), MagicMock())
    coord._set_discdb_mappings = Mock()
    return coord


def _analysis(**kw):
    base = dict(
        content_type=ContentType.UNKNOWN,
        detected_name=None,
        confidence=0.0,
        needs_review=True,
    )
    base.update(kw)
    return DiscAnalysisResult(**base)


def _config(**kw):
    base = dict(
        tmdb_api_key=None,
        discdb_enabled=False,
        ai_identification_enabled=False,
        ai_api_key=None,
        ai_provider="anthropic",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _job(volume_label="THE_OFFICE_S1D1"):
    return DiscJob(
        drive_id="E:",
        volume_label=volume_label,
        content_type=ContentType.UNKNOWN,
        state=JobState.IDENTIFYING,
        staging_path="/tmp/staging",
    )


def _patch_config(monkeypatch, config):
    monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=config))


@pytest.mark.unit
class TestRunClassification:
    async def test_discdb_high_confidence_overrides_analysis(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(discdb_enabled=True))
        monkeypatch.setattr("app.core.features.DISCDB_ENABLED", True)

        @dataclass
        class _Mapping:
            index: int
            season: int
            episode: int

        signal = SimpleNamespace(
            content_type=ContentType.TV,
            confidence=0.95,
            matched_title="The Office",
            source="hash",
            disc_slug="the-office-s1d1",
            tmdb_id=2316,
            title_mappings=[_Mapping(0, 1, 1)],
        )
        monkeypatch.setattr(
            "app.core.discdb_classifier.classify_from_discdb", Mock(return_value=signal)
        )

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis.content_type == ContentType.TV
        assert analysis.confidence == 0.95
        assert analysis.classification_source == "discdb_hash"
        assert analysis.detected_name == "The Office"
        assert analysis.needs_review is False
        assert analysis.tmdb_id == 2316
        coord._set_discdb_mappings.assert_called_once()

    async def test_discdb_low_confidence_supplements_name_only(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(discdb_enabled=True))
        monkeypatch.setattr("app.core.features.DISCDB_ENABLED", True)

        signal = SimpleNamespace(
            content_type=ContentType.TV,
            confidence=0.5,
            matched_title="The Office",
            source="fuzzy",
            disc_slug="x",
            tmdb_id=None,
            title_mappings=[],
        )
        monkeypatch.setattr(
            "app.core.discdb_classifier.classify_from_discdb", Mock(return_value=signal)
        )

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        # Low-confidence DiscDB does not override the type, only fills a blank name.
        assert analysis.content_type == ContentType.UNKNOWN
        assert analysis.detected_name == "The Office"
        assert analysis._discdb_signal is signal

    async def test_tmdb_signal_is_passed_to_analyst(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name="The Office")
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(tmdb_api_key="key"))

        tmdb_signal = SimpleNamespace(
            content_type=ContentType.TV, confidence=0.8, tmdb_name="The Office"
        )
        monkeypatch.setattr(
            "app.core.tmdb_classifier.classify_from_tmdb", Mock(return_value=tmdb_signal)
        )

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis._tmdb_signal is tmdb_signal
        _, kwargs = analyst.analyze.call_args
        assert kwargs["tmdb_signal"] is tmdb_signal

    async def test_ai_fallback_requeries_tmdb(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(
            monkeypatch,
            _config(tmdb_api_key="key", ai_identification_enabled=True, ai_api_key="aikey"),
        )

        ai_tmdb = SimpleNamespace(
            content_type=ContentType.MOVIE, confidence=0.9, tmdb_name="Inception"
        )
        # First TMDB lookup (from the label) fails; re-query with AI name succeeds.
        monkeypatch.setattr(
            "app.core.tmdb_classifier.classify_from_tmdb", Mock(side_effect=[None, ai_tmdb])
        )
        monkeypatch.setattr(
            "app.core.ai_identifier.identify_from_label",
            AsyncMock(return_value={"title": "Inception"}),
        )

        analysis = await coord._run_classification(
            _job("INCEPTION_2010"), 1, [], None, is_staging=False
        )

        assert analysis._tmdb_signal is ai_tmdb

    async def test_ai_name_used_when_tmdb_requery_also_fails(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(
            monkeypatch,
            _config(tmdb_api_key="key", ai_identification_enabled=True, ai_api_key="aikey"),
        )

        monkeypatch.setattr("app.core.tmdb_classifier.classify_from_tmdb", Mock(return_value=None))
        monkeypatch.setattr(
            "app.core.ai_identifier.identify_from_label",
            AsyncMock(return_value={"title": "Inception"}),
        )

        analysis = await coord._run_classification(
            _job("INCEPTION_2010"), 1, [], None, is_staging=False
        )

        assert analysis.detected_name == "Inception"
        assert analysis.classification_source == "ai"

    async def test_no_signals_returns_analyst_result_unchanged(self, monkeypatch):
        analyst = MagicMock()
        result = _analysis(
            content_type=ContentType.MOVIE, detected_name="Some Movie", confidence=0.6
        )
        analyst.analyze.return_value = result
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config())

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis is result
        assert analysis._discdb_signal is None
        assert analysis._tmdb_signal is None

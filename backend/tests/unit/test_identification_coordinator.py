"""Unit tests for IdentificationCoordinator._run_classification branch routing.

The classifier backends (DiscDB / TMDB / AI) and the analyst are stubbed so the
test isolates the merge/override logic: which signal wins, how low-confidence
DiscDB supplements, and the AI re-query fallback. The method does not touch the
DB session, so a transient DiscJob and session=None are sufficient.
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.core.analyst import DiscAnalysisResult
from app.core.tmdb_classifier import TmdbSignal
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import (
    IdentificationCoordinator,
    _candidates_json_from_signal,
    _label_has_year,
)


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
        enable_fingerprint_identification=False,
        fingerprint_server_url="https://server",
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
class TestCandidatesJsonFromSignal:
    """The persisted candidates_json must capture all same-name twins so the
    downstream wrong-show detector can suggest the right one (e.g. Frasier 2023)."""

    def test_serializes_all_candidates(self):
        sig = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.85,
            tmdb_id=3452,
            tmdb_name="Frasier",
            all_candidates=[
                {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
                {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
            ],
        )
        out = _candidates_json_from_signal(sig)
        assert {c["tmdb_id"] for c in json.loads(out)} == {3452, 195241}

    def test_none_when_no_twins(self):
        sig = TmdbSignal(
            content_type=ContentType.TV, confidence=0.85, tmdb_id=1396, tmdb_name="Breaking Bad"
        )
        assert _candidates_json_from_signal(sig) is None

    def test_none_for_missing_signal(self):
        assert _candidates_json_from_signal(None) is None


@pytest.mark.unit
class TestLabelHasYear:
    """A 4-digit 19xx/20xx in the disc label/name lets popularity+year
    disambiguate same-name twins, so the no-year proactive flag is suppressed."""

    def test_detects_year_in_various_forms(self):
        assert _label_has_year("FRASIER_2023") is True
        assert _label_has_year("FRASIER (2023)") is True
        assert _label_has_year("THE_OFFICE_2005") is True
        assert _label_has_year("", "Frasier 1993") is True  # second arg carries it

    def test_no_year_for_season_disc_labels(self):
        assert _label_has_year("FRASIER_S1D1") is False
        assert _label_has_year("FRASIER") is False
        assert _label_has_year("2_BROKE_GIRLS_S1D1") is False
        assert _label_has_year("") is False
        assert _label_has_year(None) is False


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


def _net_signal(tier="canonical", **kw):
    from app.core.fingerprint_disc_classifier import NetworkDiscSignal, NetworkDiscTitle

    base = dict(
        tmdb_id=1396,
        content_type="tv",
        season=2,
        tier=tier,
        unique_contributors=5,
        mean_confidence=0.9,
        titles=[
            NetworkDiscTitle(
                title_index=0,
                duration_seconds=2820,
                size_bytes=5_000_000_000,
                assignment="episode",
                season=2,
                episode=1,
            )
        ],
    )
    base.update(kw)
    return NetworkDiscSignal(**base)


def _job_with_hash(volume_label="THE_OFFICE_S1D1", content_hash="AB" * 16):
    j = _job(volume_label)
    j.content_hash = content_hash
    return j


@pytest.mark.unit
class TestRunClassificationNetworkDisc:
    """Phase C: network disc-hash identification overrides + zero-ASR mappings."""

    def _scanned(self, index, duration, size):
        return SimpleNamespace(
            index=index, duration_seconds=duration, size_bytes=size, chapter_count=0
        )

    async def test_canonical_hit_overrides_and_sets_mappings(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=_net_signal(tier="canonical")),
        )
        # Best-effort name resolution succeeds.
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            Mock(return_value={"name": "Breaking Bad"}),
        )

        scanned = [self._scanned(0, 2820, 5_000_000_000)]
        job = _job_with_hash()
        analysis = await coord._run_classification(job, 1, scanned, None, is_staging=False)

        assert analysis.content_type == ContentType.TV
        assert analysis.tmdb_id == 1396
        assert analysis.classification_source == "fingerprint_network"
        assert analysis.needs_review is False
        assert analysis.confidence >= 0.95
        assert analysis.detected_name == "Breaking Bad"
        assert analysis.detected_season == 2
        # Mappings were persisted for the rip-time auto-assign path.
        coord._set_discdb_mappings.assert_called_once()
        applied = coord._set_discdb_mappings.call_args[0][1]
        assert len(applied) == 1
        assert applied[0].source == "network_disc"
        assert applied[0].index == 0
        assert job.discdb_mappings_json is not None

    async def test_confirmed_hit_is_identity_only_no_mappings(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=_net_signal(tier="confirmed")),
        )
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            Mock(return_value={"name": "Breaking Bad"}),
        )

        scanned = [self._scanned(0, 2820, 5_000_000_000)]
        job = _job_with_hash()
        analysis = await coord._run_classification(job, 1, scanned, None, is_staging=False)

        assert analysis.content_type == ContentType.TV
        assert analysis.tmdb_id == 1396
        assert analysis.classification_source == "fingerprint_network"
        assert analysis.needs_review is False
        # Identity only — episodes verified by ASR later, so no pre-assignment.
        coord._set_discdb_mappings.assert_not_called()
        assert job.discdb_mappings_json is None

    async def test_candidate_tier_is_noop(self, monkeypatch):
        analyst = MagicMock()
        result = _analysis(
            content_type=ContentType.MOVIE, detected_name="Heuristic", confidence=0.4
        )
        analyst.analyze.return_value = result
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=_net_signal(tier="candidate")),
        )

        analysis = await coord._run_classification(_job_with_hash(), 1, [], None, is_staging=False)

        # Below the confidence bar — existing flow untouched.
        assert analysis is result
        assert analysis.classification_source != "fingerprint_network"
        coord._set_discdb_mappings.assert_not_called()

    async def test_miss_is_noop(self, monkeypatch):
        analyst = MagicMock()
        result = _analysis(content_type=ContentType.MOVIE, detected_name="Heuristic")
        analyst.analyze.return_value = result
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=None),
        )

        analysis = await coord._run_classification(_job_with_hash(), 1, [], None, is_staging=False)
        assert analysis is result
        coord._set_discdb_mappings.assert_not_called()

    async def test_disabled_flag_skips_network(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name="Heuristic")
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=False))

        spy = AsyncMock(return_value=_net_signal())
        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network", spy
        )

        await coord._run_classification(_job_with_hash(), 1, [], None, is_staging=False)
        spy.assert_not_called()

    async def test_no_content_hash_skips_network(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name="Heuristic")
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        spy = AsyncMock(return_value=_net_signal())
        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network", spy
        )

        job = _job()  # no content_hash
        await coord._run_classification(job, 1, [], None, is_staging=False)
        spy.assert_not_called()

    async def test_network_not_clobbered_by_ai_fallback(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(
            monkeypatch,
            _config(
                enable_fingerprint_identification=True,
                tmdb_api_key="key",
                ai_identification_enabled=True,
                ai_api_key="aikey",
            ),
        )

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=_net_signal(tier="canonical")),
        )
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            Mock(return_value={"name": "Breaking Bad"}),
        )
        # TMDB label lookup fails so the AI fallback would otherwise engage.
        tmdb_mock = Mock(return_value=None)
        monkeypatch.setattr("app.core.tmdb_classifier.classify_from_tmdb", tmdb_mock)
        ai_mock = AsyncMock(return_value={"title": "Wrong Show"})
        monkeypatch.setattr("app.core.ai_identifier.identify_from_label", ai_mock)

        scanned = [self._scanned(0, 2820, 5_000_000_000)]
        analysis = await coord._run_classification(
            _job_with_hash(), 1, scanned, None, is_staging=False
        )

        # Network identity wins; AI fallback must not run / overwrite.
        assert analysis.classification_source == "fingerprint_network"
        assert analysis.detected_name == "Breaking Bad"
        assert analysis.tmdb_id == 1396
        ai_mock.assert_not_called()

    async def test_name_resolution_failure_still_applies_override(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(detected_name=None)
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(enable_fingerprint_identification=True))

        monkeypatch.setattr(
            "app.services.identification_coordinator.identify_disc_via_network",
            AsyncMock(return_value=_net_signal(tier="canonical")),
        )
        # Name lookup raises -> swallowed, override still applied via tmdb_id.
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            Mock(side_effect=RuntimeError("boom")),
        )

        scanned = [self._scanned(0, 2820, 5_000_000_000)]
        analysis = await coord._run_classification(
            _job_with_hash(), 1, scanned, None, is_staging=False
        )

        assert analysis.content_type == ContentType.TV
        assert analysis.tmdb_id == 1396
        assert analysis.classification_source == "fingerprint_network"
        assert analysis.needs_review is False

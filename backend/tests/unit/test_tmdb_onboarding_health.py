"""#243 P2/P3 — TMDB onboarding health surfacing.

P2: the dashboard banner must be driven by an explicit backend boolean
(``tmdb_configured``), not by sniffing the redacted ``"***"`` key value.

P3: when classification proceeds without TMDB because the key is absent or
rejected, the cause must be told apart from "no results" (TmdbAuthError) and
surfaced as a human-readable reason on the job — over both REST and WS.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.api.routes import ConfigResponse, JobResponse
from app.api.websocket import ConnectionManager
from app.core.analyst import DiscAnalysisResult
from app.core.tmdb_classifier import (
    TMDB_DEGRADED_AUTH_FAILED,
    TMDB_DEGRADED_NOT_CONFIGURED,
    TmdbAuthError,
    TmdbSignal,
    classify_from_tmdb,
)
from app.models import DiscJob, JobState
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator

# ---------------------------------------------------------------------------
# P2 — tmdb_configured on the config response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTmdbConfiguredField:
    """GET /api/config redacts the key as "***"/"" — the frontend banner needs a
    dedicated boolean instead of guessing at masked values."""

    def test_config_response_exposes_tmdb_configured(self):
        assert "tmdb_configured" in ConfigResponse.model_fields

    async def test_get_config_reports_true_when_key_set(self, monkeypatch):
        from app.api import routes

        cfg = AppConfig(tmdb_api_key="eyJ" + "x" * 60)
        monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
        resp = await routes.get_config()
        assert resp.tmdb_configured is True
        assert resp.tmdb_api_key == "***"  # redaction contract unchanged

    async def test_get_config_reports_false_when_key_missing(self, monkeypatch):
        from app.api import routes

        cfg = AppConfig(tmdb_api_key="")
        monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
        resp = await routes.get_config()
        assert resp.tmdb_configured is False
        assert resp.tmdb_api_key == ""


# ---------------------------------------------------------------------------
# P3 — TmdbAuthError from the classifier
# ---------------------------------------------------------------------------


def _response(status: int) -> Mock:
    resp = Mock()
    resp.status_code = status
    return resp


@pytest.mark.unit
class TestClassifierAuthError:
    """401/403 means the key is bad — that must not look like 'show not found'."""

    def test_raises_on_401(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.tmdb_classifier.requests.get", Mock(return_value=_response(401))
        )
        with pytest.raises(TmdbAuthError):
            classify_from_tmdb("The Office", "k" * 41)

    def test_raises_on_403(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.tmdb_classifier.requests.get", Mock(return_value=_response(403))
        )
        with pytest.raises(TmdbAuthError):
            classify_from_tmdb("The Office", "k" * 41)

    def test_other_errors_still_degrade_to_none(self, monkeypatch):
        # Transient server errors keep the existing graceful degradation.
        monkeypatch.setattr(
            "app.core.tmdb_classifier.requests.get", Mock(return_value=_response(500))
        )
        assert classify_from_tmdb("The Office", "k" * 41) is None


# ---------------------------------------------------------------------------
# P3 — degraded-cause recorded during classification
# ---------------------------------------------------------------------------


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


def _make_coord(analyst):
    coord = IdentificationCoordinator(analyst, MagicMock(), MagicMock(), MagicMock())
    coord._set_discdb_mappings = Mock()
    return coord


def _patch_config(monkeypatch, config):
    monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=config))


@pytest.mark.unit
class TestClassificationDegradedReason:
    async def test_reason_set_when_key_not_configured(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(
            content_type=ContentType.TV, detected_name="The Office"
        )
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config())  # no API key

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis.tmdb_degraded_reason == TMDB_DEGRADED_NOT_CONFIGURED

    async def test_reason_set_when_key_rejected(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(
            content_type=ContentType.TV, detected_name="The Office"
        )
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(tmdb_api_key="bad-key" + "x" * 40))
        monkeypatch.setattr(
            "app.core.tmdb_classifier.classify_from_tmdb",
            Mock(side_effect=TmdbAuthError("TMDB returned 401")),
        )

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis.tmdb_degraded_reason == TMDB_DEGRADED_AUTH_FAILED

    async def test_no_reason_when_tmdb_works(self, monkeypatch):
        analyst = MagicMock()
        analyst.analyze.return_value = _analysis(
            content_type=ContentType.TV, detected_name="The Office"
        )
        coord = _make_coord(analyst)
        _patch_config(monkeypatch, _config(tmdb_api_key="k" * 41))
        signal = TmdbSignal(
            content_type=ContentType.TV, confidence=0.85, tmdb_id=2316, tmdb_name="The Office"
        )
        monkeypatch.setattr(
            "app.core.tmdb_classifier.classify_from_tmdb", Mock(return_value=signal)
        )

        analysis = await coord._run_classification(_job(), 1, [], None, is_staging=True)

        assert analysis.tmdb_degraded_reason is None


# ---------------------------------------------------------------------------
# P3 — the reason must reach the frontend over BOTH REST and WS
# (see project memory: update-status REST/WS serializer drift)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDegradedReasonSerialization:
    def test_discjob_column_defaults_none(self):
        assert _job().tmdb_degraded_reason is None

    def test_job_response_exposes_field(self):
        assert "tmdb_degraded_reason" in JobResponse.model_fields

    async def test_ws_broadcast_carries_field_and_empty_string_clears(self, monkeypatch):
        mgr = ConnectionManager()
        sent: list[dict] = []
        monkeypatch.setattr(mgr, "broadcast", AsyncMock(side_effect=sent.append))

        await mgr.broadcast_job_update(1, "ripping", tmdb_degraded_reason="degraded")
        # "" must be FORWARDED (it clears the field on the frontend merge);
        # only None means "unchanged" and is omitted.
        await mgr.broadcast_job_update(1, "ripping", tmdb_degraded_reason="")
        await mgr.broadcast_job_update(1, "ripping")

        assert sent[0]["tmdb_degraded_reason"] == "degraded"
        assert sent[1]["tmdb_degraded_reason"] == ""
        assert "tmdb_degraded_reason" not in sent[2]

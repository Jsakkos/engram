"""Schema + config surface for the TheTVDB episode namespace (spec 2026-10-08)."""

from app.models.app_config import AppConfig
from app.models.disc_job import DiscJob
from app.models.show_ordering import ShowOrderingPreference


def test_disc_job_defaults_to_tmdb_namespace():
    job = DiscJob(drive_id="E:", volume_label="JUSTICE_LEAGUE_S1D1")
    assert job.episode_namespace == "tmdb"
    assert job.tvdb_divergence_json is None
    assert job.episode_namespace_note is None
    assert job.episode_crosswalk_json is None


def test_episode_namespace_has_server_default_for_upgraded_rows():
    col = DiscJob.__table__.c.episode_namespace
    assert col.server_default is not None
    assert "tmdb" in str(col.server_default.arg)


def test_show_preference_tvdb_columns():
    pref = ShowOrderingPreference(tmdb_id=1618)
    assert pref.tvdb_id is None
    assert pref.tvdb_suggestion_dismissed is False
    col = ShowOrderingPreference.__table__.c.tvdb_suggestion_dismissed
    assert str(col.server_default.arg) == "0"


def test_appconfig_tvdb_key_defaults_blank():
    assert AppConfig().tvdb_api_key == ""

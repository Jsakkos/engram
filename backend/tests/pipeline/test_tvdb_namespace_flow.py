"""Justice League S1 on TheTVDB numbering: names and network rows (spec 2026-10-08).

Pure logic, no ASR: given a TVDB job whose 26 tracks matched S01E01..S01E26,
the organizer files them under those numbers (no TMDB projection) and the
contribution payload carries only TMDB-translatable rows.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core import episode_namespace as ns
from app.core.organizer import organize_tv_episode
from app.matcher.tvdb_client import _parse_episodes
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob
from app.services.disc_contribution_queue import _derive_assignment

FIX = Path(__file__).parent.parent / "fixtures" / "tvdb"


def _rosters():
    tmdb = json.loads((FIX / "justice_league_s1_tmdb.json").read_text("utf-8"))["episodes"]
    tvdb = _parse_episodes(
        json.loads((FIX / "justice_league_s1_tvdb.json").read_text("utf-8"))["response"], 1
    )
    return tmdb, tvdb


@pytest.mark.pipeline
def test_tvdb_job_files_26_episodes_without_projection(tmp_path, monkeypatch):
    import app.core.episode_ordering as eo

    def _boom(*_a, **_k):
        raise AssertionError("a tvdb job must never be projected")

    monkeypatch.setattr(eo, "project_episode", _boom)
    names = []
    with patch("app.services.config_service.get_config_sync", return_value=AppConfig()):
        for ep in range(1, 27):
            src = tmp_path / f"t{ep:02d}.mkv"
            src.write_bytes(b"x")
            result = organize_tv_episode(
                src,
                "Justice League",
                f"S01E{ep:02d}",
                library_path=tmp_path / "lib",
                conflict_resolution="skip",
                tmdb_id="1618",
                ordering="tvdb",
            )
            assert result["success"], result
            names.append(Path(result["final_path"]).name)
    assert any("S01E01" in n for n in names) and any("S01E26" in n for n in names)
    assert len(set(names)) == 26


@pytest.mark.pipeline
def test_contribution_rows_only_for_translatable_tracks():
    tmdb, tvdb = _rosters()
    crosswalk = json.dumps(ns.build_crosswalk(1, tmdb, tvdb))
    job = DiscJob(
        drive_id="E:",
        volume_label="JL",
        content_type=ContentType.TV,
        tmdb_id=1618,
        episode_namespace="tvdb",
        episode_crosswalk_json=crosswalk,
    )
    rows = [
        _derive_assignment(
            job,
            SimpleNamespace(is_extra=False, matched_episode=f"S01E{e:02d}", state=None),
        )
        for e in range(1, 27)
    ]
    assert [r[0] for r in rows[:3]] == ["discarded"] * 3  # Secret Origins parts
    assert rows[3] == ("episode", 1, 2)  # In Blackest Night (1)
    sent = [r for r in rows if r[0] == "episode"]
    assert len({(s, e) for _, s, e in sent}) == len(sent)  # no duplicate TMDB keys

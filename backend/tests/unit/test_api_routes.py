"""Unit tests for API routes.

Tests the REST API endpoints including job management, configuration,
and validation. Uses async client with in-memory DB (patched via conftest.py).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState

# Import the patched session factory from conftest
from tests.unit.conftest import _unit_session_factory


async def _seed_config(
    staging_path="/tmp/staging",
    makemkv_key="T-test-key-1234567890",
    tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
    **kwargs,
) -> AppConfig:
    """Insert a config row via the patched session factory."""
    async with _unit_session_factory() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key=makemkv_key,
            staging_path=staging_path,
            library_movies_path="/media/movies",
            library_tv_path="/media/tv",
            tmdb_api_key=tmdb_api_key,
            max_concurrent_matches=4,
            ffmpeg_path="/usr/bin/ffmpeg",
            conflict_resolution_default="rename",
            **kwargs,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


async def _seed_job(**kwargs) -> DiscJob:
    """Insert a job row via the patched session factory."""
    defaults = dict(
        drive_id="D:",
        volume_label="TEST_DISC",
        content_type=ContentType.TV,
        state=JobState.IDLE,
        detected_title="Test Show",
        detected_season=1,
        staging_path="/tmp/staging/job_123",
    )
    defaults.update(kwargs)
    async with _unit_session_factory() as session:
        job = DiscJob(**defaults)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _seed_titles(job_id: int, count: int = 3) -> list[DiscTitle]:
    """Insert title rows via the patched session factory."""
    async with _unit_session_factory() as session:
        titles = []
        for i in range(count):
            title = DiscTitle(
                job_id=job_id,
                title_index=i,
                duration_seconds=2400 + i * 60,
                file_size_bytes=1024 * 1024 * 1024,
                state=TitleState.PENDING,
            )
            session.add(title)
            titles.append(title)
        await session.commit()
        for t in titles:
            await session.refresh(t)
        return titles


@pytest.fixture
async def client():
    """Provide an async HTTP client with the patched DB session."""

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Job Endpoints
# ---------------------------------------------------------------------------


class TestJobEndpoints:
    """Test job-related API endpoints."""

    async def test_list_jobs_empty(self, client):
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_jobs_with_data(self, client):
        job = await _seed_job()
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["id"] == job.id
        assert jobs[0]["volume_label"] == "TEST_DISC"
        assert jobs[0]["state"] == "idle"

    async def test_get_job_by_id(self, client):
        job = await _seed_job()
        response = await client.get(f"/api/jobs/{job.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job.id
        assert data["detected_title"] == "Test Show"
        assert data["detected_season"] == 1

    async def test_get_job_not_found(self, client):
        response = await client.get("/api/jobs/999")
        assert response.status_code == 404

    async def test_candidates_json_exposed_in_job_and_detail(self, client):
        """Same-name twin candidates must survive the serializers in both the
        job response and the detail response, or the UI can't surface the
        "did you mean Frasier (2023)?" disambiguation (three-way-sync rule)."""
        payload = (
            '[{"tmdb_id": 3452, "name": "Frasier", "year": "1993"}, '
            '{"tmdb_id": 195241, "name": "Frasier", "year": "2023"}]'
        )
        job = await _seed_job(candidates_json=payload)

        resp = await client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        assert resp.json()["candidates_json"] == payload

        detail = await client.get(f"/api/jobs/{job.id}/detail")
        assert detail.status_code == 200
        assert detail.json()["candidates_json"] == payload

    async def test_identity_prompt_json_exposed_in_job_and_detail(self, client):
        """identity_prompt_json must survive BOTH the JobResponse serializer and
        build_job_detail() — three-way-sync rule; REST/WS serializer drift is a
        documented recurring bug class. Mirrors the candidates_json guard above.
        Also verifies the field defaults to null (not omitted) when not set, so
        the frontend merge can discriminate present-and-null from absent."""
        prompt = '{"kind": "season", "reason": "Could not detect season automatically"}'
        with_prompt = await _seed_job(identity_prompt_json=prompt)
        without_prompt = await _seed_job(volume_label="NO_PROMPT")

        resp = await client.get(f"/api/jobs/{with_prompt.id}")
        assert resp.status_code == 200
        assert resp.json()["identity_prompt_json"] == prompt

        detail = await client.get(f"/api/jobs/{with_prompt.id}/detail")
        assert detail.status_code == 200
        assert detail.json()["identity_prompt_json"] == prompt

        # Null when not set — field must be present (not omitted) in both payloads
        resp2 = await client.get(f"/api/jobs/{without_prompt.id}")
        assert resp2.json()["identity_prompt_json"] is None

        detail2 = await client.get(f"/api/jobs/{without_prompt.id}/detail")
        assert detail2.json()["identity_prompt_json"] is None

    async def test_tmdb_identity_fields_exposed_in_job_response(self, client):
        """The dashboard reads tmdb_id to suppress the dead-end episode-review
        button, and the re-identify modal shows tmdb_name/tmdb_year. These must
        survive the JobResponse serializer in both list and by-id endpoints —
        present (even as null) for an unconfirmed disc, populated for a known one."""
        confirmed = await _seed_job(tmdb_id=18409, tmdb_name="The Office", tmdb_year=2005)
        unconfirmed = await _seed_job(volume_label="AMBIGUOUS", tmdb_id=None)

        by_id = (await client.get(f"/api/jobs/{confirmed.id}")).json()
        assert by_id["tmdb_id"] == 18409
        assert by_id["tmdb_name"] == "The Office"
        assert by_id["tmdb_year"] == 2005

        listed = {j["id"]: j for j in (await client.get("/api/jobs")).json()}
        # Null identity is present-as-null (not omitted), so `tmdb_id == null`
        # is a reliable client-side discriminator.
        assert listed[unconfirmed.id]["tmdb_id"] is None
        assert listed[confirmed.id]["tmdb_id"] == 18409

    async def test_tmdb_identity_fields_exposed_in_job_detail(self, client):
        """build_job_detail() assembles the detail dict manually, so tmdb_year must be
        declared in JobDetailResponse AND added to the dict — Pydantic silently drops a
        field present in only one of the two."""
        confirmed = await _seed_job(tmdb_id=18409, tmdb_name="The Office", tmdb_year=2005)

        detail = (await client.get(f"/api/jobs/{confirmed.id}/detail")).json()
        assert detail["tmdb_id"] == 18409
        assert detail["tmdb_name"] == "The Office"
        assert detail["tmdb_year"] == 2005

    async def test_get_job_titles(self, client):
        job = await _seed_job()
        await _seed_titles(job.id, count=3)
        response = await client.get(f"/api/jobs/{job.id}/titles")
        assert response.status_code == 200
        titles = response.json()
        assert len(titles) == 3
        assert titles[0]["title_index"] == 0
        assert titles[0]["state"] == "pending"

    async def test_start_job_not_found(self, client):
        response = await client.post("/api/jobs/999/start")
        assert response.status_code == 404

    async def test_cancel_job_not_found(self, client):
        response = await client.post("/api/jobs/999/cancel")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Config Endpoints
# ---------------------------------------------------------------------------


class TestConfigEndpoints:
    """Test configuration API endpoints."""

    async def test_get_config_redacts_api_keys(self, client):
        await _seed_config()
        response = await client.get("/api/config")
        assert response.status_code == 200
        config = response.json()
        assert config["makemkv_key"] == "***"
        assert config["tmdb_api_key"] == "***"
        assert config["makemkv_path"] == "/usr/bin/makemkvcon"
        assert config["staging_path"] == "/tmp/staging"
        assert config["library_movies_path"] == "/media/movies"

    async def test_get_config_creates_default_when_empty(self, client):
        response = await client.get("/api/config")
        assert response.status_code == 200

    async def test_allow_lan_access_defaults_false(self, client):
        await _seed_config()
        config = (await client.get("/api/config")).json()
        assert config["allow_lan_access"] is False

    async def test_allow_lan_access_roundtrips(self, client):
        await _seed_config()
        response = await client.put("/api/config", json={"allow_lan_access": True})
        assert response.status_code == 200
        config = (await client.get("/api/config")).json()
        assert config["allow_lan_access"] is True

    async def test_update_config(self, client):
        await _seed_config()
        update_data = {
            "staging_path": "/new/staging/path",
            "max_concurrent_matches": 8,
        }
        response = await client.put("/api/config", json=update_data)
        assert response.status_code == 200

        verify = await client.get("/api/config")
        config = verify.json()
        assert config["staging_path"] == "/new/staging/path"
        assert config["max_concurrent_matches"] == 8

    async def test_update_config_with_new_api_keys(self, client):
        await _seed_config()
        update_data = {
            "makemkv_key": "T-new-key-0987654321",
            "tmdb_api_key": "eyJhbGciOiJIUzI1NiJ9.new_token",
        }
        response = await client.put("/api/config", json=update_data)
        assert response.status_code == 200

        verify = await client.get("/api/config")
        config = verify.json()
        assert config["makemkv_key"] == "***"
        assert config["tmdb_api_key"] == "***"

    async def test_ai_api_key_persists_and_blank_does_not_clobber(self, client):
        """Reproduces the user's report end-to-end through the real routes:

        a saved AI key must read back as '***' (so the UI shows "Key saved"),
        and re-saving must not wipe it — neither when the field is omitted (the
        frontend's blank-save behavior) nor when an empty string is sent directly.
        """
        await _seed_config()
        # User enters their Gemini key.
        r = await client.put("/api/config", json={"ai_api_key": "AIzaSy-secret-123"})
        assert r.status_code == 200
        # Reopening settings: GET signals a saved key (the UI's "Key saved" cue).
        assert (await client.get("/api/config")).json()["ai_api_key"] == "***"
        # An unchanged save (frontend omits the blank field) must not clobber it.
        r = await client.put("/api/config", json={"staging_path": "/some/where"})
        assert r.status_code == 200
        assert (await client.get("/api/config")).json()["ai_api_key"] == "***"
        # Defense-in-depth: even a direct blank must not clobber the stored key.
        r = await client.put("/api/config", json={"ai_api_key": ""})
        assert r.status_code == 200
        assert (await client.get("/api/config")).json()["ai_api_key"] == "***"

    async def test_ai_episode_matching_enabled_roundtrips(self, client):
        """The AI episode-matching toggle must persist AND read back.

        It gates the no-subtitle AI fallback (and the post-match LLM suggestion),
        so if the API can't save/return it the whole feature is unreachable from
        the UI — the checkbox would silently reset to off on every reload.
        """
        await _seed_config()
        r = await client.put("/api/config", json={"ai_episode_matching_enabled": True})
        assert r.status_code == 200
        config = (await client.get("/api/config")).json()
        assert config["ai_episode_matching_enabled"] is True

    async def test_pretranscription_flags_default_on_and_off(self, client):
        """GET must expose both prewarmer flags with their defaults.

        The master switch ships enabled; the expensive full-file option ships
        disabled. If either is missing from ConfigResponse the prewarmer
        becomes uncontrollable from the UI (the PR #283 bug class).
        """
        await _seed_config()
        config = (await client.get("/api/config")).json()
        assert config["enable_background_pretranscription"] is True
        assert config["pretranscribe_full_file"] is False

    async def test_pretranscription_flags_roundtrip(self, client):
        """PUT must persist each prewarmer flag and read it back."""
        await _seed_config()
        r = await client.put(
            "/api/config",
            json={
                "enable_background_pretranscription": False,
                "pretranscribe_full_file": True,
            },
        )
        assert r.status_code == 200
        config = (await client.get("/api/config")).json()
        assert config["enable_background_pretranscription"] is False
        assert config["pretranscribe_full_file"] is True

    async def test_pretranscription_flags_unrelated_put_leaves_unchanged(self, client):
        """A PUT that omits both flags must not reset them to defaults."""
        await _seed_config()
        r = await client.put(
            "/api/config",
            json={
                "enable_background_pretranscription": False,
                "pretranscribe_full_file": True,
            },
        )
        assert r.status_code == 200
        # Unrelated update — neither flag in the payload.
        r = await client.put("/api/config", json={"staging_path": "/some/where"})
        assert r.status_code == 200
        config = (await client.get("/api/config")).json()
        assert config["enable_background_pretranscription"] is False
        assert config["pretranscribe_full_file"] is True


# ---------------------------------------------------------------------------
# Network info
# ---------------------------------------------------------------------------


class TestNetworkInfoEndpoint:
    """Test the LAN access network info endpoint."""

    async def test_reports_disabled_by_default(self, client):
        await _seed_config()
        info = (await client.get("/api/network/info")).json()
        assert info["lan_access_enabled"] is False
        assert info["active_lan_bound"] is False
        assert isinstance(info["port"], int)

    async def test_reports_enabled_toggle_before_restart(self, client):
        # Toggle persisted but server still bound to localhost this session:
        # enabled True, active_lan_bound False → UI shows "restart to apply".
        await _seed_config(allow_lan_access=True)
        info = (await client.get("/api/network/info")).json()
        assert info["lan_access_enabled"] is True
        assert info["active_lan_bound"] is False

    async def test_active_when_bound_all_interfaces(self, client):
        await _seed_config(allow_lan_access=True)
        app.state.bound_host = "0.0.0.0"
        app.state.bound_port = 8000
        try:
            info = (await client.get("/api/network/info")).json()
        finally:
            del app.state.bound_host
            del app.state.bound_port
        assert info["active_lan_bound"] is True
        assert info["port"] == 8000
        # lan_ip may be None in a network-less CI sandbox; when present, the URL
        # is derived from it.
        if info["lan_ip"] is not None:
            assert info["lan_url"] == f"http://{info['lan_ip']}:8000"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Test API request validation."""

    async def test_invalid_job_id_type(self, client):
        response = await client.get("/api/jobs/invalid")
        assert response.status_code == 422

    async def test_invalid_config_values(self, client):
        await _seed_config()
        invalid_data = {"max_concurrent_matches": -1}
        response = await client.put("/api/config", json=invalid_data)
        assert response.status_code in [200, 400, 422]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error handling in API endpoints."""

    async def test_malformed_json(self, client):
        response = await client.put(
            "/api/config",
            content="{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    async def test_delete_single_job(self, client):
        """Clearing a job soft-deletes it (sets cleared_at), hiding from list."""
        job = await _seed_job(state=JobState.COMPLETED)
        response = await client.delete(f"/api/jobs/{job.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cleared"
        # Job still accessible directly (soft-deleted)
        verify = await client.get(f"/api/jobs/{job.id}")
        assert verify.status_code == 200
        # But hidden from the active list
        list_resp = await client.get("/api/jobs")
        job_ids = [j["id"] for j in list_resp.json()]
        assert job.id not in job_ids


# ---------------------------------------------------------------------------
# ASR Status
# ---------------------------------------------------------------------------


class TestAsrStatusEndpoint:
    async def test_asr_status_reports_cpu_runtime(self, client):
        from unittest.mock import patch

        with (
            patch("app.matcher.asr_models.detect_asr_device", return_value="cpu"),
            patch("app.matcher.asr_models.psutil.cpu_count", return_value=8),
        ):
            resp = await client.get("/api/asr-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["device"] == "cpu"
        assert body["compute_type"] == "int8"
        assert body["workers"] >= 1
        assert "max_concurrent_matches" in body
        assert "model" in body

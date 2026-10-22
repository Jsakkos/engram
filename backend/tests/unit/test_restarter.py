"""Tests for the plain-restart support behind "Restart now" (#722)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.guards import require_localhost_or_lan
from app.core import restarter
from app.core.updater import UpdateError
from app.main import app


class TestChangedRestartFields:
    def test_reports_only_changed_restart_fields(self):
        before = SimpleNamespace(max_concurrent_matches=2, enable_gpu_acceleration=False)
        update = {
            "max_concurrent_matches": 4,
            "enable_gpu_acceleration": False,
            "tmdb_api_key": "x",
        }
        assert restarter.changed_restart_fields(before, update) == ["max_concurrent_matches"]

    def test_unchanged_value_is_not_reported(self):
        before = SimpleNamespace(max_concurrent_matches=2)
        assert restarter.changed_restart_fields(before, {"max_concurrent_matches": 2}) == []

    def test_live_settings_are_not_restart_fields(self):
        for live in ("conflict_resolution_default", "extras_policy", "always_review"):
            assert live not in restarter.RESTART_REQUIRED_FIELDS


class TestRestartSupport:
    def test_packaged(self):
        with patch.object(restarter, "is_frozen", return_value=True):
            s = restarter.restart_support()
        assert s.mode == "packaged" and s.can_restart

    def test_docker_gets_instructions(self):
        with (
            patch.object(restarter, "is_frozen", return_value=False),
            patch("os.path.exists", return_value=True),
        ):
            s = restarter.restart_support()
        assert s.mode == "docker" and not s.can_restart and "container" in s.hint

    def test_source_gets_instructions(self):
        with (
            patch.object(restarter, "is_frozen", return_value=False),
            patch("os.path.exists", return_value=False),
        ):
            s = restarter.restart_support()
        assert s.mode == "source" and not s.can_restart and s.hint


class TestRestartBat:
    def test_waits_for_pid_then_relaunches_with_flag(self):
        bat = restarter.render_restart_bat(
            exe=r"C:\Engram\engram.exe", args=["--updated"], pid=4242, log_path=r"C:\l.log"
        )
        assert "PID eq 4242" in bat and "IMAGENAME eq engram.exe" in bat
        assert 'start "" "C:\\Engram\\engram.exe" "--updated"' in bat
        assert "ping -n 2" in bat and "timeout" not in bat


@pytest.fixture
async def client():
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


HDR = {"X-Engram-Request": "1", "Host": "localhost:8000"}
PACKAGED = restarter.RestartSupport("packaged", True, "")
SOURCE = restarter.RestartSupport("source", False, "Stop and start the backend.")


class TestRestartEndpoints:
    async def test_status_idle_packaged(self, client):
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=AsyncMock()),
        ):
            body = (await client.get("/api/restart/status")).json()
        assert body == {"mode": "packaged", "can_restart": True, "hint": "", "active_jobs": False}

    async def test_status_reports_active_jobs(self, client):
        busy = AsyncMock(side_effect=UpdateError("A disc operation is in progress."))
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=busy),
        ):
            body = (await client.get("/api/restart/status")).json()
        assert body["active_jobs"] is True

    async def test_restart_refused_when_not_packaged(self, client):
        with patch("app.api.routes.restart_support", return_value=SOURCE):
            resp = await client.post("/api/restart", headers=HDR)
        assert resp.status_code == 400
        assert "Stop and start" in resp.json()["detail"]

    async def test_restart_refused_while_jobs_active(self, client):
        busy = AsyncMock(side_effect=UpdateError("A disc operation is in progress."))
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=busy),
            patch("app.api.routes.restart_now") as go,
        ):
            resp = await client.post("/api/restart", headers=HDR)
        assert resp.status_code == 409
        go.assert_not_called()

    async def test_restart_schedules_relaunch_after_response(self, client):
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=AsyncMock()),
            patch("app.api.routes.restart_now") as go,
        ):
            resp = await client.post("/api/restart", headers=HDR)
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        go.assert_called_once()


class TestRestartCsrfGuard:
    async def test_cross_site_style_post_without_header_is_refused(self, client):
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=AsyncMock()),
            patch("app.api.routes.restart_now") as go,
        ):
            resp = await client.post("/api/restart", headers={"Host": "localhost:8000"})
        assert resp.status_code == 403
        go.assert_not_called()


class TestRestartRebindingGuard:
    async def test_rebound_hostname_is_refused(self, client):
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=AsyncMock()),
            patch("app.api.routes.restart_now") as go,
        ):
            resp = await client.post(
                "/api/restart", headers={**HDR, "Host": "attacker.example.com:8000"}
            )
        assert resp.status_code == 403
        go.assert_not_called()

    async def test_ip_literal_host_is_allowed(self, client):
        with (
            patch("app.api.routes.restart_support", return_value=PACKAGED),
            patch("app.api.routes.update_checker._check_no_active_jobs", new=AsyncMock()),
            patch("app.api.routes.restart_now"),
        ):
            resp = await client.post("/api/restart", headers={**HDR, "Host": "192.168.1.5:8000"})
        assert resp.status_code == 200


class TestPutConfigReportsRestart:
    async def test_put_config_lists_changed_restart_fields(self, client):
        before = SimpleNamespace(max_concurrent_matches=2)
        with (
            patch("app.services.config_service.get_config", new=AsyncMock(return_value=before)),
            patch("app.services.config_service.update_config", new=AsyncMock()),
            patch("app.api.routes.validate_config_update", new=AsyncMock(side_effect=lambda d: d)),
        ):
            body = (await client.put("/api/config", json={"max_concurrent_matches": 5})).json()
        assert body["restart_required"] == ["max_concurrent_matches"]
        assert body["restart_required_labels"] == ["Concurrent matches"]

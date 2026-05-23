"""Unit tests for the diagnostics / bug-report endpoint.

Verifies that GET /api/diagnostics/report surfaces *real* data (app/tool
versions, job context) rather than placeholders, and that the version source of
truth cannot silently drift between app/__init__.py and pyproject.toml.
"""

import tomllib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app as app_pkg
from app.api.validation import ToolDetectionResult
from app.database import get_session
from app.main import app
from tests.unit.conftest import _unit_session_factory
from tests.unit.test_api_routes import _seed_job


@pytest.fixture
async def client():
    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def fake_tools(monkeypatch):
    """Patch tool detection so the test does not depend on installed binaries.

    The endpoint resolves these names lazily (``from app.api.validation import
    ...`` inside the handler), so we patch them on the source module — that is
    where the lookup happens at call time.
    """
    monkeypatch.setattr(
        "app.api.validation.detect_makemkv",
        lambda: ToolDetectionResult(
            found=True, path="/usr/bin/makemkvcon", version="MakeMKV v1.17.7"
        ),
    )
    monkeypatch.setattr(
        "app.api.validation.detect_ffmpeg",
        lambda: ToolDetectionResult(
            found=True, path="/usr/bin/ffmpeg", version="ffmpeg version 6.1.1"
        ),
    )


class TestBugReport:
    async def test_report_contains_real_versions(self, client, fake_tools):
        resp = await client.get("/api/diagnostics/report")
        assert resp.status_code == 200
        data = resp.json()

        # App version is the real package version, never the frozen-build fallback.
        assert data["app_version"] == app_pkg.__version__
        assert data["app_version"] != "0.0.0"

        assert data["python_version"]
        assert data["os"]
        assert data["makemkv_version"] == "MakeMKV v1.17.7"
        assert data["ffmpeg_version"] == "ffmpeg version 6.1.1"

        # The copy-to-clipboard payload mirrors the GitHub issue body.
        assert "## Bug Report" in data["markdown"]
        assert "MakeMKV v1.17.7" in data["markdown"]
        assert "ffmpeg version 6.1.1" in data["markdown"]
        assert data["github_url"].startswith("https://github.com/")

    async def test_report_includes_job_context(self, client, fake_tools):
        from app.models.disc_job import JobState

        job = await _seed_job(
            volume_label="INCEPTION_2010",
            state=JobState.FAILED,
            error_message="boom during ripping",
        )

        resp = await client.get(f"/api/diagnostics/report?job_id={job.id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["job"] is not None
        assert data["job"]["id"] == job.id
        assert data["job"]["volume_label"] == "INCEPTION_2010"
        assert data["job"]["state"] == "failed"
        assert data["job"]["error"] == "boom during ripping"
        assert "Job Context" in data["markdown"]
        assert "boom during ripping" in data["markdown"]

    async def test_report_reports_missing_tools(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.validation.detect_makemkv",
            lambda: ToolDetectionResult(found=False, error="MakeMKV not found"),
        )
        monkeypatch.setattr(
            "app.api.validation.detect_ffmpeg",
            lambda: ToolDetectionResult(found=False, error="FFmpeg not found"),
        )

        resp = await client.get("/api/diagnostics/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["makemkv_version"] == "MakeMKV not found"
        assert data["ffmpeg_version"] == "FFmpeg not found"


def test_version_source_of_truth_is_consistent():
    """pyproject.toml must match app.__version__ so the reported version is real."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert declared == app_pkg.__version__, (
        f"pyproject.toml version {declared!r} != app.__version__ "
        f"{app_pkg.__version__!r} — bump them together."
    )

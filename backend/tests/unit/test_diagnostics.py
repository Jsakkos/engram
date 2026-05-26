"""Unit tests for the diagnostics / bug-report endpoint.

Verifies that GET /api/diagnostics/report surfaces *real* data (app/tool
versions, job context) rather than placeholders, and that the version source of
truth cannot silently drift between app/__init__.py and pyproject.toml.
"""

import io
import tomllib
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app as app_pkg
from app.api.validation import ToolDetectionResult
from app.database import get_session
from app.main import app
from app.models.disc_job import DiscTitle, TitleState
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


class TestBundle:
    async def test_bundle_returns_zip_with_expected_members(self, client, fake_tools, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes._read_job_tagged_logs",
            lambda *a, **k: (["2026-01-01 00:00:00 | INFO | job=1 | x:y:1 - hello"], False),
        )
        job = await _seed_job(volume_label="THE_OFFICE_S1")

        resp = await client.get(f"/api/diagnostics/report/{job.id}/bundle")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert f"engram-bug-report-job-{job.id}.zip" in resp.headers["content-disposition"]

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())
        assert {"report.md", "job-detail.json", "job-logs.txt"} <= names
        assert "## Bug Report" in zf.read("report.md").decode()

    async def test_bundle_includes_scan_log(self, client, fake_tools, monkeypatch, tmp_path):
        log_dir = tmp_path / "makemkv"
        log_dir.mkdir()
        (log_dir / "scan.log").write_text("MSG: scan output line\n", encoding="utf-8")
        monkeypatch.setattr("app.api.routes.get_makemkv_log_dir", lambda jid: log_dir)
        monkeypatch.setattr("app.api.routes._read_job_tagged_logs", lambda *a, **k: ([], False))
        job = await _seed_job()

        resp = await client.get(f"/api/diagnostics/report/{job.id}/bundle")
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "scan.log" in zf.namelist()
        assert "scan output line" in zf.read("scan.log").decode()

    async def test_bundle_redacts_secrets(self, client, fake_tools, monkeypatch):
        secret = "eyJhbGciOiJIUzITESTsupersecretpayloadvalue1234567890"
        # error_message lands in report.md (not just job-detail.json) — it must
        # be sanitized too.
        err_secret = "eyJERRORtokensecretvaluethatislongenough1234567890"
        monkeypatch.setattr("app.api.routes._read_job_tagged_logs", lambda *a, **k: ([], False))
        job = await _seed_job(
            volume_label="SECRET_DISC", error_message=f"rip failed: token={err_secret}"
        )
        async with _unit_session_factory() as session:
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=0,
                    duration_seconds=1200,
                    file_size_bytes=1,
                    match_details=f"token={secret}",
                    state=TitleState.MATCHED,
                )
            )
            await session.commit()

        resp = await client.get(f"/api/diagnostics/report/{job.id}/bundle")
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        combined = b"\n".join(zf.read(n) for n in zf.namelist())
        assert secret.encode() not in combined
        # report.md error field is sanitized, not just job-detail.json.
        report_md = zf.read("report.md")
        assert err_secret.encode() not in report_md
        assert b"***REDACTED***" in combined

    async def test_bundle_404_for_missing_job(self, client, fake_tools):
        resp = await client.get("/api/diagnostics/report/999999/bundle")
        assert resp.status_code == 404


class TestJobTaggedLogReader:
    def test_filters_to_matching_job(self, tmp_path):
        from app.api.routes import _read_job_tagged_logs

        log = tmp_path / "engram.log"
        log.write_text(
            "t | INFO | job=42 | a:b:1 - keep me\n"
            "t | INFO | job=99 | a:b:1 - drop me\n"
            "t | INFO | job=- | a:b:1 - untagged drop\n"
            "t | INFO | job=42 | a:b:2 - keep me too\n",
            encoding="utf-8",
        )
        lines, is_fallback = _read_job_tagged_logs(42, log_path=log)
        assert is_fallback is False
        assert len(lines) == 2
        assert all("job=42 |" in ln for ln in lines)
        assert not any("drop" in ln for ln in lines)

    def test_falls_back_to_global_errors_when_untagged(self, tmp_path):
        from app.api.routes import _read_job_tagged_logs

        log = tmp_path / "engram.log"
        log.write_text(
            "t | ERROR | job=- | a:b:1 - something broke\n"
            "t | INFO | job=7 | a:b:1 - unrelated job\n",
            encoding="utf-8",
        )
        # Job 1234 has no tagged lines → fallback to global ERROR tail.
        lines, is_fallback = _read_job_tagged_logs(1234, log_path=log)
        assert is_fallback is True
        assert any("something broke" in ln for ln in lines)

    def test_absent_log_marks_fallback(self, tmp_path):
        from app.api.routes import _read_job_tagged_logs

        # Missing log file: empty, but flagged as fallback so the bundle never
        # mislabels "(no logs)" as job-specific.
        lines, is_fallback = _read_job_tagged_logs(1, log_path=tmp_path / "nope.log")
        assert lines == []
        assert is_fallback is True


def test_sanitize_obj_redacts_home_and_secrets():
    from app.api.routes import _sanitize_obj

    home = str(Path.home())
    data = {
        "staging_path": f"{home}/.engram/staging/X",
        "match_details": "token=eyJsecrettokenvaluethatislong1234567890",
        "nested": [{"final_path": f"{home}/media"}],
    }
    out = _sanitize_obj(data)
    assert home not in out["staging_path"]
    assert out["staging_path"].startswith("~")
    assert "***REDACTED***" in out["match_details"]
    assert home not in out["nested"][0]["final_path"]


def test_version_source_of_truth_is_consistent():
    """pyproject.toml must match app.__version__ so the reported version is real."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert declared == app_pkg.__version__, (
        f"pyproject.toml version {declared!r} != app.__version__ "
        f"{app_pkg.__version__!r} — bump them together."
    )

"""Integration tests for /api/fingerprint/bootstrap/scan and /accept.

Heavy external dependencies (TMDB, fpcalc, filesystem walks) are monkeypatched
so tests run without a real TV library, real TMDB credentials, or fpcalc binary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import require_localhost
from app.database import async_session, init_db
from app.main import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize DB and scrub bootstrap rows between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM fingerprint_contributions"))
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Async HTTP client backed by the FastAPI app. Bypasses localhost guard."""
    app.dependency_overrides[require_localhost] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost, None)


# ---------------------------------------------------------------------------
# /api/fingerprint/bootstrap/scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_groups_parseable_files_by_show(client, tmp_path):
    """walk_library results are grouped by show; resolved show has tmdb_id set."""
    # Create labeled MKV files for "Severance"
    (tmp_path / "Severance - S01E01.mkv").touch()
    (tmp_path / "Severance - S01E02.mkv").touch()
    # Unparseable file (no Show - SnnEnn pattern)
    (tmp_path / "Random Clip.mkv").touch()

    def fake_fetch_show_id(show_name: str) -> str | None:
        if "Severance" in show_name:
            return "95396"
        return None

    fake_details = {
        "name": "Severance",
        "original_name": "Severance",
        "first_air_date": "2022-02-18",
    }

    with (
        patch("app.api.routes.asyncio.to_thread") as mock_to_thread,
    ):
        # to_thread is called for fetch_show_id and fetch_show_details; dispatch by callable.
        async def side_effect(fn, *args, **kwargs):
            fn_name = getattr(fn, "__name__", "") or getattr(fn, "__qualname__", "")
            if "fetch_show_id" in fn_name:
                return fake_fetch_show_id(*args)
            if "fetch_show_details" in fn_name:
                return fake_details
            return fn(*args, **kwargs)

        mock_to_thread.side_effect = side_effect

        resp = await client.post("/api/fingerprint/bootstrap/scan", json={"path": str(tmp_path)})

    assert resp.status_code == 200, resp.text
    data = resp.json()

    shows = data["shows"]
    assert len(shows) == 1
    show = shows[0]
    assert show["folder_name"] == "Severance"
    assert show["tmdb_id"] == 95396
    assert show["tmdb_name"] == "Severance"
    assert show["tmdb_year"] == 2022
    assert show["resolved"] is True
    assert show["episode_count"] == 2
    assert len(show["episodes"]) == 2

    unparseable = data["unparseable"]
    unparseable_files = [u["file"] for u in unparseable]
    assert any("Random Clip.mkv" in f for f in unparseable_files)

    summary = data["summary"]
    assert summary["parsed"] == 2
    assert summary["unparseable"] == 1
    assert summary["shows"] == 1
    assert summary["total_files"] == 3


@pytest.mark.asyncio
async def test_scan_unresolved_show_has_null_tmdb(client, tmp_path):
    """A show whose name doesn't match TMDB gets resolved=false and null tmdb_id."""
    (tmp_path / "UnknownShow - S01E01.mkv").touch()

    with patch("app.api.routes.asyncio.to_thread") as mock_to_thread:

        async def side_effect(fn, *args, **kwargs):
            fn_name = getattr(fn, "__name__", "") or getattr(fn, "__qualname__", "")
            if "fetch_show_id" in fn_name:
                return None  # unresolved
            return fn(*args, **kwargs)

        mock_to_thread.side_effect = side_effect

        resp = await client.post("/api/fingerprint/bootstrap/scan", json={"path": str(tmp_path)})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    shows = data["shows"]
    assert len(shows) == 1
    show = shows[0]
    assert show["resolved"] is False
    assert show["tmdb_id"] is None
    assert show["tmdb_name"] is None


@pytest.mark.asyncio
async def test_scan_nonexistent_path_returns_400(client):
    """A path that does not exist on disk returns HTTP 400."""
    resp = await client.post(
        "/api/fingerprint/bootstrap/scan",
        json={"path": "/nonexistent/path/that/does/not/exist"},
    )
    assert resp.status_code == 400
    assert (
        "does not exist" in resp.json()["detail"].lower()
        or "directory" in resp.json()["detail"].lower()
    )


@pytest.mark.asyncio
async def test_scan_file_path_returns_400(client, tmp_path):
    """Passing a file path (not a directory) returns HTTP 400."""
    f = tmp_path / "not_a_dir.mkv"
    f.touch()
    resp = await client.post(
        "/api/fingerprint/bootstrap/scan",
        json={"path": str(f)},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_scan_skips_extras_directory(client, tmp_path):
    """Files inside an Extras/ sub-directory are treated as unparseable (not grouped)."""
    extras_dir = tmp_path / "Extras"
    extras_dir.mkdir()
    (extras_dir / "Severance - S01E01.mkv").touch()
    # A parseable file outside Extras
    (tmp_path / "Severance - S01E02.mkv").touch()

    with patch("app.api.routes.asyncio.to_thread") as mock_to_thread:

        async def side_effect(fn, *args, **kwargs):
            fn_name = getattr(fn, "__name__", "") or getattr(fn, "__qualname__", "")
            if "fetch_show_id" in fn_name:
                return "95396"
            if "fetch_show_details" in fn_name:
                return {"name": "Severance", "first_air_date": "2022-02-18"}
            return fn(*args, **kwargs)

        mock_to_thread.side_effect = side_effect

        resp = await client.post("/api/fingerprint/bootstrap/scan", json={"path": str(tmp_path)})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the file outside Extras should be parsed
    assert data["summary"]["parsed"] == 1
    show = data["shows"][0]
    assert show["episode_count"] == 1


# ---------------------------------------------------------------------------
# /api/fingerprint/bootstrap/accept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_enqueues_contributions(client, tmp_path):
    """Two items with a mocked extractor produce queued=2, failed=0 and DB rows."""
    from app.matcher.chromaprint_extractor import ChromaprintResult

    fake_result = ChromaprintResult(
        hashes=[1, 2, 3, 4],
        duration_seconds=42.0,
        fpcalc_version="fpcalc version 1.5.1",
    )

    items = [
        {"file": str(tmp_path / "ep1.mkv"), "tmdb_id": 95396, "season": 1, "episode": 1},
        {"file": str(tmp_path / "ep2.mkv"), "tmdb_id": 95396, "season": 1, "episode": 2},
    ]

    # ChromaprintExtractor is imported lazily inside the endpoint body, so we
    # patch it at its source module rather than on app.api.routes.
    with (
        patch("app.api.routes.asyncio.to_thread") as mock_to_thread,
        patch("app.matcher.chromaprint_extractor.ChromaprintExtractor") as MockExtractor,
    ):
        # detect_fpcalc runs in to_thread; return a found result
        from app.api.validation import ToolDetectionResult

        mock_to_thread.return_value = ToolDetectionResult(found=True, path="/usr/bin/fpcalc")

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract = AsyncMock(return_value=fake_result)
        MockExtractor.return_value = mock_extractor_instance

        # Seed a pseudonym and contributions_enabled so the queue actually inserts
        from app.services.config_service import update_config as update_db_config

        await update_db_config(
            contribution_pseudonym="test-pseudonym-bootstrap",
            enable_fingerprint_contributions=True,
            fpcalc_path=None,  # force auto-detect path via to_thread
        )

        resp = await client.post("/api/fingerprint/bootstrap/accept", json={"items": items})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["queued"] == 2
    assert data["failed"] == 0

    # Verify rows were written to the DB
    async with async_session() as session:
        from sqlmodel import select as sqlmodel_select

        from app.models.fingerprint import FingerprintContribution

        rows = (
            (
                await session.execute(
                    sqlmodel_select(FingerprintContribution).where(
                        FingerprintContribution.match_source == "bootstrap"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.match_source == "bootstrap"
            assert row.match_confidence == 1.0
            assert row.tmdb_id == 95396


@pytest.mark.asyncio
async def test_accept_partial_failure_does_not_abort_batch(client, tmp_path):
    """A failed extraction on one item is counted as failed without stopping others."""
    from app.matcher.chromaprint_extractor import ChromaprintResult

    fake_result = ChromaprintResult(
        hashes=[10, 20],
        duration_seconds=30.0,
        fpcalc_version="fpcalc version 1.5.1",
    )

    items = [
        {"file": str(tmp_path / "good.mkv"), "tmdb_id": 12345, "season": 1, "episode": 1},
        {"file": str(tmp_path / "bad.mkv"), "tmdb_id": 12345, "season": 1, "episode": 2},
    ]

    call_count = 0

    async def extract_side_effect(path: str) -> ChromaprintResult:
        nonlocal call_count
        call_count += 1
        if "bad.mkv" in path:
            raise RuntimeError("Simulated fpcalc failure")
        return fake_result

    with (
        patch("app.api.routes.asyncio.to_thread") as mock_to_thread,
        patch("app.matcher.chromaprint_extractor.ChromaprintExtractor") as MockExtractor,
    ):
        from app.api.validation import ToolDetectionResult

        mock_to_thread.return_value = ToolDetectionResult(found=True, path="/usr/bin/fpcalc")

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract = AsyncMock(side_effect=extract_side_effect)
        MockExtractor.return_value = mock_extractor_instance

        from app.services.config_service import update_config as update_db_config

        await update_db_config(
            contribution_pseudonym="test-pseudonym-partial",
            enable_fingerprint_contributions=True,
            fpcalc_path=None,
        )

        resp = await client.post("/api/fingerprint/bootstrap/accept", json={"items": items})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["queued"] == 1
    assert data["failed"] == 1

    # Only 1 DB row should exist
    async with async_session() as session:
        from sqlmodel import select as sqlmodel_select

        from app.models.fingerprint import FingerprintContribution

        rows = (
            (
                await session.execute(
                    sqlmodel_select(FingerprintContribution).where(
                        FingerprintContribution.match_source == "bootstrap"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_accept_no_fpcalc_returns_400(client, tmp_path):
    """When fpcalc cannot be found, the endpoint returns 400 with a clear message."""
    items = [
        {"file": str(tmp_path / "ep1.mkv"), "tmdb_id": 99, "season": 1, "episode": 1},
    ]

    with (
        patch("app.api.routes.asyncio.to_thread") as mock_to_thread,
    ):
        from app.api.validation import ToolDetectionResult

        mock_to_thread.return_value = ToolDetectionResult(found=False, path=None, error="not found")

        from app.services.config_service import update_config as update_db_config

        await update_db_config(
            contribution_pseudonym="test-pseudonym-nofpcalc",
            enable_fingerprint_contributions=True,
            fpcalc_path=None,  # no explicit config path either
        )

        resp = await client.post("/api/fingerprint/bootstrap/accept", json={"items": items})

    assert resp.status_code == 400
    assert "fpcalc" in resp.json()["detail"].lower()

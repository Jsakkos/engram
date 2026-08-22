# External Player Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator open a ripped track in their native video player, in one click, from the manual review UI.

**Architecture:** Two read-only FastAPI endpoints under the existing `/jobs/{job_id}/titles/{title_id}/` route family. One streams the raw MKV with HTTP range support (Starlette's `FileResponse` supplies ranges for free); the other returns a one-entry `.m3u` playlist pointing at it. The review Inspector renders an "Open in player" link plus a "Copy stream URL" button. No transcoding, no ffmpeg, no subprocess. Browsers cannot decode MakeMKV output, so the file is handed to a native player rather than played in-page.

**Tech Stack:** FastAPI + Starlette 1.3.1 (`FileResponse` range support), SQLModel, pytest/httpx `ASGITransport` for backend tests; React 18 + TypeScript, existing `SvActionButton` synapse primitive, `sonner` toasts, vitest + React Testing Library for frontend tests.

**Design spec:** `docs/superpowers/specs/2026-08-22-review-external-player-design.md`

---

## File Structure

**Create:**
- `backend/tests/unit/test_media_endpoints.py`: all backend tests for both endpoints and the path guard.

**Modify:**
- `backend/app/core/security.py`: add `is_within_configured_roots`, a pure predicate beside the existing guards. No I/O, no config access, so it stays unit-testable and matches the "boolean predicate as barrier guard" convention documented in that module's docstring.
- `backend/tests/unit/test_security.py`: tests for the new predicate.
- `backend/app/api/routes.py`: add the two endpoints plus one shared path-resolution helper.
- `frontend/src/components/ReviewQueue/Inspector.tsx`: render the two controls plus the standing hint.
- `frontend/src/components/ReviewQueue/Inspector.test.tsx`: tests for the controls.

**Why the guard is split from the endpoints:** the containment rule is the security-critical piece and deserves direct unit tests that do not need an HTTP client, a database, or a seeded config. `security.py` already houses exactly this shape of helper.

---

## Task 1: Path containment guard

**Files:**
- Modify: `backend/app/core/security.py`
- Test: `backend/tests/unit/test_security.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_security.py`. Add `is_within_configured_roots` to the existing import block at the top of the file so it reads:

```python
from app.core.security import (
    executable_basename_allowed,
    is_allowed_image_url,
    is_within_configured_roots,
    sanitize_log_value,
)
```

Then append this class at the end of the file:

```python
class TestIsWithinConfiguredRoots:
    """Containment guard for media paths read out of the database."""

    def test_accepts_file_directly_in_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "title_01.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(root)])

    def test_accepts_file_in_nested_subdirectory(self, tmp_path):
        root = tmp_path / "library"
        nested = root / "Show" / "Season 01"
        nested.mkdir(parents=True)
        target = nested / "Show - S01E01.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(root)])

    def test_accepts_when_any_one_root_matches(self, tmp_path):
        staging = tmp_path / "staging"
        library = tmp_path / "library"
        staging.mkdir()
        library.mkdir()
        target = library / "movie.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(staging), str(library)])

    def test_rejects_file_outside_every_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        outside = tmp_path / "elsewhere.mkv"
        outside.touch()
        assert not is_within_configured_roots(outside, [str(root)])

    def test_rejects_parent_traversal(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        secret = tmp_path / "secret.mkv"
        secret.touch()
        traversal = root / ".." / "secret.mkv"
        assert not is_within_configured_roots(traversal, [str(root)])

    def test_rejects_sibling_root_with_shared_prefix(self, tmp_path):
        # "staging-old" must not pass a "staging" root via a bare string prefix
        # comparison. commonpath compares path components, not characters.
        root = tmp_path / "staging"
        sibling = tmp_path / "staging-old"
        root.mkdir()
        sibling.mkdir()
        target = sibling / "leak.mkv"
        target.touch()
        assert not is_within_configured_roots(target, [str(root)])

    def test_rejects_empty_root_list(self, tmp_path):
        target = tmp_path / "x.mkv"
        target.touch()
        assert not is_within_configured_roots(target, [])

    def test_ignores_unset_roots(self, tmp_path):
        # Unconfigured paths arrive as "" from AppConfig defaults. An empty
        # string must never be treated as a root, or it would resolve to the
        # process CWD and silently authorise it.
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "ok.mkv"
        target.touch()
        assert is_within_configured_roots(target, ["", str(root), ""])
        outside = tmp_path / "nope.mkv"
        outside.touch()
        assert not is_within_configured_roots(outside, ["", ""])

    def test_rejects_symlink_escaping_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        secret = tmp_path / "secret.mkv"
        secret.write_bytes(b"x")
        link = root / "innocent.mkv"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable (Windows without developer mode)")
        assert not is_within_configured_roots(link, [str(root)])
```

`tmp_path` is a pytest builtin fixture, no conftest change needed. `pytest` must be imported in this file for the `skip` call; add `import pytest` above the `from app.core.security import ...` block if it is not already there.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_security.py -k IsWithinConfiguredRoots -v
```

Expected: collection error, `ImportError: cannot import name 'is_within_configured_roots'`.

- [ ] **Step 3: Write the implementation**

Add to `backend/app/core/security.py`, after `executable_basename_allowed`:

```python
def is_within_configured_roots(path: os.PathLike[str] | str, roots: Sequence[str]) -> bool:
    """Return True if ``path`` resolves inside at least one of ``roots``.

    Barrier guard for the media endpoints: the file path comes out of the
    database (``DiscTitle.output_filename`` / ``organized_to``), so a corrupted
    or hand-edited row must not become an arbitrary file read.

    Both sides are fully resolved first, so ``..`` segments and symlinks are
    followed before the comparison. A symlink sitting inside a root but
    pointing outside it is therefore rejected, not followed into.
    Comparison uses ``os.path.commonpath``,
    which compares path *components*: a bare string prefix test would let
    ``/media/staging-old`` pass a ``/media/staging`` root.

    Empty roots are skipped. Unconfigured ``AppConfig`` paths default to ``""``,
    which would otherwise resolve to the process working directory and silently
    authorise it.
    """
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError):
        return False

    for root in roots:
        if not root:
            continue
        try:
            resolved_root = Path(root).resolve(strict=False)
            if os.path.commonpath([resolved, resolved_root]) == str(resolved_root):
                return True
        except (OSError, ValueError):
            # commonpath raises ValueError for paths on different Windows
            # drives, which simply means "not contained".
            continue
    return False
```

Add `from pathlib import Path` to the imports at the top of `security.py` (the module currently imports `os`, `re`, `ipaddress`, `Sequence`, and `urlparse`, but not `Path`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_security.py -v
```

Expected: all pass, including the pre-existing tests in that file.

- [ ] **Step 5: Lint**

```bash
cd backend && uv run ruff check app/core/security.py tests/unit/test_security.py && uv run ruff format app/core/security.py tests/unit/test_security.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/security.py backend/tests/unit/test_security.py
git commit -m "feat(security): add is_within_configured_roots path guard"
```

---

## Task 2: Media streaming endpoint

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/unit/test_media_endpoints.py` (create)

The route goes in the `/jobs/{job_id}/titles/{title_id}/` family. Place it immediately after `rematch_title` (currently ends around `routes.py:4170`) so the title-scoped routes stay together.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_media_endpoints.py`:

```python
"""Unit tests for the external-player media endpoints.

Covers GET /api/jobs/{job_id}/titles/{title_id}/media and .../playlist.m3u:
range handling, path resolution and its fallback, the containment guard, and
the origin gate.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.guards import require_localhost_or_lan
from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory

MEDIA_BYTES = b"MKVFAKEPAYLOAD" * 16  # 224 bytes


async def _seed_config(staging: str, movies: str, tv: str) -> AppConfig:
    async with _unit_session_factory() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key-1234567890",
            staging_path=staging,
            library_movies_path=movies,
            library_tv_path=tv,
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
            ffmpeg_path="/usr/bin/ffmpeg",
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


async def _seed_job_and_title(**title_kwargs) -> tuple[DiscJob, DiscTitle]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="D:",
            volume_label="TEST_DISC",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Test Show",
            detected_season=1,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        defaults = dict(
            job_id=job.id,
            title_index=1,
            duration_seconds=1320,
            file_size_bytes=len(MEDIA_BYTES),
            state=TitleState.REVIEW,
        )
        defaults.update(title_kwargs)
        title = DiscTitle(**defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


@pytest.fixture
def media_file(tmp_path):
    """A staging root containing one fake MKV. Returns (staging_root, file)."""
    staging = tmp_path / "staging"
    staging.mkdir()
    f = staging / "title_01.mkv"
    f.write_bytes(MEDIA_BYTES)
    return staging, f


@pytest.fixture
async def client(tmp_path):
    """Async client with the patched DB session and the origin gate disabled.

    The gate is exercised explicitly in TestOriginGate; every other test
    overrides it so it cannot mask an unrelated failure.
    """

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestMediaEndpoint:
    async def test_full_request_returns_whole_file(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES
        assert r.headers["accept-ranges"] == "bytes"

    async def test_range_request_returns_206_partial(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(
            f"/api/jobs/{job.id}/titles/{title.id}/media",
            headers={"Range": "bytes=0-9"},
        )

        assert r.status_code == 206
        assert r.content == MEDIA_BYTES[:10]
        assert r.headers["content-range"] == f"bytes 0-9/{len(MEDIA_BYTES)}"

    async def test_unsatisfiable_range_returns_416(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(
            f"/api/jobs/{job.id}/titles/{title.id}/media",
            headers={"Range": "bytes=99999-"},
        )

        assert r.status_code == 416

    async def test_falls_back_to_organized_to(self, client, tmp_path):
        library = tmp_path / "tv"
        library.mkdir()
        organized = library / "Show - S01E01.mkv"
        organized.write_bytes(MEDIA_BYTES)
        await _seed_config(str(tmp_path / "staging"), str(tmp_path / "movies"), str(library))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=str(organized))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES

    async def test_prefers_output_filename_over_organized_to(self, client, tmp_path, media_file):
        staging, f = media_file
        library = tmp_path / "tv"
        library.mkdir()
        organized = library / "other.mkv"
        organized.write_bytes(b"WRONGFILE")
        await _seed_config(str(staging), str(tmp_path / "movies"), str(library))
        job, title = await _seed_job_and_title(
            output_filename=str(f), organized_to=str(organized)
        )

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES

    async def test_no_recorded_path_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=None)

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 404
        assert "not been ripped" in r.json()["detail"]

    async def test_missing_file_on_disk_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(staging / "gone.mkv"))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 404
        assert "no longer on disk" in r.json()["detail"]

    async def test_path_outside_configured_roots_returns_403(self, client, tmp_path, media_file):
        staging, _ = media_file
        outside = tmp_path / "elsewhere.mkv"
        outside.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(outside))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_traversal_path_returns_403(self, client, tmp_path, media_file):
        staging, _ = media_file
        secret = tmp_path / "secret.mkv"
        secret.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(
            output_filename=str(staging / ".." / "secret.mkv")
        )

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_title_belonging_to_another_job_returns_404(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        _job_a, title_a = await _seed_job_and_title(output_filename=str(f))
        job_b, _title_b = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job_b.id}/titles/{title_a.id}/media")

        assert r.status_code == 404

    async def test_unknown_job_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))

        r = await client.get("/api/jobs/99999/titles/1/media")

        assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_media_endpoints.py -v
```

Expected: every test fails with `assert 404 == 200` (or similar), because FastAPI has no route registered at that path yet.

- [ ] **Step 3: Write the implementation**

Add to `backend/app/api/routes.py`, immediately after the `rematch_title` function. First add the shared resolver:

```python
async def _resolve_title_media_path(job: DiscJob, title_id: int, session: AsyncSession) -> Path:
    """Resolve the on-disk media file for a title, or raise the right HTTPException.

    The path is read from the database and never accepted from the client:
    ``output_filename`` (set at rip time) first, then ``organized_to`` for a
    track whose staging copy has already been moved into the library.

    Distinguishes three failure modes deliberately, because they need different
    user-facing fixes: nothing recorded (not ripped yet), recorded but gone
    (cleaned up or moved externally), and out of bounds (bad data or
    misconfiguration, which is a 403 rather than a 404 so it is not mistaken
    for ordinary absence).
    """
    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    recorded = title.output_filename or title.organized_to
    if not recorded:
        raise HTTPException(
            status_code=404, detail="This track has not been ripped yet, so there is nothing to play"
        )

    config = await get_config()
    roots = [config.staging_path, config.library_tv_path, config.library_movies_path]
    if not is_within_configured_roots(recorded, roots):
        logger.warning(
            "Refusing to serve media outside the configured roots: %s",
            sanitize_log_value(recorded),
        )
        raise HTTPException(
            status_code=403,
            detail="This file is outside the configured staging and library folders",
        )

    path = Path(recorded)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file for this track is no longer on disk")
    return path


@router.get(
    "/jobs/{job_id}/titles/{title_id}/media",
    dependencies=[Depends(require_localhost_or_lan)],
)
async def stream_title_media(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Stream a ripped track for playback in an external player.

    Serves the raw MKV untouched. Browsers cannot decode it (MKV container,
    MPEG-2/HEVC video, DTS/TrueHD/AC3 audio), so this is consumed by VLC/MPV
    and friends via the companion playlist endpoint, not by a <video> element.
    Starlette's FileResponse implements Range/206/416, so seeking works without
    any handling here.
    """
    path = await _resolve_title_media_path(job, title_id, session)
    return FileResponse(path, media_type="video/x-matroska", filename=path.name)
```

Two import additions at the top of `routes.py`:

```python
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
```

and add `is_within_configured_roots` to the existing `app.core.security` import so it reads:

```python
from app.core.security import (
    is_allowed_image_url,
    is_within_configured_roots,
    sanitize_log_value,
)
```

`Path`, `DiscTitle`, `logger` (defined at `routes.py:55`), `require_localhost_or_lan`, and `AsyncSession` are all already available at module scope in `routes.py`.

`get_config` is **not**. Every existing caller imports it inside the function body (see `routes.py:793` and `routes.py:977`) to avoid an import cycle, so `_resolve_title_media_path` must do the same. Add this as the first line of the function body, above the `session.get` call:

```python
    from app.services.config_service import get_config
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_media_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_media_endpoints.py
git commit -m "feat(api): stream ripped tracks for external player playback"
```

---

## Task 3: Playlist endpoint

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/unit/test_media_endpoints.py:end`

The critical behaviour is that the URL inside the playlist is built from the request's `Host` header. A hardcoded `localhost` works on the backend machine and fails everywhere else, and passes local testing, so it is tested explicitly.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_media_endpoints.py`:

```python
class TestPlaylistEndpoint:
    async def test_returns_m3u_with_absolute_media_url(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 200
        body = r.text
        assert body.startswith("#EXTM3U")
        assert f"/api/jobs/{job.id}/titles/{title.id}/media" in body
        assert "http://test/" in body

    async def test_url_uses_request_host_not_localhost(self, tmp_path, media_file):
        """A playlist opened on another machine must not point at localhost.

        This is the failure that passes local testing and breaks every remote
        user, so it is asserted against an explicit non-loopback Host.
        """
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[require_localhost_or_lan] = lambda: None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://192.168.1.50:8000") as ac:
            r = await ac.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")
        app.dependency_overrides.clear()

        assert r.status_code == 200
        assert "192.168.1.50:8000" in r.text
        assert "localhost" not in r.text
        assert "127.0.0.1" not in r.text

    async def test_served_as_attachment_with_playlist_type(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert "audio/x-mpegurl" in r.headers["content-type"]
        disposition = r.headers["content-disposition"]
        assert disposition.startswith("attachment")
        assert f"engram-job-{job.id}-title-{title.title_index}.m3u" in disposition

    async def test_playlist_404s_when_track_not_ripped(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=None)

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 404

    async def test_playlist_403s_for_out_of_bounds_path(self, client, tmp_path, media_file):
        staging, _ = media_file
        outside = tmp_path / "elsewhere.mkv"
        outside.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(outside))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 403


class TestOriginGate:
    """Both endpoints sit behind require_localhost_or_lan.

    ASGITransport defaults the peer address to loopback, so the 403 branch is
    only reachable by passing an explicit non-loopback client= to the transport.
    """

    @pytest.fixture
    async def lan_client(self):
        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        transport = ASGITransport(app=app, client=("192.168.1.50", 51234))
        async with AsyncClient(transport=transport, base_url="http://192.168.1.50:8000") as ac:
            yield ac
        app.dependency_overrides.clear()

    async def test_lan_peer_blocked_when_lan_access_disabled(
        self, lan_client, tmp_path, media_file
    ):
        staging, f = media_file
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    makemkv_path="/usr/bin/makemkvcon",
                    staging_path=str(staging),
                    library_movies_path=str(tmp_path / "movies"),
                    library_tv_path=str(tmp_path / "tv"),
                    allow_lan_access=False,
                )
            )
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await lan_client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_lan_peer_allowed_when_lan_access_enabled(
        self, lan_client, tmp_path, media_file
    ):
        staging, f = media_file
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    makemkv_path="/usr/bin/makemkvcon",
                    staging_path=str(staging),
                    library_movies_path=str(tmp_path / "movies"),
                    library_tv_path=str(tmp_path / "tv"),
                    allow_lan_access=True,
                )
            )
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await lan_client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_media_endpoints.py -k "Playlist or OriginGate" -v
```

Expected: the `TestPlaylistEndpoint` tests fail with 404 (no route). The `TestOriginGate` tests should already pass, since Task 2 attached the gate to the media route; if `test_lan_peer_blocked_when_lan_access_disabled` fails with 200, the gate was not wired and Task 2 needs fixing before continuing.

- [ ] **Step 3: Write the implementation**

Add to `backend/app/api/routes.py`, immediately after `stream_title_media`:

```python
@router.get(
    "/jobs/{job_id}/titles/{title_id}/playlist.m3u",
    dependencies=[Depends(require_localhost_or_lan)],
)
async def title_playlist(
    request: Request,
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return a one-entry .m3u pointing at this track's media URL.

    The browser downloads this and the OS hands it to whatever owns .m3u,
    which is how the track reaches a native player.

    The media URL is built from the *request* URL, so it carries the host the
    dashboard was actually reached on. Deriving it from settings.host instead
    would emit ``localhost`` for every remote user: correct on the backend
    machine, broken everywhere else, and invisible in local testing.
    """
    # Resolve first so a missing or out-of-bounds file fails here, rather than
    # handing the user a playlist that errors inside their player.
    await _resolve_title_media_path(job, title_id, session)

    title = await session.get(DiscTitle, title_id)
    media_url = str(request.url_for("stream_title_media", job_id=job.id, title_id=title_id))
    label = f"{job.detected_title or job.volume_label} - Title {title.title_index}"
    body = f"#EXTM3U\n#EXTINF:-1,{label}\n{media_url}\n"
    filename = f"engram-job-{job.id}-title-{title.title_index}.m3u"

    return Response(
        content=body,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

Add `Response` to the FastAPI import line at the top of `routes.py`:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
```

`request.url_for(...)` resolves by the route *function* name, so `stream_title_media` must not be renamed without updating this call.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_media_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```bash
cd backend && uv run ruff check app/api/routes.py tests/unit/test_media_endpoints.py && uv run ruff format app/api/routes.py tests/unit/test_media_endpoints.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_media_endpoints.py
git commit -m "feat(api): add .m3u playlist endpoint for external player handoff"
```

---

## Task 4: Review UI controls

**Files:**
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx`
- Test: `frontend/src/components/ReviewQueue/Inspector.test.tsx`

The controls go in the Inspector header, directly under the duration/size metadata line at `Inspector.tsx:126-129`. That is where the operator is already reading the track's physical properties when the "what actually is this?" question comes up.

Both controls ship together. The copy button is not a fallback: a failed `.m3u` handoff produces no signal the page can detect, so the copy path is the one action that cannot fail silently. The hint is always visible for the same reason.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/ReviewQueue/Inspector.test.tsx`:

```tsx
describe('Inspector — external player controls', () => {
    it('renders both controls when the track has a ripped file', () => {
        renderInspector({
            title: makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' }),
        });

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /copy stream url/i })).toBeInTheDocument();
    });

    it('renders the controls for an organized track with no staging copy', () => {
        renderInspector({
            title: makeTitle({
                output_filename: null,
                organized_to: 'C:\\tv\\Show\\Season 01\\Show - S01E01.mkv',
            }),
        });

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
    });

    it('hides the controls when the track has no file at all', () => {
        renderInspector({
            title: makeTitle({ output_filename: null, organized_to: null }),
        });

        expect(screen.queryByRole('link', { name: /open in player/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /copy stream url/i })).not.toBeInTheDocument();
    });

    it('points the player link at the playlist endpoint for this job and title', () => {
        renderInspector({
            title: makeTitle({
                id: 42,
                job_id: 7,
                output_filename: 'C:\\staging\\title_01.mkv',
            }),
        });

        const link = screen.getByRole('link', { name: /open in player/i });
        expect(link).toHaveAttribute('href', '/api/jobs/7/titles/42/playlist.m3u');
    });

    it('copies an absolute media URL to the clipboard', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });

        renderInspector({
            title: makeTitle({
                id: 42,
                job_id: 7,
                output_filename: 'C:\\staging\\title_01.mkv',
            }),
        });

        await userEvent.click(screen.getByRole('button', { name: /copy stream url/i }));

        expect(writeText).toHaveBeenCalledWith(
            `${window.location.origin}/api/jobs/7/titles/42/media`,
        );
    });

    it('shows the standing hint whenever the controls render', () => {
        // The hint is permanent, not error-triggered: a failed .m3u handoff
        // gives the page no signal to react to.
        renderInspector({
            title: makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' }),
        });

        expect(screen.getByText(/open network stream/i)).toBeInTheDocument();
    });
});
```

Add `userEvent` to the imports at the top of the file:

```tsx
import userEvent from '@testing-library/user-event';
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npm run test:unit -- Inspector
```

Expected: the six new tests fail with "Unable to find role link/button". Pre-existing Inspector tests still pass.

- [ ] **Step 3: Write the implementation**

In `frontend/src/components/ReviewQueue/Inspector.tsx`, add the toast import at the top:

```tsx
import { toast } from 'sonner';
```

Add this helper above the `Inspector` function, next to the existing `pct` helper:

```tsx
/**
 * Ripped file for this track, if one exists on disk.
 *
 * `output_filename` is the staging copy written at rip time; `organized_to`
 * is where it landed after being moved into the library. A track parked in a
 * pre-rip review has neither, and gets no playback controls.
 */
function hasPlayableFile(title: DiscTitle): boolean {
    return !!(title.output_filename || title.organized_to);
}
```

Inside the `Inspector` component body, above the `return`, add:

```tsx
const playable = hasPlayableFile(title);
const playlistHref = `/api/jobs/${title.job_id}/titles/${title.id}/playlist.m3u`;
const mediaUrl = `${window.location.origin}/api/jobs/${title.job_id}/titles/${title.id}/media`;

const copyStreamUrl = async () => {
    try {
        await navigator.clipboard.writeText(mediaUrl);
        toast.success('Stream URL copied');
    } catch {
        // Clipboard access is denied in some browsers over plain HTTP. Show the
        // URL so the operator can still select it by hand rather than getting
        // a silently dead button.
        toast.error(`Could not copy. URL: ${mediaUrl}`);
    }
};
```

Then, in the header block, immediately after the metadata `<div>` that ends with `{title.chapter_count} chapters` (around line 129), insert:

```tsx
{playable && (
    <div style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <SvActionButton
                tone="cyan"
                size="sm"
                href={playlistHref}
                ariaLabel="Open in player"
                title="Open this track in your system's default video player"
            >
                Open in player
            </SvActionButton>
            <SvActionButton
                tone="neutral"
                size="sm"
                onClick={copyStreamUrl}
                ariaLabel="Copy stream URL"
                title="Copy the stream URL for your player's Open Network Stream"
            >
                Copy stream URL
            </SvActionButton>
        </div>
        <div style={{ ...monoFaint, marginTop: 6, fontSize: 10 }}>
            Opens in your default player. Nothing happened? Copy the URL and use
            your player's "Open Network Stream".
        </div>
    </div>
)}
```

`SvActionButton` is already imported in this file, and it renders an `<a>` when `href` is set, which is what makes the first control resolve as `role="link"` in the tests.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npm run test:unit -- Inspector
```

Expected: all pass.

- [ ] **Step 5: Typecheck and lint**

```bash
cd frontend && npm run build && npm run lint
```

Expected: build succeeds, no lint errors. `npm run build` runs the TypeScript check.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ReviewQueue/Inspector.tsx frontend/src/components/ReviewQueue/Inspector.test.tsx
git commit -m "feat(review): add external player controls to the title inspector"
```

---

## Task 5: Full verification and changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full backend unit tier**

```bash
cd backend && uv run pytest tests/unit/ -q
```

Expected: all pass. This tier takes several minutes; do not run it inside a subagent with a short timeout.

- [ ] **Step 2: Run the frontend unit tier and build**

```bash
cd frontend && npm run test:unit && npm run build
```

Expected: all pass, build succeeds.

- [ ] **Step 3: Add the changelog entry**

Add under the `## [Unreleased]` section in `CHANGELOG.md`, creating an `### Added` subsection if one is not already there:

```markdown
### Added

- Play a track in your own video player straight from manual review. Each track in the review inspector now has "Open in player", which hands the file to VLC/MPV/IINA via a playlist, and "Copy stream URL" for your player's Open Network Stream. Files stay untouched: nothing is re-encoded, so you get every audio and subtitle track at full quality. Reachable from another machine only when LAN access is enabled.
```

Do not add a PR number yet; add it when the PR exists.

- [ ] **Step 4: Verify no em dashes**

```bash
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' CHANGELOG.md
```

Expected: `0`. Use this byte alternation rather than a character class holding literal dash characters, which false-positives on other multi-byte characters in Git Bash.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for external player handoff"
```

---

## Manual verification

Automated tests cannot observe what a native application does after the browser hands off, which is the whole point of the feature. Run this once before opening the PR.

- [ ] Start the backend and frontend, then drive a job to `REVIEW_NEEDED` with at least one ripped track (see the simulation endpoints in `CLAUDE.md`, or use a real disc).
- [ ] Open the review page, select a track, and confirm both controls and the hint appear.
- [ ] Click "Open in player" and confirm the track opens and plays in VLC with working seek and audio.
- [ ] Click "Copy stream URL", then paste it into VLC via Media, Open Network Stream, and confirm it plays.
- [ ] Seek to the middle of the file and confirm playback resumes there, which proves range requests are working end to end.
- [ ] Select a track in a pre-rip review job and confirm no controls appear.
- [ ] Stop this session's servers before opening the PR, per the CLAUDE.md rule about orphaned uvicorn and makemkvcon processes.

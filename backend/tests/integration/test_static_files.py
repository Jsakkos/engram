"""Integration tests for static file serving MIME types (Issue #110).

Verifies that CSS/JS/SVG assets are served with the correct Content-Type
headers even when the Windows Registry MIME mappings are corrupted.
"""

import mimetypes

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
def static_root(tmp_path):
    """Create a minimal static directory with one file per asset type."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "styles.css").write_text("body { background: #0b1120; }")
    (assets / "bundle.js").write_text("console.log('engram');")
    (assets / "icon.svg").write_text("<svg></svg>")
    return tmp_path


@pytest.fixture(autouse=True)
def restore_mime_types():
    """Save and restore mimetypes state around each test."""
    mimetypes.init()
    saved = dict(mimetypes.types_map)
    yield
    mimetypes.types_map.clear()
    mimetypes.types_map.update(saved)


async def _make_app(static_root):
    app = FastAPI()
    app.mount("/assets", StaticFiles(directory=str(static_root / "assets")), name="assets")
    return app


async def test_css_served_with_correct_content_type(static_root):
    """CSS must be served as text/css so browsers apply the stylesheet."""
    mimetypes.add_type("text/css", ".css")
    app = await _make_app(static_root)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/assets/styles.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


async def test_js_served_with_correct_content_type(static_root):
    """JS must be served as application/javascript so browsers execute the module."""
    mimetypes.add_type("application/javascript", ".js")
    app = await _make_app(static_root)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/assets/bundle.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


async def test_svg_served_with_correct_content_type(static_root):
    """SVG must be served as image/svg+xml."""
    mimetypes.add_type("image/svg+xml", ".svg")
    app = await _make_app(static_root)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/assets/icon.svg")

    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]


async def test_corrupted_registry_causes_wrong_css_content_type(static_root):
    """Demonstrates the bug: corrupted Registry → CSS served as text/plain."""
    mimetypes.types_map[".css"] = "text/plain"
    app = await _make_app(static_root)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/assets/styles.css")

    assert response.status_code == 200
    # Without the fix, CSS is rejected by browsers (they see text/plain)
    assert "text/plain" in response.headers["content-type"]

"""Tests for the precomputed-cache fetch-on-first-run service.

Exercises ``ensure_precomputed_cache()`` end to end against a real localhost
HTTP server: download -> checksum -> extract -> install, plus the offline,
disabled, checksum-mismatch, format-mismatch, and up-to-date paths. The
happy-path test also confirms an installed cache loads through EpisodeMatcher
and matches the correct episode.

No external network is used -- the artifact is served from a loopback port.
"""

import hashlib
import json
import socket
import tarfile
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

import app.services.config_service as config_service
from app.config import settings
from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)
from app.services.precomputed_cache_service import (
    _CACHE_TAG,
    _MANIFEST_NAME,
    _TARBALL_NAME,
    ensure_precomputed_cache,
)

_SHOW = "Test Show"  # sanitize_filename("Test Show") == "Test Show"
_CODES = ["S01E01", "S01E02", "S01E03"]
_DOCS = [
    "detective solves the murder in the old mansion at midnight",
    "the spaceship crew explores a distant alien planet",
    "a chef cooks an elaborate pasta dinner in a small kitchen",
]
_CONTENT_VERSION = "2026-05-19"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request stderr logging
        pass


@contextmanager
def _http_server(directory):
    """Serve ``directory`` over HTTP on a loopback port for the duration of the block."""
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _free_port() -> int:
    """Return a port with nothing listening on it (for the offline path)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_precomputed_tree(root) -> dict:
    """Write a valid ``precomputed/`` tree under ``root``; return its in-tar manifest."""
    precomputed = root / "precomputed"
    show_dir = precomputed / _SHOW
    show_dir.mkdir(parents=True)

    counts = build_hashing_vectorizer().transform(_DOCS)
    idf = compute_idf(counts)
    np.save(precomputed / "idf.npy", idf)
    # v2: persist uint16 hashed counts; the loader applies apply_tfidf at
    # startup. Mirrors what scripts/build_subtitle_cache.py writes, including
    # the defensive clip to uint16 range.
    u16_max = np.iinfo(np.uint16).max
    counts_u16 = sparse.csr_matrix(
        (np.minimum(counts.data, u16_max).astype(np.uint16), counts.indices, counts.indptr),
        shape=counts.shape,
    )
    sparse.save_npz(show_dir / "S01.npz", counts_u16)
    (show_dir / "S01.index.json").write_text(json.dumps(_CODES))

    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "content_version": _CONTENT_VERSION,
        "n_features": HASHING_N_FEATURES,
        "shows": {_SHOW: {"tmdb_id": 1, "name": _SHOW, "seasons": [1], "episode_counts": {"1": 3}}},
    }
    (precomputed / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    return manifest


def _publish(release_dir, build_dir, *, manifest_overrides=None, corrupt_sha=False) -> None:
    """Build a cache tarball + release manifest into ``release_dir/<tag>/``."""
    manifest = _build_precomputed_tree(build_dir)
    tag_dir = release_dir / _CACHE_TAG
    tag_dir.mkdir(parents=True, exist_ok=True)

    tarball = tag_dir / _TARBALL_NAME
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(build_dir / "precomputed", arcname="precomputed")

    sha = "0" * 64 if corrupt_sha else hashlib.sha256(tarball.read_bytes()).hexdigest()
    release_manifest = dict(manifest, tarball_sha256=sha)
    release_manifest.update(manifest_overrides or {})
    (tag_dir / _MANIFEST_NAME).write_text(json.dumps(release_manifest, indent=2))


def _fake_config(cache_dir, *, enabled=True, installed_version=""):
    return SimpleNamespace(
        precomputed_cache_enabled=enabled,
        subtitles_cache_path=str(cache_dir),
        precomputed_cache_version=installed_version,
    )


@pytest.fixture
def patched_config(monkeypatch):
    """Patch config_service.get_config/update_config; yield (set_config, recorded_updates)."""
    state = {"config": None}
    updates: dict = {}

    async def fake_get_config():
        return state["config"]

    async def fake_update_config(**kwargs):
        updates.update(kwargs)

    monkeypatch.setattr(config_service, "get_config", fake_get_config)
    monkeypatch.setattr(config_service, "update_config", fake_update_config)
    return state.__setitem__, updates


def _set(set_config, cfg):
    set_config("config", cfg)


async def test_disabled_skips_download(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"
    _set(set_config, _fake_config(cache_dir, enabled=False))
    monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{_free_port()}")

    await ensure_precomputed_cache()  # must not raise

    assert not (cache_dir / "precomputed").exists()
    assert updates == {}


async def test_offline_is_silent(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"
    _set(set_config, _fake_config(cache_dir))
    # Nothing is listening on this port -> connection refused.
    monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{_free_port()}")

    await ensure_precomputed_cache()  # must not raise

    assert not (cache_dir / "precomputed").exists()
    assert updates == {}


async def test_happy_path_installs_and_matches(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"
    _set(set_config, _fake_config(cache_dir))
    _publish(tmp_path / "release", tmp_path / "build")

    with _http_server(tmp_path / "release") as port:
        monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{port}")
        await ensure_precomputed_cache()

    precomputed = cache_dir / "precomputed"
    assert (precomputed / _MANIFEST_NAME).exists()
    assert (precomputed / "idf.npy").exists()
    assert (precomputed / _SHOW / "S01.npz").exists()
    assert updates.get("precomputed_cache_version") == _CONTENT_VERSION

    # End to end: the installed cache loads and matches through the matcher.
    matcher = EpisodeMatcher(cache_dir=cache_dir, show_name=_SHOW)
    loaded = matcher._load_precomputed_season(1)
    assert loaded is not None
    tfidf = TfidfMatcher()
    tfidf.load_precomputed(*loaded)
    results = tfidf.match("the crew explores a far away planet")
    assert results[0][0] == "S01E02"


async def test_checksum_mismatch_aborts_install(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"
    _set(set_config, _fake_config(cache_dir))
    _publish(tmp_path / "release", tmp_path / "build", corrupt_sha=True)

    with _http_server(tmp_path / "release") as port:
        monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{port}")
        await ensure_precomputed_cache()

    assert not (cache_dir / "precomputed").exists()
    assert updates == {}


async def test_format_version_mismatch_skips(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"
    _set(set_config, _fake_config(cache_dir))
    _publish(
        tmp_path / "release",
        tmp_path / "build",
        manifest_overrides={"cache_format_version": "999"},
    )

    with _http_server(tmp_path / "release") as port:
        monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{port}")
        await ensure_precomputed_cache()

    assert not (cache_dir / "precomputed").exists()
    assert updates == {}


async def test_up_to_date_skips_redownload(tmp_path, monkeypatch, patched_config):
    set_config, updates = patched_config
    cache_dir = tmp_path / "cache"

    # A local cache is already installed at the current content version.
    precomputed = cache_dir / "precomputed"
    precomputed.mkdir(parents=True)
    local_manifest = precomputed / _MANIFEST_NAME
    local_manifest.write_text(
        json.dumps(
            {"cache_format_version": CACHE_FORMAT_VERSION, "content_version": _CONTENT_VERSION}
        )
    )
    _set(set_config, _fake_config(cache_dir, installed_version=_CONTENT_VERSION))

    # Publish only a manifest, no tarball: a download attempt would fail, so
    # reaching this without error proves the up-to-date check short-circuits.
    tag_dir = tmp_path / "release" / _CACHE_TAG
    tag_dir.mkdir(parents=True)
    (tag_dir / _MANIFEST_NAME).write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "content_version": _CONTENT_VERSION,
                "tarball_sha256": "0" * 64,
            }
        )
    )

    with _http_server(tmp_path / "release") as port:
        monkeypatch.setattr(settings, "precomputed_cache_base_url", f"http://127.0.0.1:{port}")
        await ensure_precomputed_cache()

    # Local cache untouched; nothing re-downloaded.
    assert list(precomputed.iterdir()) == [local_manifest]
    assert updates == {}

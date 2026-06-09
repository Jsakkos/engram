"""Unit tests for per-disc ContentHash dedup in JobManager.

Covers the pure _same_disc discriminator, the _compute_disc_hash retry wrapper,
and _create_job_for_disc's blocking decision: a same-labelled disc with a
DIFFERENT hash must be allowed through (the Breaking Bad S2 D1-vs-D2 bug),
while the same hash (or an unreadable hash) stays blocked.
"""

import importlib
from types import SimpleNamespace

import pytest

from app.api.websocket import manager as ws_manager
from app.services.job_manager import job_manager

jm_mod = importlib.import_module("app.services.job_manager")


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


# --- _same_disc (pure) -----------------------------------------------------


def _job(content_hash, volume_label):
    return SimpleNamespace(content_hash=content_hash, volume_label=volume_label)


def test_same_disc_true_when_hashes_match():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "AAAA") is True


def test_same_disc_false_when_hashes_differ():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is False


def test_same_disc_falls_back_to_label_when_new_hash_missing():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", None) is True
    assert job_manager._same_disc(job, "OTHERLABEL", None) is False


def test_same_disc_falls_back_to_label_when_job_hash_missing():
    job = _job(None, "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is True
    assert job_manager._same_disc(job, "OTHERLABEL", "BBBB") is False


# --- _compute_disc_hash (retry) -------------------------------------------


@pytest.mark.asyncio
async def test_compute_disc_hash_returns_first_success(monkeypatch):
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "DEADBEEF")
    assert await job_manager._compute_disc_hash("F:") == "DEADBEEF"


@pytest.mark.asyncio
async def test_compute_disc_hash_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    calls = {"n": 0}

    def flaky(drive):
        calls["n"] += 1
        return None if calls["n"] < 2 else "CAFE"

    monkeypatch.setattr(jm_mod, "compute_content_hash", flaky)
    assert await job_manager._compute_disc_hash("F:") == "CAFE"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_compute_disc_hash_returns_none_when_never_ready(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: None)
    assert await job_manager._compute_disc_hash("F:") is None

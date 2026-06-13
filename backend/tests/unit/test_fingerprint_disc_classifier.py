"""Unit tests for the network disc-identification read path.

Covers the hash hex->bytes->b64url-nopad encoding, best-effort failure
handling (HTTP error / malformed JSON / miss all return None, never raise),
the default-base-URL fallback, and the network-title -> DiscDbTitleMapping
verification helper (+-2s / +-1% against a scanned title; index from the
scanned title; "extra"/"discarded" skipped; source="network_disc").

httpx is mocked (monkeypatch httpx.AsyncClient.get) — no network is hit.
"""

import base64

import httpx
import pytest

from app.core.fingerprint_disc_classifier import (
    NetworkDiscSignal,
    NetworkDiscTitle,
    identify_disc_via_network,
    network_titles_to_mappings,
)
from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL


def _hit_payload() -> dict:
    return {
        "disc": {
            "tmdb_id": 1396,
            "content_type": "tv",
            "season": 1,
            "tier": "canonical",
            "unique_contributors": 7,
            "mean_confidence": 0.92,
            "titles": [
                {
                    "title_index": 0,
                    "duration_seconds": 2820,
                    "size_bytes": 5_000_000_000,
                    "assignment": "episode",
                    "season": 1,
                    "episode": 1,
                    "match_confidence": 0.95,
                    "match_source": "network_disc",
                },
                {
                    "title_index": 1,
                    "duration_seconds": 600,
                    "size_bytes": 900_000_000,
                    "assignment": "extra",
                    "season": None,
                    "episode": None,
                    "match_confidence": 0.4,
                    "match_source": "network_disc",
                },
            ],
        }
    }


class _FakeResp:
    def __init__(self, payload, *, raise_exc: Exception | None = None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        return None

    def json(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._payload


def _patch_get(monkeypatch, *, payload=None, resp=None, captured=None):
    async def fake_get(self, url, params=None):
        if captured is not None:
            captured["url"] = url
            captured["params"] = params
            captured["base_url"] = str(self.base_url) if self.base_url else ""
        if resp is not None:
            return resp
        return _FakeResp(payload)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)


# ---------------------------------------------------------------------------
# Hash encoding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hash_hex_to_b64url_nopad_encoding(monkeypatch):
    """Uppercase-hex MD5 -> raw bytes -> base64url WITHOUT padding."""
    content_hash = "0123456789ABCDEF0123456789ABCDEF"
    raw = bytes.fromhex(content_hash)
    expected = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    captured: dict = {}
    _patch_get(monkeypatch, payload={"disc": None}, captured=captured)

    await identify_disc_via_network(content_hash, "https://server")

    assert captured["params"]["hash"] == expected
    # No padding survived.
    assert "=" not in captured["params"]["hash"]
    # Round-trips back to the original bytes (adding padding back).
    padded = expected + "=" * (-len(expected) % 4)
    assert base64.urlsafe_b64decode(padded) == raw


@pytest.mark.asyncio
async def test_malformed_hex_returns_none_without_raising(monkeypatch):
    """An odd-length / garbage content_hash is swallowed, not raised."""
    captured: dict = {}
    _patch_get(monkeypatch, payload={"disc": None}, captured=captured)

    # Odd length -> bytes.fromhex raises ValueError -> handled.
    out = await identify_disc_via_network("ABC", "https://server")
    assert out is None
    # Never even reached the GET.
    assert captured == {}


# ---------------------------------------------------------------------------
# Best-effort parsing / failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_miss_returns_none(monkeypatch):
    _patch_get(monkeypatch, payload={"disc": None})
    out = await identify_disc_via_network("00" * 16, "https://server")
    assert out is None


@pytest.mark.asyncio
async def test_missing_disc_key_returns_none(monkeypatch):
    _patch_get(monkeypatch, payload={})
    out = await identify_disc_via_network("00" * 16, "https://server")
    assert out is None


@pytest.mark.asyncio
async def test_hit_parses_signal_and_titles(monkeypatch):
    _patch_get(monkeypatch, payload=_hit_payload())
    sig = await identify_disc_via_network("00" * 16, "https://server")
    assert isinstance(sig, NetworkDiscSignal)
    assert sig.tmdb_id == 1396
    assert sig.content_type == "tv"
    assert sig.season == 1
    assert sig.tier == "canonical"
    assert sig.unique_contributors == 7
    assert sig.mean_confidence == pytest.approx(0.92)
    assert len(sig.titles) == 2
    t0 = sig.titles[0]
    assert isinstance(t0, NetworkDiscTitle)
    assert t0.title_index == 0
    assert t0.duration_seconds == 2820
    assert t0.size_bytes == 5_000_000_000
    assert t0.assignment == "episode"
    assert t0.season == 1 and t0.episode == 1


@pytest.mark.asyncio
async def test_http_error_returns_none(monkeypatch):
    async def fake_get(self, url, params=None):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    out = await identify_disc_via_network("00" * 16, "https://server")
    assert out is None


@pytest.mark.asyncio
async def test_malformed_json_returns_none(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(None, raise_exc=ValueError("bad json")))
    out = await identify_disc_via_network("00" * 16, "https://server")
    assert out is None


@pytest.mark.asyncio
async def test_garbage_disc_fields_return_none(monkeypatch):
    # tmdb_id missing -> treated as a miss.
    _patch_get(monkeypatch, payload={"disc": {"content_type": "tv"}})
    out = await identify_disc_via_network("00" * 16, "https://server")
    assert out is None


@pytest.mark.asyncio
async def test_truthy_non_iterable_titles_does_not_raise(monkeypatch):
    """A valid identity whose ``titles`` is a truthy non-iterable (e.g. ``5``)
    must NOT raise — the disc stays usable with an empty title list."""
    payload = {
        "disc": {
            "tmdb_id": 1396,
            "content_type": "tv",
            "tier": "canonical",
            "titles": 5,  # truthy non-iterable -> `for t in 5` would TypeError
        }
    }
    _patch_get(monkeypatch, payload=payload)
    sig = await identify_disc_via_network("00" * 16, "https://server")
    # Belt-and-suspenders: signal survives, titles coerced to empty.
    assert sig is not None
    assert sig.tmdb_id == 1396
    assert sig.titles == []


@pytest.mark.asyncio
async def test_truthy_non_numeric_aggregate_fields_do_not_raise(monkeypatch):
    """Truthy-but-non-numeric ``unique_contributors`` / ``mean_confidence``
    (e.g. ``"x"`` / ``{}``) must be coerced to 0 / 0.0, never raise."""
    payload = {
        "disc": {
            "tmdb_id": 1396,
            "content_type": "tv",
            "tier": "canonical",
            "unique_contributors": "x",  # truthy non-numeric -> int() would raise
            "mean_confidence": {},  # truthy non-numeric -> float() would raise
            "titles": [],
        }
    }
    _patch_get(monkeypatch, payload=payload)
    sig = await identify_disc_via_network("00" * 16, "https://server")
    assert sig is not None
    assert sig.unique_contributors == 0
    assert sig.mean_confidence == 0.0


@pytest.mark.asyncio
async def test_wholly_bizarre_body_never_raises(monkeypatch):
    """The outer guard is the real guarantee: no server body, however bizarre,
    propagates an exception out of ``identify_disc_via_network``."""
    for body in (
        {"disc": {"tmdb_id": 1396, "content_type": "tv", "tier": "x", "titles": [1, 2, 3]}},
        {"disc": {"tmdb_id": object(), "content_type": "tv", "tier": "x"}},
        {"disc": 12345},
        [1, 2, 3],
        "not-a-dict",
        42,
    ):
        # Must return without raising regardless of body shape.
        out = await _identify_with_body(monkeypatch, body)
        assert out is None or isinstance(out, NetworkDiscSignal)


async def _identify_with_body(monkeypatch, body):
    _patch_get(monkeypatch, payload=body)
    return await identify_disc_via_network("00" * 16, "https://server")


@pytest.mark.asyncio
async def test_blank_server_url_falls_back_to_default(monkeypatch):
    captured: dict = {}
    _patch_get(monkeypatch, payload={"disc": None}, captured=captured)
    await identify_disc_via_network("00" * 16, "")
    assert captured["url"] == f"{DEFAULT_FINGERPRINT_SERVER_URL}/v1/identify-disc"


@pytest.mark.asyncio
async def test_none_server_url_falls_back_to_default(monkeypatch):
    captured: dict = {}
    _patch_get(monkeypatch, payload={"disc": None}, captured=captured)
    await identify_disc_via_network("00" * 16, None)
    assert captured["url"] == f"{DEFAULT_FINGERPRINT_SERVER_URL}/v1/identify-disc"


@pytest.mark.asyncio
async def test_trailing_slash_server_url_normalized(monkeypatch):
    captured: dict = {}
    _patch_get(monkeypatch, payload={"disc": None}, captured=captured)
    await identify_disc_via_network("00" * 16, "https://server/")
    assert captured["url"] == "https://server/v1/identify-disc"


# ---------------------------------------------------------------------------
# Network-title -> DiscDbTitleMapping verification helper
# ---------------------------------------------------------------------------


def _scanned(index, duration, size):
    """Minimal scanned-title stand-in (TitleInfo-like duck type)."""
    from types import SimpleNamespace

    return SimpleNamespace(index=index, duration_seconds=duration, size_bytes=size)


def _net_title(**kw):
    base = dict(
        title_index=0,
        duration_seconds=2820,
        size_bytes=5_000_000_000,
        assignment="episode",
        season=1,
        episode=1,
        match_confidence=0.95,
        match_source="network_disc",
    )
    base.update(kw)
    return NetworkDiscTitle(**base)


def test_mapping_verifies_within_tolerance_uses_scanned_index():
    # Scanned title at a DIFFERENT index but within +-2s / +-1% -> mapped,
    # and the mapping's index comes from the SCANNED title (MakeMKV drift guard).
    scanned = [_scanned(index=7, duration=2821, size=5_010_000_000)]  # +1s, +0.2%
    net = [_net_title(title_index=0)]
    maps = network_titles_to_mappings(net, scanned)
    assert len(maps) == 1
    m = maps[0]
    assert m.index == 7  # from the scanned title, NOT the network title_index
    assert m.title_type == "Episode"
    assert m.season == 1 and m.episode == 1
    assert m.duration_seconds == 2821 and m.size_bytes == 5_010_000_000
    assert m.source == "network_disc"


def test_mapping_dropped_when_no_scanned_title_verifies():
    scanned = [_scanned(index=0, duration=100, size=1_000_000)]  # nowhere near
    net = [_net_title()]
    assert network_titles_to_mappings(net, scanned) == []


def test_duration_just_outside_tolerance_is_dropped():
    scanned = [_scanned(index=0, duration=2823, size=5_000_000_000)]  # +3s > 2s
    net = [_net_title(duration_seconds=2820)]
    assert network_titles_to_mappings(net, scanned) == []


def test_size_just_outside_one_percent_is_dropped():
    # +1.5% size delta, duration exact.
    scanned = [_scanned(index=0, duration=2820, size=5_075_000_000)]
    net = [_net_title(duration_seconds=2820, size_bytes=5_000_000_000)]
    assert network_titles_to_mappings(net, scanned) == []


def test_extra_and_discarded_assignments_are_skipped():
    scanned = [
        _scanned(index=0, duration=2820, size=5_000_000_000),
        _scanned(index=1, duration=600, size=900_000_000),
    ]
    net = [
        _net_title(title_index=1, duration_seconds=600, size_bytes=900_000_000, assignment="extra"),
        _net_title(
            title_index=0,
            duration_seconds=2820,
            size_bytes=5_000_000_000,
            assignment="discarded",
        ),
    ]
    assert network_titles_to_mappings(net, scanned) == []


def test_main_movie_assignment_maps_to_mainmovie_type():
    scanned = [_scanned(index=3, duration=7200, size=20_000_000_000)]
    net = [
        _net_title(
            title_index=0,
            duration_seconds=7200,
            size_bytes=20_000_000_000,
            assignment="main_movie",
            season=None,
            episode=None,
        )
    ]
    maps = network_titles_to_mappings(net, scanned)
    assert len(maps) == 1
    assert maps[0].title_type == "MainMovie"
    assert maps[0].index == 3
    assert maps[0].source == "network_disc"


def test_each_scanned_title_consumed_once():
    # Two network episodes with identical specs but only one scanned match ->
    # only the first should bind; the second finds nothing left.
    scanned = [_scanned(index=0, duration=2820, size=5_000_000_000)]
    net = [
        _net_title(title_index=0, episode=1),
        _net_title(title_index=9, episode=2),
    ]
    maps = network_titles_to_mappings(net, scanned)
    assert len(maps) == 1
    assert maps[0].episode == 1

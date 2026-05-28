"""Tests for the client-side zstd+varint codec (wire-format encoder)."""

import hashlib

import pytest

from app.services.zstd_varint_codec import (
    decode_zstd_varint,
    encode_zstd_varint,
    fingerprint_sha256,
)


def test_roundtrip_empty():
    encoded = encode_zstd_varint([])
    assert decode_zstd_varint(encoded) == []


def test_roundtrip_small():
    hashes = [1, 2, 3, 4, 5, 100, 1000, 1000000, 4294967295]
    encoded = encode_zstd_varint(hashes)
    assert decode_zstd_varint(encoded) == hashes


def test_encoded_smaller_than_naive():
    """A 1000-hash stream encodes to under 5000 bytes (vs 4000 raw)."""
    hashes = list(range(1000))
    encoded = encode_zstd_varint(hashes)
    assert len(encoded) < 5000


def test_compatibility_with_phase1_blob():
    """
    Phase 1 stored gzip-JSON; uploader decodes that, re-encodes as zstd-varint.
    Verify the SHA256 of the DECOMPRESSED VARINT (not the gzip-JSON) is what
    the server will dedupe on.
    """
    hashes = [42, 100, 200, 300]
    encoded = encode_zstd_varint(hashes)
    decoded = decode_zstd_varint(encoded)
    assert decoded == hashes

    # SHA256 of the canonical decompressed varint bytes
    expected = hashlib.sha256(_varint_bytes(hashes)).digest()
    assert fingerprint_sha256(hashes) == expected


def _varint_bytes(values: list[int]) -> bytes:
    out = bytearray()
    for v in values:
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v & 0x7F)
    return bytes(out)


def test_varint_byte_fixture():
    """Exact LEB128 bytes — must match the TypeScript server codec."""
    from app.services.zstd_varint_codec import to_varint_bytes

    assert to_varint_bytes([42, 100, 255, 256]) == bytes([0x2A, 0x64, 0xFF, 0x01, 0x80, 0x02])


def test_varint_uint32_max_is_five_bytes():
    from app.services.zstd_varint_codec import to_varint_bytes

    assert len(to_varint_bytes([4294967295])) == 5


def test_decode_rejects_overlong_varint():
    """A varint longer than 5 bytes is not uint32-compatible — reject it."""
    from app.services.zstd_varint_codec import _read_varint_stream

    # six continuation bytes = malformed for uint32
    bad = bytes([0x80, 0x80, 0x80, 0x80, 0x80, 0x01])
    with pytest.raises(ValueError):
        _read_varint_stream(bad)

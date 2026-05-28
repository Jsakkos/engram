"""Client-side zstd+varint codec.

Phase 1 stores chromaprints locally as gzip-JSON via ChromaprintResult.to_blob().
The uploader (Phase 2) re-encodes them as zstd-compressed varint streams on the
wire to match the server's storage format. This module is the encoder/decoder.

The varint scheme: standard LEB128 unsigned encoding, 7 bits per byte with the
high bit signaling continuation. Identical to the protobuf varint format and to
the TypeScript server codec, so byte sequences (and their SHA256) match across
languages.
"""

from __future__ import annotations

import hashlib

import zstandard as zstd

# Module-level singletons. zstandard's compressor/decompressor objects are NOT
# documented as thread-safe; this is fine for the current asyncio-only callers
# (the uploader runs on a single event loop). Do not share these across OS
# threads — construct per-thread instances if that ever becomes necessary.
_COMPRESSOR = zstd.ZstdCompressor(level=11)
_DECOMPRESSOR = zstd.ZstdDecompressor()


def _write_varint(buf: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError(f"varint values must be unsigned: {value}")
    while value >= 0x80:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value & 0x7F)


def _read_varint_stream(data: bytes) -> list[int]:
    out: list[int] = []
    value = 0
    shift = 0
    for b in data:
        value |= (b & 0x7F) << shift
        shift += 7
        if shift > 35:
            # Max valid uint32 LEB128 is 5 bytes (shifts 0,7,14,21,28).
            # shift > 35 means byte 6+ of one varint — not uint32-compatible.
            raise ValueError("varint > 5 bytes: stream not uint32-compatible")
        if (b & 0x80) == 0:
            out.append(value)
            value = 0
            shift = 0
    return out


def to_varint_bytes(hashes: list[int]) -> bytes:
    """uint32[] -> raw LEB128 varint bytes (uncompressed wire primitive)."""
    buf = bytearray()
    for h in hashes:
        _write_varint(buf, h)
    return bytes(buf)


def encode_zstd_varint(hashes: list[int]) -> bytes:
    """uint32[] -> zstd-compressed varint stream (wire format)."""
    return _COMPRESSOR.compress(to_varint_bytes(hashes))


def decode_zstd_varint(blob: bytes) -> list[int]:
    """zstd-compressed varint stream -> uint32[]."""
    if not blob:
        return []
    return _read_varint_stream(_DECOMPRESSOR.decompress(blob))


def fingerprint_sha256(hashes: list[int]) -> bytes:
    """SHA256 of the DECOMPRESSED varint stream. Server dedupes on this."""
    return hashlib.sha256(to_varint_bytes(hashes)).digest()

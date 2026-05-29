"""Local fingerprint-pack cache (Phase 3).

Downloads per-show packs from GET /v1/pack/{tmdb_id} and caches them under
~/.engram/cache/fingerprint_packs/. The pack format mirrors the server's
pack_builder.ts: zstd of newline-JSON (header line, per-episode lines, optional
df line). manifest.json carries per-show ETag + timestamps for 304 revalidation.
"""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import zstandard
from loguru import logger

from app.services.zstd_varint_codec import decode_zstd_varint

DEFAULT_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class DecodedPack:
    tmdb_id: int
    n_episodes: int
    episodes: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    df_map: dict[int, int] = field(default_factory=dict)


def _zstd_decompress(data: bytes) -> bytes:
    """Decompress a zstd frame, tolerating frames without an embedded content size
    (the server's wasm encoder may omit it). Streaming avoids the size requirement."""
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(data)) as reader:
        return reader.read()


class PackCache:
    def __init__(
        self, base_dir: Path | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self.base_dir = (
            Path(base_dir) if base_dir else Path("~/.engram/cache/fingerprint_packs").expanduser()
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def path(self, tmdb_id: int) -> Path:
        return self.base_dir / f"{tmdb_id}.zstd"

    def _manifest_path(self) -> Path:
        return self.base_dir / "manifest.json"

    def manifest(self) -> dict:
        p = self._manifest_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_manifest(self, m: dict) -> None:
        self._manifest_path().write_text(json.dumps(m, separators=(",", ":")))

    def has(self, tmdb_id: int) -> bool:
        if not self.path(tmdb_id).exists():
            return False
        entry = self.manifest().get(str(tmdb_id))
        if not entry:
            return True
        age = time.time() - entry.get("downloaded_at", 0)
        return age <= self.ttl_seconds

    def load(self, tmdb_id: int) -> DecodedPack | None:
        p = self.path(tmdb_id)
        if not p.exists():
            return None
        try:
            raw = _zstd_decompress(p.read_bytes())
        except zstandard.ZstdError as e:
            logger.warning(f"Corrupt pack for {tmdb_id}: {e}")
            return None
        lines = raw.decode("utf-8").split("\n")
        header = json.loads(lines[0])
        pack = DecodedPack(
            tmdb_id=int(header["tmdb_id"]), n_episodes=int(header.get("n_episodes", 0))
        )
        for line in lines[1:]:
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("kind") == "df":
                pack.df_map = {int(h): int(c) for h, c in obj.get("df", [])}
                continue
            blob = base64.b64decode(obj["fingerprint_b64"])
            pack.episodes[(int(obj["season"]), int(obj["episode"]))] = set(decode_zstd_varint(blob))
        return pack

    async def ensure(self, tmdb_id: int, server_url: str) -> bool:
        """Download/refresh the pack. Returns True if a usable pack is present afterward."""
        url = f"{server_url.rstrip('/')}/v1/pack/{tmdb_id}"
        entry = self.manifest().get(str(tmdb_id), {})
        headers = {}
        if self.path(tmdb_id).exists() and entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            logger.info(f"Pack fetch failed for {tmdb_id}: {e}")
            return self.path(tmdb_id).exists()

        if resp.status_code == 304:
            entry["downloaded_at"] = time.time()
            m = self.manifest()
            m[str(tmdb_id)] = entry
            self._write_manifest(m)
            return True
        if resp.status_code == 200:
            self.path(tmdb_id).write_bytes(resp.content)
            m = self.manifest()
            m[str(tmdb_id)] = {"etag": resp.headers.get("ETag"), "downloaded_at": time.time()}
            self._write_manifest(m)
            return True
        if resp.status_code == 404:
            return False
        logger.info(f"Pack fetch for {tmdb_id} returned {resp.status_code}")
        return self.path(tmdb_id).exists()

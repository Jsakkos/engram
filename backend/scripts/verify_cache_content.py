"""Confirm which cached shows are ACTUALLY corrupted by checking whether the
cached subtitles mention the show's own characters.

Complements ``audit_subtitle_cache.py`` (which only screens for TVsubtitles
resolution vulnerability and over-flags). This script is the authoritative
corruption detector: for each show it pulls the main cast's character names from
TMDB and checks how many appear in the cached subtitles. A show whose subtitles
mention none of its characters is almost certainly the wrong show (e.g. the
"2 Broke Girls" cache that is really Gilmore Girls, or "American Gods" that
matched nothing in the Gemini eval).

Usage (from backend/):
    uv run python scripts/verify_cache_content.py --shows "American Gods,Arrow"
    uv run python scripts/verify_cache_content.py            # whole cache
    uv run python scripts/verify_cache_content.py --out content_audit.json

Needs a TMDB Read Access Token in the app config (engram.db). Hits TMDB twice
per show (search + credits); does NOT modify the cache.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

import httpx


def _read_tmdb_token(cache_dir: Path) -> str:
    """Read the TMDB Read Access Token from engram.db. The real config lives in
    the same ``.engram`` dir as the cache (dev's backend/engram.db is empty)."""
    candidates = [
        cache_dir.parent / "engram.db",  # e.g. ~/.engram/engram.db (next to cache)
        Path.home() / ".engram" / "engram.db",
        Path("engram.db"),
    ]
    for db in candidates:
        if db.exists():
            try:
                # closing() guarantees the connection is closed even if the query
                # raises; a bare ``with sqlite3.connect()`` only manages the
                # transaction, not closure.
                with closing(sqlite3.connect(db)) as con:
                    row = con.execute("select tmdb_api_key from app_config limit 1").fetchone()
                if row and row[0]:
                    return row[0]
            except sqlite3.OperationalError:
                continue
    raise SystemExit(f"No tmdb_api_key found in any of: {[str(c) for c in candidates]}")


# Short/common character-name tokens that would cause false matches.
_STOP = {
    "the",
    "and",
    "mrs",
    "mr",
    "dr",
    "miss",
    "aunt",
    "uncle",
    "doctor",
    "young",
    "old",
    "officer",
    "agent",
    "king",
    "queen",
    "lord",
    "lady",
    "captain",
    "man",
    "woman",
    "girl",
    "boy",
    "father",
    "mother",
    "himself",
    "herself",
    "voice",
}
_TOP_CAST = 12  # main characters to check
# Cast-name mentions per 1000 words. A correct show names its leads constantly;
# a wrong show only hits the odd coincidental name. Calibrated on a labeled set.
_DENSITY_OK = 2.0
_DENSITY_BAD = 0.5


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _name_tokens(character: str) -> set[str]:
    return {t for t in _norm(character).split() if len(t) >= 4 and t not in _STOP}


def _tmdb_show_id(client: httpx.Client, show: str) -> int | None:
    r = client.get("/search/tv", params={"query": show})
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    exact = [x for x in results if _norm(x.get("name", "")) == _norm(show)]
    return (exact or results)[0]["id"]


def _character_token_sets(client: httpx.Client, show_id: int) -> list[set[str]]:
    r = client.get(f"/tv/{show_id}/aggregate_credits")
    r.raise_for_status()
    cast = r.json().get("cast", [])
    cast.sort(key=lambda c: c.get("total_episode_count", 0), reverse=True)
    sets = []
    for member in cast[:_TOP_CAST]:
        roles = member.get("roles") or [{}]
        toks = _name_tokens(roles[0].get("character", ""))
        if toks:
            sets.append(toks)
    return sets


def _sample_subtitle_text(show_dir: Path, max_files: int = 4) -> str:
    files = sorted(show_dir.glob("*.srt"))[:max_files]
    return " ".join(f.read_text(encoding="utf-8", errors="ignore").lower() for f in files)


def verify_show(client: httpx.Client, show_dir: Path) -> dict:
    show = show_dir.name
    try:
        show_id = _tmdb_show_id(client, show)
        if show_id is None:
            return {"show": show, "status": "no_tmdb"}
        char_sets = _character_token_sets(client, show_id)
    except Exception as e:
        return {"show": show, "status": "error", "detail": str(e)[:200]}

    if not char_sets:
        return {"show": show, "status": "no_cast_tokens"}

    text = _sample_subtitle_text(show_dir)
    words = max(len(text.split()), 1)
    # Frequency, not mere presence: real main characters are named dozens of times;
    # a coincidental collision (e.g. a "Sophie" in the wrong show) appears rarely.
    mentions = sum(text.count(t) for toks in char_sets for t in toks)
    present = sum(1 for toks in char_sets if any(t in text for t in toks))
    density = mentions / words * 1000  # cast-name mentions per 1000 words

    if density >= _DENSITY_OK:
        status = "content_ok"
    elif density <= _DENSITY_BAD:
        status = "content_mismatch"
    else:
        status = "content_weak"  # ambiguous — review manually
    return {
        "show": show,
        "status": status,
        "characters_checked": len(char_sets),
        "characters_present": present,
        "cast_mentions": mentions,
        "density_per_1k": round(density, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Confirm cache corruption via TMDB character names.")
    ap.add_argument("--cache-dir", default="~/.engram/cache")
    ap.add_argument(
        "--shows", default=None, help="Comma-separated show names (default: all cached)."
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Best-effort: a redirected/wrapped stream may lack reconfigure or
            # reject it; fall back to the default encoding.
            pass

    cache_root = Path(args.cache_dir).expanduser()
    token = _read_tmdb_token(cache_root)
    data_dir = cache_root / "data"
    if args.shows:
        wanted = [s.strip() for s in args.shows.split(",")]
        dirs = [data_dir / s for s in wanted if (data_dir / s).is_dir()]
    else:
        dirs = sorted(p for p in data_dir.iterdir() if p.is_dir() and any(p.glob("*.srt")))
    if args.limit:
        dirs = dirs[: args.limit]

    out_path = Path(args.out).expanduser() if args.out else data_dir.parent / "content_audit.json"
    print(f"Verifying content of {len(dirs)} cached shows against TMDB cast...\n")

    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    records = []

    with httpx.Client(
        base_url="https://api.themoviedb.org/3", headers=headers, timeout=30
    ) as client:
        for i, show_dir in enumerate(dirs, 1):
            rec = verify_show(client, show_dir)
            records.append(rec)
            if rec["status"] == "content_mismatch":
                print(
                    f"  [{i}/{len(dirs)}] CORRUPT  {rec['show']!r}: 0/"
                    f"{rec['characters_checked']} characters appear in subtitles"
                )
            elif rec["status"] in ("content_weak", "no_cast_tokens", "no_tmdb", "error"):
                print(f"  [{i}/{len(dirs)}] {rec['status'].upper():<16} {rec['show']!r}")

    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    by_status = Counter(r["status"] for r in records)
    print("\n=== Summary ===")
    for status, n in by_status.most_common():
        print(f"  {status:>16}: {n}")
    corrupt = [r["show"] for r in records if r["status"] == "content_mismatch"]
    if corrupt:
        print(f"\nCONFIRMED corrupt ({len(corrupt)}): {corrupt}")
    weak = [r["show"] for r in records if r["status"] == "content_weak"]
    if weak:
        print(f"Review manually ({len(weak)}): {weak}")
    print(f"\nFull report: {out_path}")


if __name__ == "__main__":
    main()

"""Audit the local subtitle cache for shows mislabeled by the TVsubtitles
first-result bug (see app/matcher/tvsubtitles_client._best_show_match).

TVsubtitles' search substring-matches and orders by ascending show id, so the
old resolver (first anchor, no name check) could download the wrong show's
subtitles into a show's cache dir — e.g. "2 Broke Girls" got Gilmore Girls.

For every show directory under ``<cache>/data/`` this re-runs the TVsubtitles
search and compares:
  - first  = the result the buggy resolver would pick (document order)
  - best   = the result whose title actually matches the show name

and flags directories where they disagree (likely corrupted) or where no title
matches (show not confidently on TVsubtitles).

Usage (from backend/):
    uv run python scripts/audit_subtitle_cache.py
    uv run python scripts/audit_subtitle_cache.py --limit 20
    uv run python scripts/audit_subtitle_cache.py --cache-dir ~/.engram/cache --out audit.json

NOTE: hits TVsubtitles once per show at ~1 req/sec, so a full cache (~250 shows)
takes a few minutes. It does NOT modify or re-download anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

# Make ``app`` importable when run as ``scripts/audit_subtitle_cache.py``.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.matcher.tvsubtitles_client import (  # noqa: E402
    TVSubtitlesClient,
    _best_show_match,
    _normalize_show_name,
    _parse_show_results,
    _strip_year_suffix,
)


def _enable_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Best-effort: a redirected/wrapped stream may lack reconfigure or
            # reject it; fall back to the default encoding.
            pass


def _cached_shows(data_dir: Path) -> list[str]:
    return sorted(p.name for p in data_dir.iterdir() if p.is_dir() and any(p.glob("*.srt")))


def audit_show(client: TVSubtitlesClient, show: str) -> dict:
    try:
        resp = client._post(urljoin(client.BASE_URL, "/search1.php"), data={"qs": show})
        resp.raise_for_status()
        results = _parse_show_results(resp.text)
    except Exception as e:  # network / parse — record and move on
        return {"show": show, "status": "error", "detail": str(e)[:200]}

    if not results:
        return {"show": show, "status": "no_results", "n_results": 0}

    first_id, first_title = results[0]
    best_id = _best_show_match(results, show)
    first_matches = _normalize_show_name(_strip_year_suffix(first_title)) == _normalize_show_name(
        show
    )

    rec = {
        "show": show,
        "n_results": len(results),
        "first_id": first_id,
        "first_title": first_title,
        "best_id": best_id,
    }
    if best_id is None:
        rec["status"] = "no_exact_match"
    elif not first_matches:
        # The OLD resolver would have grabbed `first_title` under the `show`
        # name. This flags a show as *vulnerable* to the mislabel bug — NOT a
        # confirmation of corruption: the build often falls back to another
        # provider that supplied the correct subtitles. Confirm with a content
        # check (TMDB cast tokens / the Gemini eval) before re-downloading.
        rec["status"] = "tvsub_mismatch"
    else:
        rec["status"] = "ok"
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit subtitle cache for mislabeled shows.")
    ap.add_argument("--cache-dir", default="~/.engram/cache", help="Path to the engram cache dir.")
    ap.add_argument("--limit", type=int, default=0, help="Audit only the first N shows (0 = all).")
    ap.add_argument("--out", default=None, help="Write the full JSON report here.")
    args = ap.parse_args()
    _enable_utf8_stdout()

    data_dir = Path(args.cache_dir).expanduser() / "data"
    if not data_dir.exists():
        raise SystemExit(f"Cache data dir not found: {data_dir}")
    out_path = Path(args.out).expanduser() if args.out else data_dir.parent / "cache_audit.json"

    shows = _cached_shows(data_dir)
    if args.limit:
        shows = shows[: args.limit]
    print(f"Auditing {len(shows)} cached shows against TVsubtitles (~1/sec)...\n")

    client = TVSubtitlesClient()
    records = []
    for i, show in enumerate(shows, 1):
        rec = audit_show(client, show)
        records.append(rec)
        if rec["status"] == "tvsub_mismatch":
            print(
                f"  [{i}/{len(shows)}] MISMATCH {show!r}: first hit = "
                f"{rec['first_title']!r} (id {rec['first_id']}); correct id {rec['best_id']}"
            )
        elif rec["status"] == "no_exact_match":
            print(
                f"  [{i}/{len(shows)}] NO-MATCH {show!r}: not found on TVsubtitles by exact title"
            )
        elif rec["status"] in ("no_results", "error"):
            print(f"  [{i}/{len(shows)}] {rec['status'].upper()} {show!r}")

    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== Summary ===")
    for status in ("tvsub_mismatch", "no_exact_match", "no_results", "error", "ok"):
        if status in by_status:
            print(f"  {status:>14}: {by_status[status]}")
    vulnerable = [r["show"] for r in records if r["status"] == "tvsub_mismatch"]
    if vulnerable:
        print(
            f"\nTVsubtitles-vulnerable shows ({len(vulnerable)}) — VERIFY CONTENT before "
            f"re-downloading (many are false positives sourced from other providers):"
        )
        print(f"  {vulnerable}")
    print(f"\nFull report: {out_path}")


if __name__ == "__main__":
    main()

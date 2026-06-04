#!/usr/bin/env python3
"""Update per-OS download badges and chart from GitHub release stats."""

import html
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Jsakkos/engram"
ROOT = Path(__file__).parent.parent
BADGES_DIR = ROOT / "docs" / "badges"
CHART_PATH = ROOT / "docs" / "downloads-chart.svg"

CYAN = "#06b6d4"
MAGENTA = "#ec4899"
VIOLET = "#a78bfa"
BG = "#0f172a"
TEXT = "#e2e8f0"
GRID = "#1e293b"

# Per-OS app binaries are matched by exact (prefix, suffix), NOT by bare
# extension. Counting every ".tar.gz" swept in the macOS builds and — far worse
# — the rolling "engram-subtitle-cache.tar.gz" data pack, whose download_count
# resets to 0 each time the cache is rebuilt and the asset is overwritten. That
# reset is what made the Linux badge "fluctuate down quite a bit".
PLATFORMS = {
    "windows": ("engram-windows-", ".zip"),
    "linux": ("engram-linux-", ".tar.gz"),
    "macos": ("engram-macos-", ".tar.gz"),
}
_APP_PREFIXES = tuple(prefix for prefix, _ in PLATFORMS.values())


def fetch_releases(token: str) -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                page_data: list[dict] = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub API returned {e.code} for page {page}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error fetching releases page {page}: {e.reason}") from e
        if not page_data:
            break
        releases.extend(page_data)
        page += 1
    return releases


def _platform_downloads(release: dict, prefix: str, suffix: str) -> int:
    return sum(
        a["download_count"]
        for a in release["assets"]
        if a["name"].startswith(prefix) and a["name"].endswith(suffix)
    )


def compute_stats(
    releases: list[dict],
) -> tuple[dict[str, int], list[tuple[str, int, int, int]]]:
    totals = {os_name: 0 for os_name in PLATFORMS}
    per_release: list[tuple[str, int, int, int]] = []
    for release in releases:
        tag: str = release["tag_name"]
        # Skip releases that ship no app binary at all (e.g. the rolling
        # subtitle-cache data-pack releases) so they neither pollute the totals
        # nor add empty rows to the chart.
        if not any(a["name"].startswith(_APP_PREFIXES) for a in release["assets"]):
            continue
        counts = {
            os_name: _platform_downloads(release, prefix, suffix)
            for os_name, (prefix, suffix) in PLATFORMS.items()
        }
        for os_name, count in counts.items():
            totals[os_name] += count
        per_release.append((tag, counts["windows"], counts["linux"], counts["macos"]))
    return totals, per_release


def write_badge_json(path: Path, label: str, count: int, color: str, logo: str) -> None:
    data = {
        "schemaVersion": 1,
        "label": label,
        "message": f"{count:,} downloads",
        "color": color,
        "namedLogo": logo,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def generate_svg(per_release: list[tuple[str, int, int, int]]) -> str:
    data = per_release[:10]
    if not data:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="60"><rect width="800" height="60" fill="{BG}" rx="8"/></svg>'

    max_val = max(max(w, lin, mac) for _, w, lin, mac in data) or 1

    width = 800
    label_w = 90
    bar_area = width - label_w - 24
    bar_h = 12
    bar_gap = 3
    header_h = 40
    legend_h = 28
    row_h = 3 * bar_h + 2 * bar_gap + 16
    rows = len(data)
    height = header_h + legend_h + rows * row_h + 16

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="system-ui,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="{BG}" rx="8"/>',
        f'<text x="{width // 2}" y="26" text-anchor="middle" fill="{TEXT}" font-size="14" font-weight="bold">Downloads per Release</text>',
        f'<rect x="{width - 280}" y="36" width="12" height="12" fill="{CYAN}" rx="2"/>',
        f'<text x="{width - 264}" y="46" fill="{TEXT}" font-size="11">Windows</text>',
        f'<rect x="{width - 190}" y="36" width="12" height="12" fill="{MAGENTA}" rx="2"/>',
        f'<text x="{width - 174}" y="46" fill="{TEXT}" font-size="11">Linux</text>',
        f'<rect x="{width - 110}" y="36" width="12" height="12" fill="{VIOLET}" rx="2"/>',
        f'<text x="{width - 94}" y="46" fill="{TEXT}" font-size="11">macOS</text>',
    ]

    y_base = header_h + legend_h
    for i, (tag, win, linux, mac) in enumerate(data):
        y = y_base + i * row_h
        if i % 2 == 0:
            parts.append(
                f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="{GRID}" opacity="0.5"/>'
            )
        parts.append(
            f'<text x="{label_w - 6}" y="{y + row_h // 2 + 4}" text-anchor="end" fill="{TEXT}" font-size="11">{html.escape(tag)}</text>'
        )
        for j, (val, color) in enumerate(((win, CYAN), (linux, MAGENTA), (mac, VIOLET))):
            by = y + 5 + j * (bar_h + bar_gap)
            bar_w = max(int((val / max_val) * bar_area), 2) if val else 0
            parts.append(
                f'<rect x="{label_w}" y="{by}" width="{bar_w}" height="{bar_h}" fill="{color}" rx="2"/>'
            )
            if val:
                parts.append(
                    f'<text x="{label_w + bar_w + 4}" y="{by + bar_h - 2}" fill="{color}" font-size="10">{val}</text>'
                )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable not set")

    releases = fetch_releases(token)
    totals, per_release = compute_stats(releases)

    BADGES_DIR.mkdir(parents=True, exist_ok=True)

    write_badge_json(
        BADGES_DIR / "windows-downloads.json",
        label="Windows",
        count=totals["windows"],
        color="06b6d4",
        logo="windows",
    )
    write_badge_json(
        BADGES_DIR / "linux-downloads.json",
        label="Linux",
        count=totals["linux"],
        color="ec4899",
        logo="linux",
    )
    write_badge_json(
        BADGES_DIR / "macos-downloads.json",
        label="macOS",
        count=totals["macos"],
        color="a78bfa",
        logo="apple",
    )

    CHART_PATH.write_text(generate_svg(per_release), encoding="utf-8")

    print(f"Windows total: {totals['windows']:,}")
    print(f"Linux total:   {totals['linux']:,}")
    print(f"macOS total:   {totals['macos']:,}")
    print(f"Chart:         {len(per_release)} releases (showing {min(len(per_release), 10)})")


if __name__ == "__main__":
    main()

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
BG = "#0f172a"
TEXT = "#e2e8f0"
GRID = "#1e293b"


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


def compute_stats(
    releases: list[dict],
) -> tuple[int, int, list[tuple[str, int, int]]]:
    total_windows = 0
    total_linux = 0
    per_release: list[tuple[str, int, int]] = []
    for release in releases:
        tag: str = release["tag_name"]
        win = sum(
            a["download_count"]
            for a in release["assets"]
            if a["name"].endswith(".zip")
        )
        linux = sum(
            a["download_count"]
            for a in release["assets"]
            if a["name"].endswith(".tar.gz")
        )
        total_windows += win
        total_linux += linux
        per_release.append((tag, win, linux))
    return total_windows, total_linux, per_release


def write_badge_json(
    path: Path, label: str, count: int, color: str, logo: str
) -> None:
    data = {
        "schemaVersion": 1,
        "label": label,
        "message": f"{count:,} downloads",
        "color": color,
        "namedLogo": logo,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def generate_svg(per_release: list[tuple[str, int, int]]) -> str:
    data = per_release[:10]
    if not data:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="60"><rect width="800" height="60" fill="{BG}" rx="8"/></svg>'

    max_val = max(max(w, l) for _, w, l in data) or 1

    width = 800
    label_w = 90
    bar_area = width - label_w - 24
    row_h = 44
    bar_h = 14
    bar_gap = 4
    header_h = 40
    legend_h = 28
    rows = len(data)
    height = header_h + legend_h + rows * row_h + 16

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="system-ui,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="{BG}" rx="8"/>',
        f'<text x="{width // 2}" y="26" text-anchor="middle" fill="{TEXT}" font-size="14" font-weight="bold">Downloads per Release</text>',
        f'<rect x="{width - 190}" y="36" width="12" height="12" fill="{CYAN}" rx="2"/>',
        f'<text x="{width - 174}" y="46" fill="{TEXT}" font-size="11">Windows</text>',
        f'<rect x="{width - 110}" y="36" width="12" height="12" fill="{MAGENTA}" rx="2"/>',
        f'<text x="{width - 94}" y="46" fill="{TEXT}" font-size="11">Linux</text>',
    ]

    y_base = header_h + legend_h
    for i, (tag, win, linux) in enumerate(data):
        y = y_base + i * row_h
        if i % 2 == 0:
            parts.append(
                f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="{GRID}" opacity="0.5"/>'
            )
        parts.append(
            f'<text x="{label_w - 6}" y="{y + 19}" text-anchor="end" fill="{TEXT}" font-size="11">{html.escape(tag)}</text>'
        )
        win_w = max(int((win / max_val) * bar_area), 2) if win else 0
        parts.append(
            f'<rect x="{label_w}" y="{y + 5}" width="{win_w}" height="{bar_h}" fill="{CYAN}" rx="2"/>'
        )
        if win:
            parts.append(
                f'<text x="{label_w + win_w + 4}" y="{y + 16}" fill="{CYAN}" font-size="10">{win}</text>'
            )
        linux_w = max(int((linux / max_val) * bar_area), 2) if linux else 0
        parts.append(
            f'<rect x="{label_w}" y="{y + 5 + bar_h + bar_gap}" width="{linux_w}" height="{bar_h}" fill="{MAGENTA}" rx="2"/>'
        )
        if linux:
            parts.append(
                f'<text x="{label_w + linux_w + 4}" y="{y + 16 + bar_h + bar_gap}" fill="{MAGENTA}" font-size="10">{linux}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable not set")

    releases = fetch_releases(token)
    total_windows, total_linux, per_release = compute_stats(releases)

    BADGES_DIR.mkdir(parents=True, exist_ok=True)

    write_badge_json(
        BADGES_DIR / "windows-downloads.json",
        label="Windows",
        count=total_windows,
        color="06b6d4",
        logo="windows",
    )
    write_badge_json(
        BADGES_DIR / "linux-downloads.json",
        label="Linux",
        count=total_linux,
        color="ec4899",
        logo="linux",
    )

    CHART_PATH.write_text(generate_svg(per_release), encoding="utf-8")

    print(f"Windows total: {total_windows:,}")
    print(f"Linux total:   {total_linux:,}")
    print(f"Chart:         {len(per_release)} releases (showing {min(len(per_release), 10)})")


if __name__ == "__main__":
    main()

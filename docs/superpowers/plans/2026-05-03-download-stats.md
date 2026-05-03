# Download Stats Badges & Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-OS download count badges and a per-release download bar chart to the README, auto-updated by a GitHub Actions workflow.

**Architecture:** A pure-Python stdlib script queries the GitHub releases API, writes two shields.io endpoint JSON files and one SVG bar chart into `docs/`, then a workflow commits those files on release publish and on a daily schedule.

**Tech Stack:** Python 3.11 stdlib (`urllib.request`, `json`, `pathlib`, `os`), GitHub Actions, shields.io endpoint badges.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `scripts/update_download_stats.py` | Fetch API, write badge JSONs, write SVG chart |
| Create | `.github/workflows/download-stats.yml` | Trigger on release + schedule, run script, commit |
| Create | `docs/badges/windows-downloads.json` | Generated shields.io badge JSON (Windows totals) |
| Create | `docs/badges/linux-downloads.json` | Generated shields.io badge JSON (Linux totals) |
| Create | `docs/downloads-chart.svg` | Generated SVG horizontal bar chart |
| Modify | `README.md` lines 15–19 | Add two endpoint badges to badge row |
| Modify | `README.md` line 42 | Add Downloads section with SVG chart after Screenshots table |

---

### Task 1: Write the download stats script

**Files:**
- Create: `scripts/update_download_stats.py`

- [ ] **Step 1: Create `scripts/update_download_stats.py`**

```python
#!/usr/bin/env python3
"""Update per-OS download badges and chart from GitHub release stats."""

import json
import os
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
        with urllib.request.urlopen(req) as resp:
            page_data: list[dict] = json.loads(resp.read())
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
        "logoColor": "white",
        "logo": logo,
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
            f'<text x="{label_w - 6}" y="{y + 19}" text-anchor="end" fill="{TEXT}" font-size="11">{tag}</text>'
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
```

- [ ] **Step 2: Run the script locally to verify output**

```bash
$env:GITHUB_TOKEN = (gh auth token)
python scripts/update_download_stats.py
```

Expected output (values will differ):
```
Windows total: 51
Linux total:   12
Chart:         19 releases (showing 10)
```

Expected files created:
- `docs/badges/windows-downloads.json`
- `docs/badges/linux-downloads.json`
- `docs/downloads-chart.svg`

- [ ] **Step 3: Inspect badge JSON**

Open `docs/badges/windows-downloads.json`. Should look like:
```json
{
  "schemaVersion": 1,
  "label": "Windows",
  "message": "51 downloads",
  "color": "06b6d4",
  "logoColor": "white",
  "logo": "windows"
}
```

- [ ] **Step 4: Inspect the SVG**

Open `docs/downloads-chart.svg` in a browser (drag the file onto a browser tab). Should show a dark-background horizontal bar chart with cyan Windows bars and magenta Linux bars, newest release at top.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/download-stats
git add scripts/update_download_stats.py docs/badges/ docs/downloads-chart.svg
git commit -m "feat: add download stats script and initial generated files"
```

---

### Task 2: Create the GitHub Actions workflow

**Files:**
- Create: `.github/workflows/download-stats.yml`

- [ ] **Step 1: Create `.github/workflows/download-stats.yml`**

```yaml
name: Download Stats

on:
  release:
    types: [published]
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  update-stats:
    name: Update Download Stats
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"

      - name: Update download stats
        run: python scripts/update_download_stats.py
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Commit changes
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/badges/ docs/downloads-chart.svg
          git diff --cached --quiet || git commit -m "chore: update download stats"
          git push
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/download-stats.yml
git commit -m "ci: add download-stats workflow (daily + on release)"
```

---

### Task 3: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add two download badges to the badge row**

Find the existing badge block (lines 15–19):
```html
<p align="center">
  <a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/github/v/release/Jsakkos/engram?style=flat-square&color=06b6d4" alt="Release" /></a>
  <a href="https://github.com/Jsakkos/engram/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Jsakkos/engram/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Jsakkos/engram?style=flat-square&color=ec4899" alt="License" /></a>
</p>
```

Replace with:
```html
<p align="center">
  <a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/github/v/release/Jsakkos/engram?style=flat-square&color=06b6d4" alt="Release" /></a>
  <a href="https://github.com/Jsakkos/engram/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Jsakkos/engram/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Jsakkos/engram?style=flat-square&color=ec4899" alt="License" /></a>
  <a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Jsakkos/engram/main/docs/badges/windows-downloads.json&style=flat-square" alt="Windows Downloads" /></a>
  <a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Jsakkos/engram/main/docs/badges/linux-downloads.json&style=flat-square" alt="Linux Downloads" /></a>
</p>
```

- [ ] **Step 2: Add Downloads section after the Screenshots table**

Find the closing `</table>` tag followed by `## Features` (around line 42–44):
```markdown
</table>

## Features
```

Replace with:
```markdown
</table>

## Downloads

<p align="center">
  <img src="docs/downloads-chart.svg" alt="Downloads per release" />
</p>

## Features
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add download badges and chart to README"
```

---

### Task 4: Push and verify

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/download-stats
```

- [ ] **Step 2: Open a PR and check the preview**

```bash
gh pr create --title "feat: download stats badges and chart" --body "$(cat <<'EOF'
## Summary
- Adds Windows and Linux download count badges to the README badge row
- Adds a per-release download bar chart (SVG) to the README
- Adds `.github/workflows/download-stats.yml` to regenerate on release publish and daily at 02:00 UTC

## Test plan
- [ ] Badge JSON files are present in `docs/badges/`
- [ ] `docs/downloads-chart.svg` renders correctly in a browser (dark background, cyan Windows bars, magenta Linux bars)
- [ ] README preview shows the new badges and chart correctly
- [ ] Trigger `workflow_dispatch` on the workflow after merge to confirm it runs and commits

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge, manually trigger the workflow to confirm it runs end-to-end**

```bash
gh workflow run download-stats.yml
```

Wait ~30 seconds, then check:
```bash
gh run list --workflow=download-stats.yml --limit=1
```

Expected: `completed` with `success` status and a commit `chore: update download stats` on `main`.

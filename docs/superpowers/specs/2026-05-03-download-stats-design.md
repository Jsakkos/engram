# Download Stats Badges & Chart

**Date:** 2026-05-03  
**Status:** Approved

## Summary

Add per-OS download count badges and a per-release download bar chart to the README, auto-updated by a GitHub Actions workflow on every release publish and daily schedule.

## Architecture

### New files

| Path | Purpose |
|------|---------|
| `.github/workflows/download-stats.yml` | Workflow: triggers on release + daily cron |
| `scripts/update_download_stats.py` | Python stdlib script: fetches GitHub API, writes badge JSONs + SVG chart |
| `docs/badges/windows-downloads.json` | Shields.io endpoint JSON for Windows badge (committed by workflow) |
| `docs/badges/linux-downloads.json` | Shields.io endpoint JSON for Linux badge (committed by workflow) |
| `docs/downloads-chart.svg` | SVG bar chart committed by workflow |

### Modified files

| Path | Change |
|------|--------|
| `README.md` | Add two endpoint badges to badge row; add Downloads section with SVG chart |

## Workflow (`download-stats.yml`)

**Triggers:**
- `on: release: types: [published]` — fires immediately on release publish
- `on: schedule: cron: '0 2 * * *'` — daily at 02:00 UTC
- `on: workflow_dispatch` — manual trigger for testing

**Permissions:** `contents: write` (same pattern as `docs.yml`)

**Steps:**
1. `actions/checkout@v6`
2. `actions/setup-python@v6` with Python 3.11
3. Run `python scripts/update_download_stats.py`
4. Commit and push if any files changed (skip if no diff — no empty commits)

**Auth:** Uses built-in `GITHUB_TOKEN` — no PAT required.

**Commit attribution:** `github-actions[bot]` with message `chore: update download stats`.

## Script (`update_download_stats.py`)

**Dependencies:** stdlib only (`urllib.request`, `json`, `os`, `pathlib`)

**Algorithm:**
1. Call `GET /repos/Jsakkos/engram/releases` (paginated, all pages)
2. For each release, sum `download_count` where asset name ends in `.zip` → Windows total; ends in `.tar.gz` → Linux total
3. Write `docs/badges/windows-downloads.json` and `docs/badges/linux-downloads.json` in shields.io endpoint schema:
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
4. Build per-release data: list of `(tag, windows_count, linux_count)` sorted newest-first, capped at 10 most recent releases for readability
5. Generate `docs/downloads-chart.svg`: horizontal bar chart, two bars per release row (cyan `#06b6d4` for Windows, magenta `#ec4899` for Linux), legend, no external fonts

## README Changes

**Badge row** — append two shields.io endpoint badges after the existing license badge:
```markdown
<a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Jsakkos/engram/main/docs/badges/windows-downloads.json&style=flat-square" alt="Windows Downloads" /></a>
<a href="https://github.com/Jsakkos/engram/releases"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Jsakkos/engram/main/docs/badges/linux-downloads.json&style=flat-square" alt="Linux Downloads" /></a>
```

**Downloads section** — added after the existing screenshots table:
```markdown
## Downloads
<p align="center">
  <img src="docs/downloads-chart.svg" alt="Downloads per release" />
</p>
```

## Chart Spec

- **Type:** Horizontal bar chart
- **Bars:** Two per release row — Windows (cyan `#06b6d4`), Linux (magenta `#ec4899`)
- **Y-axis:** Release tags, newest at top, capped at 10 releases
- **X-axis:** Download count, auto-scaled to max value
- **Dimensions:** 800×(60 + rows×44)px — fits GitHub README column
- **Font:** system-ui / sans-serif (no external font dependency)
- **Background:** dark (`#0f172a`) to match cyberpunk theme
- **Legend:** top-right, Windows / Linux labels with color swatches

## Error Handling

- If GitHub API returns non-200, script exits non-zero → workflow fails visibly
- If a release has no matching assets (e.g. old releases before Linux was added), that OS count is 0 — not an error
- If `docs/badges/` directory doesn't exist, script creates it

## Out of Scope

- Historical time-series graph (would require accumulating snapshots over time)
- PyPI download tracking
- Per-architecture breakdown

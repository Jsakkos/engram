---
hide:
  - navigation
---

<div style="text-align: center;" markdown>

![Engram](engram.svg){ width="120" }

# Engram

**Disc ripping and media organization with a reactive web dashboard.**

Monitors optical drives, rips with MakeMKV, identifies episodes via audio fingerprinting,
and files everything into your media library -- automatically.

[![Release](https://img.shields.io/github/v/release/Jsakkos/engram?style=flat-square&color=06b6d4)](https://github.com/Jsakkos/engram/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/Jsakkos/engram/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Jsakkos/engram/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Jsakkos/engram?style=flat-square&color=ec4899)](https://github.com/Jsakkos/engram/blob/main/LICENSE)

</div>

---

## Workflow

<div class="grid" markdown>

| ![Ripping in progress](screenshots/03-ripping-state.png) |
|:--:|
| *Ripping a TV disc with real-time progress* |

| ![Per-track progress](screenshots/05-per-track-ripping.png) |
|:--:|
| *Track grid showing per-episode byte progress* |

| ![Episode matching](screenshots/08-match-candidates.png) |
|:--:|
| *Audio fingerprint matching with confidence scores* |

</div>

---

## Features

- **Automatic disc detection** -- monitors optical drives and starts processing on insertion
- **Smart classification** -- distinguishes TV shows from movies using duration analysis, TMDB lookup, and TheDiscDB
- **Audio fingerprint matching** -- identifies TV episodes via ASR transcription matched against subtitles
- **Real-time dashboard** -- cyberpunk-themed web UI with WebSocket live updates, progress tracking, and notifications
- **Human-in-the-loop** -- review queue for low-confidence matches with competing candidate display
- **Job history & analytics** -- searchable archive of all completed/failed jobs with drill-down detail panel, processing timeline, and TheDiscDB metadata
- **TheDiscDB integration** -- automatic disc identification via content hash fingerprinting with persisted title mappings
- **Responsive design** -- works on desktop and mobile with compact/expanded view modes

## Platform Support

| Feature | Windows | Linux | macOS |
|---------|---------|-------|-------|
| Automatic drive detection | Yes | No | No |
| MakeMKV ripping | Yes | Yes | Yes |
| Episode matching (ASR) | Yes | Yes | Yes |
| Web dashboard & API | Yes | Yes | Yes |
| Tool auto-detection | Yes | Yes | Yes |
| TheDiscDB / TMDB lookup | Yes | Yes | Yes |

**Windows** is the primary platform with full automatic disc detection via kernel32 APIs. On **Linux** and **macOS**, the backend and dashboard run fully, but disc insertion must be triggered manually via the simulation API or by pointing Engram at a staging directory with pre-ripped files.

---

<div style="text-align: center;" markdown>

[Get Started](getting-started/installation.md){ .md-button .md-button--primary }
[API Reference](api/rest.md){ .md-button }
[Architecture](architecture/overview.md){ .md-button }

</div>

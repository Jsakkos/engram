# Changelog

All notable changes to Engram will be documented in this file.

## [0.1.9] - 2026-02-22

### Fixed
- Discs with generic Windows volume labels (e.g. `LOGICAL_VOLUME_ID`, `VIDEO_TS`, `BDMV`) no longer produce spurious TMDB search results and wrong detected titles
- TMDB name overrides are now guarded by a Jaccard word-token similarity check (≥ 35%); completely unrelated TMDB matches are discarded and the parsed disc name is preserved
- Jobs where the disc name cannot be detected now enter `REVIEW_NEEDED` state instead of attempting to rip with an unknown title

### Added
- **Name Prompt Modal**: when a disc label is unreadable, a cyberpunk-styled modal prompts the user to enter the title, media type (TV/Movie), and season number before ripping begins
- `POST /api/jobs/{job_id}/set-name` endpoint to resume a stalled job after the user provides a name and content type
- `review_reason` field on `DiscJob` model to communicate why a job entered review state (SQLite migration: `ALTER TABLE disc_jobs ADD COLUMN review_reason TEXT`)
- `backend/scripts/migrate_db.py` utility script for applying future schema migrations to an existing database
- 9 new unit tests covering generic label detection and TMDB similarity guard

## [0.1.8] - 2026-02-22

### Fixed
- CI/CD failures: formatting, lock file sync, and cross-platform test compatibility

## [0.1.7] - 2026-02-22

### Fixed
- TMDB classifier bug causing incorrect content type detection

## [0.1.6] - 2026-02-22

### Fixed
- Multiple tracks showing RIPPING state simultaneously
- Per-track ripping progress stuck at 0% during real disc rips
- Movie review workflow, config wizard key visibility, and review page overhaul

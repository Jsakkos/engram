# Changelog

All notable changes to Engram will be documented in this file.

## [0.5.0] - 2026-04-05

### Changed
- **JobManager decomposition**: broke up the 4,295-line `JobManager` (52 methods) into 5 focused coordinators + thin orchestrator (#58)
  - `IdentificationCoordinator` — disc scanning, DiscDB/TMDB/AI classification
  - `MatchingCoordinator` — episode matching, subtitles, file readiness
  - `FinalizationCoordinator` — conflict resolution, organization, review workflow
  - `CleanupService` — staging cleanup, timed cleanup, DiscDB export
  - `SimulationService` — all simulation methods for E2E testing
  - `JobManager` reduced from 4,295 to 1,166 lines
- **Alembic for database migrations**: replaced custom `_migrate_schema()` with Alembic for versioned, reversible migrations; existing databases auto-stamped on first startup (#58)
- **CORS origins configurable**: read from `CORS_ORIGINS` env var (via `Settings` model) instead of hardcoded localhost (#58)

### Added
- **WebSocket heartbeat**: server sends ping every 30s to detect and clean up stale connections (#58)
- **Accessibility improvements**: ARIA attributes and keyboard handlers on DiscCard, ReviewQueue, ConfigWizard, NamePromptModal (#58)

### Fixed
- **Memory leak**: `_episode_runtimes` and `_discdb_mappings` per-job caches now cleared on job completion/failure (#58)
- **Blocking event loop**: `DiscAnalyst` config loading switched from sync DB call to async preloading in async contexts (#58)
- **Sync engine churn**: `get_config_sync()` now caches the sync SQLAlchemy engine instead of creating one per call (#58)
- **O(n²) loop**: `has_selection` check in `_run_ripping` hoisted out of inner loop (#58)
- **Heartbeat deadlock risk**: heartbeat closes socket directly instead of calling `disconnect()` to avoid lock contention with `broadcast()` (#58)

### Removed
- Unused frontend dependencies: `@mui/material`, `@mui/icons-material`, `@emotion/react`, `@emotion/styled`, `react-router` v7 (#58)

## [0.4.5] - 2026-04-04

### Fixed
- **Multi-drive cancel isolation**: canceling one drive's rip no longer kills another drive's rip — `MakeMKVExtractor` now tracks processes per job (#64)
- **Elapsed time 1-hour offset**: replaced deprecated `datetime.utcnow()` with `datetime.now(UTC)` across all backend files; frontend appends `Z` suffix to naive timestamps (#61)
- **Catalog-number volume labels**: labels like `BBCDVD1550` are now detected as publisher catalog codes and trigger the name prompt when TMDB/DiscDB lookups fail (#62)

### Added
- **Season selector in episode review**: users can now pick season S01–S20 in the TV review UI instead of being locked to the auto-detected season (#63)
- 5 new multi-drive integration tests: concurrent ripping, cancel isolation, drive removal isolation, mixed content, dual identification (#65)
- Catalog number detection unit tests

### Changed
- Bumped GitHub Actions: `actions/setup-node` v4→v6, `astral-sh/setup-uv` v4→v7, `actions/setup-python` v5→v6

## [0.4.4] - 2026-03-15

### Added
- History tab improvements: enhanced drill-down detail panel with processing timeline and per-track breakdown
- DiscDB mapping persistence: `discdb_mappings_json` column on `DiscJob` stores serialized title mappings across server restarts
- CI caching: Playwright browsers cached by version, uv packages cached by lockfile hash, apt packages cached via `cache-apt-pkgs-action`
- Safe database migration: `ALTER TABLE ADD COLUMN` for additive changes preserves job history; drop/recreate only when columns are removed

## [0.4.3] - 2026-03-14

### Added
- Show ripping stall errors in UI: when MakeMKV stalls or encounters read errors, the dashboard now displays the error state on the affected job card

## [0.4.2] - 2026-03-13

### Fixed
- Resolve movie track state stuck at QUEUED during real disc ripping

## [0.4.1] - 2026-03-12

### Fixed
- Resolve Play All titles blocking job completion and stale RIPPING state

### Added
- Multi-track movie E2E tests and CI `e2e-tests` job
- Playwright global setup to dismiss ConfigWizard in CI
- E2E selectors updated for post-UI-overhaul compatibility

## [0.4.0] - 2026-03-10

### Added
- **Full UI/UX overhaul**: navy theme with circuit board background, enhanced components, holographic poster effects
- **Responsive design**: compact/expanded view modes, elapsed time display, browser notifications
- **Job history & analytics**: searchable archive of all completed/failed jobs with drill-down detail panel, stats dashboard, deep-linkable via `/history/:jobId`
- **Diagnostics endpoints**: `GET /api/diagnostics/report`, bug report button in UI
- **CI/release workflow**: Linux release build, auto-publish releases on tag push
- **AI disc resolution**: support for Anthropic, OpenAI, and OpenRouter providers for ambiguous disc identification

### Fixed
- Pre-existing ruff lint and format issues

## [0.3.0] - 2026-03-01

### Added
- **TheDiscDB integration**: automatic disc identification via content hash fingerprinting (#35)
- **Configurable extras handling**: policy for bonus content (behind-the-scenes, deleted scenes) during organization (#25, #26)
- **Naming conventions**: standardized file naming patterns for movies and TV shows
- **Configurable staging file cleanup**: policies for managing temporary ripped files (#28)
- **Split processing states**: separate matching and organizing states with match confidence scores (#16, #30)
- **TMDB API key validation**: endpoint and UI for validating TMDB tokens (#22)
- **Disc scan metadata logging**: snapshot capture for debugging classification (#34)
- **Exhaustive test coverage**: state machine property-based tests, integration test improvements (#27)

### Fixed
- TMDB classification override logic (#33)
- Movie track progress stuck in ripping state (#32)
- DiscDB: keep extras selected for ripping when DiscDB identifies movie
- DiscDB: use MainMovie mapping to skip review for known movie discs

### Changed
- Flattened matcher module directory structure (#23)

## [0.1.9] - 2026-02-22

### Fixed
- Discs with generic Windows volume labels (e.g. `LOGICAL_VOLUME_ID`, `VIDEO_TS`, `BDMV`) no longer produce spurious TMDB search results and wrong detected titles
- TMDB name overrides are now guarded by a Jaccard word-token similarity check; completely unrelated TMDB matches are discarded and the parsed disc name is preserved
- Jobs where the disc name cannot be detected now enter `REVIEW_NEEDED` state instead of attempting to rip with an unknown title

### Added
- **Name Prompt Modal**: when a disc label is unreadable, a cyberpunk-styled modal prompts the user to enter the title, media type (TV/Movie), and season number before ripping begins
- `POST /api/jobs/{job_id}/set-name` endpoint to resume a stalled job after the user provides a name and content type
- `review_reason` field on `DiscJob` model to communicate why a job entered review state
- `backend/scripts/migrate_db.py` utility script for applying future schema migrations
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

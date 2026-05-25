# Changelog

All notable changes to Engram will be documented in this file.

## [0.7.3] - 2026-05-25

### Fixed
- **Ripping progress detection**: MakeMKV robot-mode output (`PRGC`/`PRGV`) was misread — the leading field is a message code (not a title index) and progress is `value/65536` (not `current/total`) — producing phantom "title 5018 is ripping" states and >100% per-title progress bars. The filesystem monitor (output-file sizes) is now the single source of per-title and overall progress; the stall-watchdog heartbeat is also fed from it (#209).
- **Redundant disc re-scans during ripping**: each title previously triggered its own `makemkvcon` invocation, re-opening and re-scanning the whole disc every time. Ripping now issues one `makemkvcon … all` pass for the full disc selection, falling back to individual re-rips only for any titles missing from that pass (#209).

### Changed
- **CI macOS Intel runner**: the `macos-13` GitHub Actions runner used to build the x64 release binary was retired and is no longer available; replaced with `macos-15-intel` so macOS x64 release builds succeed again (#210).
- **Subtitle cache build speed**: seasons already covered on disk are skipped on each daily run — no TMDB, OpenSubtitles, or scraper calls — until a configurable freshness window (default 30 days) expires or `--refresh` forces a full re-harvest. Previously every season was re-attempted on each run regardless of prior coverage (#204).

## [0.7.2] - 2026-05-25

### Fixed
- **macOS frozen-build launch crash**: the packaged app now calls `multiprocessing.freeze_support()`
  before spawning workers, preventing an infinite fork-bomb on macOS where the spawn start method
  caused worker processes to re-execute the frozen entry point — opening endless browser windows and
  crashing immediately (#206).
- **macOS Intel binary mislabeled as x64**: `macos-latest` GitHub Actions runner is Apple Silicon,
  so prior releases shipped an arm64 binary as `engram-macos-x64.tar.gz` (Intel Macs received
  "bad CPU type"). CI now builds on `macos-13` (x64) and `macos-14` (arm64) separately (#206).
- **Python 3.14 incompatibility**: `requires-python` capped to `<3.14` as `onnxruntime` (via
  `faster-whisper`) has no cp314 wheel; backend Python pinned to 3.13 (#206).

### Added
- **macOS Apple Silicon download**: `engram-macos-arm64.tar.gz` is now published as a dedicated
  release artifact for M1/M2/M3/M4 Macs (#206).

### Changed
- **Hardened cross-platform smoke tests**: release builds assert binary architecture with `file`
  and a process-count guard (≤ 2 processes) catches re-spawn bugs headlessly; CI runs `uv sync`
  resolution across Python 3.11–3.13 on Ubuntu and macOS arm64 (#206).

## [0.7.1] - 2026-05-23

### Fixed
- **Frozen-build database upgrades**: the packaged app now drops columns removed from the model on startup, fixing a crash when inserting a disc (`NOT NULL constraint failed: disc_jobs.is_transcoding_enabled`) for users upgrading from a build that still had the removed "Enable transcoding" setting (#190).

## [0.7.0] - 2026-05-23

### Added
- **Pre-built subtitle cache**: ships a precomputed subtitle-vector cache so episode matching can run without scraping subtitle sites on every disc, falling back to live scraping only when a season isn't covered (#140). Cache builds are now resumable and log API status (#149), and the builder accepts a `--show-list` to target specific shows.
- **Smarter episode matcher**: persistent on-disk caches plus a threaded provider scheduler and reworked subtitle providers (#155), a per-provider circuit breaker so a failing source no longer stalls a run, interpretable 0–1 confidence scores (#169), and automatic deep re-matching when episodes conflict (#171). Match results now surface which subtitle provider contributed (#158).
- **Redesigned TV disc review**: an inspector-style layout with disc-level conflict detection, making it clearer which episodes clash before you commit (#165).
- **Diagnostics improvements**: bug reports can be previewed before sending and now report real installed tool versions (#174).
- **Resilient frontend**: API and WebSocket errors are handled gracefully with reconnection instead of breaking the dashboard (#180).
- **Brand refresh**: the canonical Synapse v2 brand system (#156), plus an ambient ripping animation and a bottom-anchored status bar (#137).

### Fixed
- **Ripping reliability**: the long-held database session in `_run_ripping` is now tightly scoped to avoid blocking other work (#185), and MakeMKV subprocesses are drained on shutdown alongside matching-lifecycle fixes (#181).
- **Movies**: long bonus tracks are no longer incorrectly flagged as needing review (#175).
- **Review flow**: the Process action returns to the dashboard instead of erroring (#173), and re-running a match re-matches all titles with live progress (#164).
- **MakeMKV validation**: the real installed version is detected from the robot-mode banner (#177).
- **Subtitle matching**: subtitle download is skipped when the precomputed cache already covers a season (#163); tvsubtitles episode resolution and candidate parsing were corrected (#159); UTF-16-encoded SRTs are now accepted; OpenSubtitles quota is reported accurately and skipped when exhausted.
- **Logging**: corrected log-source attribution and now surfaces disc-event errors that were previously silent (#168).
- **Security**: hardened SSRF and path-traversal sinks flagged by CodeQL (#147).

### Changed
- **Subtitle cache format v2**: ~85% smaller on disk via a compact `uint16` encoding (#154).
- **Documentation**: README reworked to be end-user-first with supporting docs consolidated (#162).
- Codebase-wide simplification sweep for maintainability (#143).

### Removed
- The unimplemented "Enable transcoding" setting (#138).
- The obsolete skyline-silhouette atmosphere layer (#139).

## [0.6.0] - 2026-05-02

### Added
- **OpenSubtitles.com REST API**: subtitle downloads now use the official `opensubtitlescom` REST API as the primary path (batch-downloads a whole season in one search call). Addic7ed and OpenSubtitles.org web scrapers remain as per-episode fallbacks. Configure API key, username, and password in Settings → TMDB & Subtitles.
- **Disc name identification via MakeMKV CINFO codes**: extractor now captures the disc display name from `CINFO:2` (e.g. `"Star Trek: Strange New Worlds - Season 3 (Disc 1)"`). When the volume label produces a failed TMDB lookup, the disc name is parsed and tried as a second-chance TMDB query — silently resolving merged-word labels like `STRANGENEWWORLDS_SEASON3` without any user prompt.
- **TMDB-failure review gate**: if both the volume label and disc name fail TMDB lookup for a TV show, the job now enters `REVIEW_NEEDED` state with the garbled name pre-filled in the correction modal (previously the job would silently start ripping with a wrong title).
- **NamePromptModal pre-fill**: when a job enters review due to an unreadable or merged-word label, the modal opens with `detected_title`, content type, and season number pre-populated — the user only needs to correct the show name.
- **Disc analyst static method** `_parse_disc_name()`: parses `"Show Title - Season N (Disc N)"` MakeMKV format into `(title, season)` tuple.
- 14 new unit tests in `tests/unit/test_disc_name_identification.py` covering extractor CINFO parsing, analyst disc-name parsing, identification coordinator fallback logic, and review gate behavior.

### Fixed
- **CINFO vs DINFO**: extractor was reading `DINFO:6` (which doesn't exist in MakeMKV robot-mode output) instead of `CINFO:2`. This meant the disc display name was never captured, so the TMDB disc-name fallback never fired for any disc.
- **Scraper timeouts**: Addic7ed and OpenSubtitles.org request timeouts reduced from 30 s to 8 s so failures are fast when those sites block requests.
- **Simulation service**: `insert_disc_from_staging` no longer crashes when `staging_path` contains paths with non-standard separators.

### Changed
- Subtitle download strategy: OpenSubtitles.com REST API is tried first (entire season at once); only falls back to per-episode scraping if credentials are absent or the API call fails.
- `SRT` validation (`is_valid_srt_file`) now deletes and re-downloads cached files that contain HTML (Cloudflare challenge pages) rather than surfacing them as valid subtitles.
- `opensubtitlescom>=0.1.0` added to backend dependencies.

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

# TheDiscDB Contribution Pipeline — Design Spec

## Context

Engram already has a read-only TheDiscDB integration (content hash lookup, episode pre-assignment). The creator of TheDiscDB (lfoust) has expressed interest in receiving disc data from Engram users to expand the database. The current TheDiscDB submission process has a high barrier (requires UPC, images, etc.), so an automated pipeline from Engram would significantly lower friction.

**Goal**: Build the Engram-side data collection, consent flow, and JSON export pipeline. TheDiscDB does not yet have a submission API — exports will be written to a local directory for manual sharing during the co-development phase. When lfoust provides an API, the export step can be swapped for an HTTP call.

**Non-goals**: TheDiscDB API design, submission queue/retry logic, real-time sync.

---

## 1. Data Collection Layer

### Existing Data (no changes needed)

| Field | Source | Model |
|-------|--------|-------|
| Disc hash (content_hash) | `compute_content_hash()` | `DiscJob.content_hash` |
| Volume label | Drive detection | `DiscJob.volume_label` |
| Content type (tv/movie) | Classification | `DiscJob.content_type` |
| TMDB ID | TMDB classifier | Available in pipeline |
| Season | Matching | `DiscJob.detected_season` |
| Episode assignments | Matching | `DiscTitle.matched_episode` |
| Duration | MakeMKV scan (attr_id 9) | `DiscTitle.duration_seconds` |
| File size | MakeMKV scan (attr_id 10) | `DiscTitle.file_size_bytes` |
| Chapter count | MakeMKV scan (attr_id 8) | `DiscTitle.chapter_count` |

### New Fields to Capture

| Field | MakeMKV attr_id | Added to |
|-------|-----------------|----------|
| Source filename (e.g., `00001.m2ts`) | 16 | `TitleInfo` + `DiscTitle.source_filename` |
| Segment count | 25 | `TitleInfo` + `DiscTitle.segment_count` |
| Segment map (e.g., `"1,2,3"`) | 26 | `TitleInfo` + `DiscTitle.segment_map` |

**Changes**:
- `TitleInfo` dataclass (`analyst.py`): add `source_filename: str = ""`, `segment_count: int = 0`, `segment_map: str = ""`
- `DiscTitle` model (`disc_job.py`): add same three columns
- `Extractor._parse_disc_info()` (`extractor.py`): parse attr_ids 16, 25, 26 in the existing TINFO loop
- Database migration: `_migrate_schema()` adds the three new columns via `ALTER TABLE ADD COLUMN`

### MakeMKV Log Capture

During `scan_disc()` and `rip_title()`, write the full stdout/stderr to:
- `~/.engram/logs/makemkv/<job_id>_scan.log`
- `~/.engram/logs/makemkv/<job_id>_rip_<title_idx>.log`

This data is captured opportunistically (we already have the output in memory). Logs are referenced in the export JSON by filename, not embedded inline.

---

## 2. Consent Flow & Configuration

### Three Contribution Tiers

| Tier | Label | Data Included | User Effort |
|------|-------|--------------|-------------|
| 1 | Don't share | Nothing exported | None |
| 2 | Share automatically | Disc hash, all track metadata, season/episode, TMDB ID, MakeMKV logs | None (auto-collected) |
| 3 | Full contribution | Everything in tier 2 + UPC code + front/back disc images | User enters UPC, uploads photos |

### Configuration

New fields on `AppConfig`:
- `discdb_contributions_enabled: bool = False` — opt-in, separate from `discdb_enabled`
- `discdb_contribution_tier: int = 2` — default to auto-share when enabled
- `discdb_export_path: str | None = None` — override export directory (default: `~/.engram/discdb-exports/`)

Exposed in ConfigWizard as a new section under the existing TheDiscDB settings.

### Per-Job Behavior

- No post-completion prompts or modals. Contribution happens on a dedicated `/contribute` page.
- When tier 2+ is enabled, exports happen automatically on job completion.
- Users can visit `/contribute` to skip specific jobs, enhance to tier 3, or review what was exported.

### Tier 3 Storage

- `DiscJob.upc_code: str | None` — new column
- Images stored at `~/.engram/submissions/<job_id>/front.jpg`, `back.jpg`
- Cleaned up after successful export generation (copied into export directory)

---

## 3. UI — Contribution Page

### Route: `/contribute`

Accessible from main nav (alongside Dashboard, History).

**Nav badge**: Shows count of pending (unexported, non-skipped) completed jobs, e.g., "Contribute (3)".

### Layout

- **Header**: Brief explanation of TheDiscDB and why contributing helps
- **Job list table**: Completed jobs with columns:
  - Volume Label
  - Content Type badge
  - Detected Title
  - Completed Date
  - Export Status (pending / exported / skipped)
- **Per-job actions**:
  - "Export" — generates JSON for tier 2 data
  - "Enhance & Export" — expands inline with UPC input + image upload, then exports (tier 3)
  - "Skip" — marks job as won't contribute
- **Bulk action**: "Export All Pending" button

### Auto-Export Behavior

When `discdb_contributions_enabled = true` and `discdb_contribution_tier >= 2`:
- JSON export is generated automatically when a job reaches COMPLETED state
- The `/contribute` page shows these as "exported" with the option to enhance to tier 3
- Nav badge reflects only non-exported, non-skipped jobs

### When Disabled

If `discdb_contributions_enabled = false`, the page shows a setup prompt linking to ConfigWizard.

---

## 4. JSON Export Schema

### File Organization

```
~/.engram/discdb-exports/
  <content_hash>/
    disc_data.json
    makemkv_scan.log
    makemkv_rip_0.log
    makemkv_rip_1.log
    front.jpg          (tier 3 only)
    back.jpg           (tier 3 only)
```

Content hash as directory name enables easy deduplication and matches TheDiscDB's primary key.

### Schema (v1.0)

```json
{
  "engram_version": "0.4.4",
  "export_version": "1.0",
  "exported_at": "2026-04-04T19:30:00Z",
  "contribution_tier": 2,

  "disc": {
    "content_hash": "D7CAB58DAC87C58C46FDA35A33759839",
    "volume_label": "BAND_OF_BROTHERS_S1D1",
    "content_type": "tv",
    "disc_number": 1
  },

  "identification": {
    "tmdb_id": 4613,
    "detected_title": "Band of Brothers",
    "detected_season": 1,
    "classification_source": "discdb_hash_match",
    "classification_confidence": 0.98
  },

  "titles": [
    {
      "index": 0,
      "source_filename": "00001.m2ts",
      "duration_seconds": 4394,
      "size_bytes": 18405949440,
      "chapter_count": 12,
      "segment_count": 1,
      "segment_map": "1",
      "title_type": "Episode",
      "matched_episode": "S01E01",
      "match_confidence": 0.99,
      "match_source": "discdb",
      "edition": null
    }
  ],

  "upc": null,
  "images": [],

  "makemkv_logs": {
    "scan_log": "makemkv_scan.log",
    "rip_logs": ["makemkv_rip_0.log", "makemkv_rip_1.log"]
  }
}
```

### title_type Derivation

- If DiscDB mappings exist for the job → use `DiscDbTitleMapping.title_type` (`"Episode"`, `"MainMovie"`, `"Extra"`, `""`)
- If no DiscDB mappings → derive from Engram's data:
  - For TV: `"Episode"` for matched titles, `"Extra"` for `is_extra=True`
  - For Movies: `"MainMovie"` for the selected/largest title, `"Extra"` for `is_extra=True`
  - Unclassified: `null`

### Movie Example (key differences)

```json
{
  "disc": {
    "content_type": "movie"
  },
  "identification": {
    "detected_season": null
  },
  "titles": [
    {
      "title_type": "MainMovie",
      "matched_episode": null,
      "edition": "Theatrical"
    },
    {
      "title_type": "Extra",
      "matched_episode": null,
      "edition": null
    }
  ]
}
```

---

## 5. Backend Pipeline

### New Module: `backend/app/core/discdb_exporter.py`

**Functions**:
- `generate_export(job: DiscJob, titles: list[DiscTitle], config: AppConfig) -> Path` — assembles JSON, copies logs, writes to export directory
- `get_export_directory(config: AppConfig) -> Path` — returns configured or default export path
- `get_pending_exports(session) -> list[DiscJob]` — completed jobs not yet exported
- `mark_exported(job_id: int, session)` — sets `exported_at` timestamp

### New DiscJob Column

- `exported_at: datetime | None` — tracks export status

### Auto-Export Hook

In `JobStateMachine.on_terminal_state()` callback (already used for staging cleanup):
- When state is COMPLETED and `discdb_contributions_enabled` is on → call `generate_export()`
- Set `exported_at` on success

### New API Routes (under `/api/contributions`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/contributions` | List completed jobs with export status |
| POST | `/api/contributions/{job_id}/export` | Manually trigger export |
| POST | `/api/contributions/{job_id}/skip` | Mark as won't contribute |
| POST | `/api/contributions/{job_id}/enhance` | Accept tier-3 data, re-export |
| GET | `/api/contributions/stats` | Counts for nav badge |

---

## 6. Files to Create/Modify

### New Files
- `backend/app/core/discdb_exporter.py` — export generation module
- `frontend/src/components/ContributePage.tsx` — contribution page component
- `tests/unit/test_discdb_exporter.py` — unit tests
- `tests/integration/test_discdb_contribution.py` — integration tests
- `tests/pipeline/test_export_schema.py` — snapshot tests

### Modified Files
- `backend/app/core/analyst.py` — add 3 fields to `TitleInfo`
- `backend/app/core/extractor.py` — parse attr_ids 16, 25, 26; capture MakeMKV logs
- `backend/app/models/disc_job.py` — add columns to `DiscTitle` and `DiscJob`
- `backend/app/models/app_config.py` — add contribution config fields
- `backend/app/services/job_manager.py` — auto-export hook in terminal state callback
- `backend/app/api/routes.py` — new contribution API routes
- `backend/app/database.py` — migration for new columns
- `frontend/src/app/App.tsx` — add `/contribute` route and nav link with badge
- `frontend/src/components/ConfigWizard.tsx` — contribution settings section

---

## 7. Testing Strategy

### Unit Tests (`tests/unit/test_discdb_exporter.py`)
- TV disc export produces correct JSON structure
- Movie disc export with MainMovie/Extra types
- New fields (segment_count, segment_map, source_filename) present
- Schema includes version fields
- Export directory creation
- `mark_exported()` sets timestamp
- No export when `discdb_contributions_enabled = False`

### Integration Tests (`tests/integration/test_discdb_contribution.py`)
- Simulate disc → complete → verify JSON file exists (auto-export)
- Manual export via POST `/api/contributions/{job_id}/export`
- Enhance with UPC via POST endpoint
- Contribution stats endpoint returns correct counts

### Pipeline Tests (`tests/pipeline/test_export_schema.py`)
- Snapshot validation of TV and movie JSON export structures

---

## 8. Verification Plan

1. **Data capture**: Run backend with DEBUG=true, simulate a TV disc, verify new fields in DB
2. **Auto-export**: Complete a simulated job, verify JSON file appears in `~/.engram/discdb-exports/`
3. **JSON validation**: Inspect generated JSON matches the schema above
4. **UI**: Navigate to `/contribute`, verify job list, test export/skip/enhance buttons
5. **Config**: Toggle contribution settings in ConfigWizard, verify behavior changes
6. **Share with lfoust**: Send a sample JSON export for feedback on the data format

# TheDiscDB Integration — 3-Wave Implementation Plan

## Context

During live testing of the TheDiscDB contribution pipeline (issue #60), we discovered several problems:
- DiscDB matches can be wrong (commentary tracks falsely matched as episodes)
- No way to see which source (DiscDB vs Engram) provided a match
- No way to re-run Engram's matcher when DiscDB data is wrong
- No way to manually reassign episodes for already-matched titles
- No batch submit for grouped multi-disc releases
- Matcher false positives from commentary tracks (fixed by increasing scan points to 10)

Six issues filed: #93-#98. This plan implements all six in 3 dependency-ordered waves on `feat/discdb-integration`.

## Dependency Graph

```
Wave 1: Foundation (#93 backend, #95)
  └── match_source + discdb_match_details columns
  └── Flag incorrect DiscDB data endpoint
         │
Wave 2: Re-Match + Review (#94, #97, #98)  
  └── Per-title and bulk re-match endpoints
  └── Episode reassignment for matched titles
         │
Wave 3: UI + Batch Submit (#93 frontend, #96)
  └── Source badges + toggle in TrackGrid
  └── Batch submit for release groups
```

---

## Wave 1: Foundation (Match Source Storage + Data Quality Flag)

**Issues:** #93 (backend), #95  
**Branch:** `feat/discdb-integration`

### Data Model Changes

**File:** `backend/app/models/disc_job.py` — Add to `DiscTitle`:

```python
match_source: str | None = Field(default=None)          # "discdb", "engram", "user"
discdb_match_details: str | None = Field(default=None)   # DiscDB match preserved separately
discdb_flagged: bool = Field(default=False)              # User flagged DiscDB data as incorrect
discdb_flag_reason: str | None = Field(default=None)     # Reason for flag
```

All nullable/defaulted — no data migration needed, `_migrate_schema()` handles new columns automatically.

### Backend Changes

**`backend/app/services/matching_coordinator.py`:**
- In `try_discdb_assignment()` (~line 75-118): set `title.match_source = "discdb"` and `title.discdb_match_details = title.match_details`
- In `_match_single_file_inner()` (~line 305): set `title.match_source = "engram"` after match result

**`backend/app/api/routes.py`:**
- Add `match_source`, `discdb_match_details`, `discdb_flagged`, `discdb_flag_reason` to `TitleResponse` (line 55-77)
- New endpoint: `POST /jobs/{job_id}/flag-discdb` with `FlagDiscDBRequest(title_id, reason, details)`
- New model: `FlagDiscDBRequest(BaseModel)` with `title_id: int`, `reason: str`, `details: str | None`

### TDD Test Plan

**Write first:** `backend/tests/unit/test_match_source.py`
- `try_discdb_assignment` sets `match_source = "discdb"` and populates `discdb_match_details`
- Engram matching sets `match_source = "engram"`
- DiscDB match stored separately in `discdb_match_details` field

**Write first:** `backend/tests/integration/test_discdb_flag.py`
- `POST /jobs/{id}/flag-discdb` returns 200, persists flag to DB
- Flag nonexistent title → 404
- Flag persists `discdb_flagged=True` and `discdb_flag_reason`
- `GET /api/jobs/{id}/titles` includes new fields in response

### Verification
1. `uv run pytest tests/unit/test_match_source.py` — all pass
2. `uv run pytest tests/integration/test_discdb_flag.py` — all pass  
3. `uv run pytest` — no regressions
4. `uv run ruff check . && uv run ruff format --check .` — clean
5. Manual: simulate disc, check `/api/jobs/{id}/titles` includes `match_source`

---

## Wave 2: Re-Match + Review Enhancement

**Issues:** #94, #97, #98  
**Prerequisites:** Wave 1 merged

### New API Endpoints

**Per-title re-match** (`POST /jobs/{job_id}/titles/{title_id}/rematch`):
- Request: `RematchRequest(source_preference: str | None)` — "discdb", "engram", or None
- If `source_preference == "discdb"`: restore from stored `discdb_match_details`
- If `source_preference == "engram"`: clear current match, re-run audio fingerprinting
- If None: run full pipeline (DiscDB first, Engram fallback)
- Requires ripped file in staging; return 400 if not found
- Allowed in states: REVIEW_NEEDED, COMPLETED (if staging exists), MATCHING

**Bulk disc re-match** (`POST /jobs/{job_id}/rematch`):
- Same request model as per-title
- Reuses existing `_rerun_matching()` in `job_manager.py` (lines 385-429) with added `source_preference` param
- Resets all selected titles, re-triggers matching

**Episode reassignment** (`POST /jobs/{job_id}/titles/{title_id}/reassign`):
- Request: `ReassignRequest(episode_code: str, edition: str | None)`
- Sets `matched_episode`, `match_confidence = 1.0`, `match_source = "user"`
- Works regardless of job state (except ORGANIZING or terminal FAILED)
- Does NOT trigger re-organization — just updates the DB
- Broadcasts title update via WebSocket

### Backend Changes

**`backend/app/services/matching_coordinator.py`:**
- New method: `rematch_single_title(job_id, title_id, source_preference)` 
- Handles 3 source modes: restore DiscDB, re-run Engram, or auto

**`backend/app/services/job_manager.py`:**
- Modify `_rerun_matching(job_id)` to accept optional `source_preference` parameter
- New method: `reassign_episode(job_id, title_id, episode_code, edition)`
- Delegation: `rematch_single_title()` → `matching_coordinator.rematch_single_title()`

**`backend/app/api/routes.py`:**
- Relax `submit_review` guard (line 548) OR add separate `/reassign` endpoint (recommended — cleaner separation)
- 3 new endpoints as described above

### Frontend Changes

**`frontend/src/components/ReviewQueue.tsx`:**
- Add "Re-Match" button per title in auto-matched section
- Add "Re-Match All" button in header
- Make auto-matched titles editable (episode dropdown + confirm)
- Add "Edit" toggle on TVTitleCard that reveals EpisodeSelector

### TDD Test Plan

**Write first:** `backend/tests/unit/test_rematch.py`
- `rematch_single_title` with `source_preference="discdb"` applies stored `discdb_match_details`
- `rematch_single_title` with `source_preference="engram"` clears DiscDB, sets state to MATCHING
- `rematch_single_title` returns error when file not found in staging
- `reassign_episode` updates `matched_episode`, `match_source = "user"`, `match_confidence = 1.0`

**Write first:** `backend/tests/integration/test_rematch.py`
- `POST /jobs/{id}/titles/{tid}/rematch` → 200, title state → MATCHING
- `POST /jobs/{id}/rematch` → 200, all titles reset
- `POST /jobs/{id}/titles/{tid}/reassign` → 200, episode updated
- Rematch without staging files → 400
- Rematch preserves `discdb_match_details` when switching to Engram

**Write first:** `frontend/e2e/rematch-flow.spec.ts`
- Re-match button visible on review page
- Episode reassignment dropdown works for matched titles

### Verification
1. `uv run pytest tests/unit/test_rematch.py` — all pass
2. `uv run pytest tests/integration/test_rematch.py` — all pass
3. `uv run pytest` — no regressions
4. `npm run build` — no TypeScript errors
5. Manual: simulate disc → review → re-match title → verify re-processing
6. Manual: reassign auto-matched title → verify DB update

---

## Wave 3: UI Source Display + Batch Submit

**Issues:** #93 (frontend), #96  
**Prerequisites:** Wave 1+2 merged

### Match Source Badges (Frontend)

**`frontend/src/app/components/TrackGrid.tsx`:**
- Add source badge per track: blue "DISCDB", purple "ENGRAM", green "MANUAL"
- Style consistent with existing quality badges

**`frontend/src/app/components/DiscCard.tsx`:**
- Add `matchSource` to Track interface
- Map from API response `match_source` field

### Source Toggle

**`frontend/src/components/ReviewQueue/TVTitleCard.tsx`:**
- When title has both `discdb_match_details` and `match_details` from Engram, show toggle
- Toggle calls `POST /api/jobs/{id}/titles/{tid}/rematch` with `source_preference`

### Batch Submit

**`backend/app/api/routes.py`:**
- New endpoint: `POST /contributions/release-group/{release_group_id}/submit`
- Queries all jobs in group, calls `submit_job()` for each sequentially
- Returns: `{"submitted": N, "failed": N, "results": [...]}`

**`backend/app/core/discdb_submitter.py`:**
- New function: `submit_release_group(jobs, titles_by_job, config, app_version)` → `list[SubmissionResult]`

**`frontend/src/components/ContributePage.tsx`:**
- Add "Submit Group" button per release group
- Show progress during batch submit ("Submitting 2/4...")
- After completion, show single "Continue on TheDiscDB" link

### TDD Test Plan

**Write first:** `backend/tests/unit/test_batch_submit.py`
- `submit_release_group` with mock httpx — all succeed
- Batch submit handles partial failures
- Empty release group → error

**Extend:** `backend/tests/integration/test_discdb_contribution.py`
- `POST /contributions/release-group/{id}/submit` with 2 completed jobs
- Batch submit updates `submitted_at` for successful jobs
- Batch submit when one job lacks content_hash → partial success

**Write first:** `frontend/e2e/match-source-display.spec.ts`
- Source badge appears on track cards
- DiscDB badge blue, Engram purple, Manual green

**Write first:** `frontend/e2e/batch-submit.spec.ts`
- "Submit Group" button appears for grouped discs
- Clicking submits all jobs in group

### Verification
1. `uv run pytest tests/unit/test_batch_submit.py` — all pass
2. `uv run pytest tests/integration/test_discdb_contribution.py` — all pass
3. `uv run pytest` — no regressions
4. `npm run build && npm run lint` — clean
5. Manual: view disc with DiscDB matches → blue badge visible
6. Manual: group 2 discs → Submit Group → both submitted

---

## Session Handoff Instructions

Each wave is a separate fresh session. Provide this context:

### For all sessions:
- Branch: `feat/discdb-integration` 
- Read `CLAUDE.md` for project conventions
- Read this spec at `docs/superpowers/specs/2026-04-09-discdb-integration-design.md`
- TDD: write tests FIRST, then implement to make them pass
- Run `uv run ruff check . && uv run ruff format .` before committing

### Wave 1 session context:
- Read: `backend/app/models/disc_job.py`, `backend/app/services/matching_coordinator.py` (lines 75-118), `backend/app/api/routes.py` (lines 55-77)
- Start with: `tests/unit/test_match_source.py`, `tests/integration/test_discdb_flag.py`
- Commit message prefix: `feat(discdb): `

### Wave 2 session context:
- Verify Wave 1 columns exist in model
- Read: `backend/app/services/job_manager.py` (lines 385-429 `_rerun_matching`), `frontend/src/components/ReviewQueue.tsx`
- Start with: `tests/unit/test_rematch.py`, `tests/integration/test_rematch.py`

### Wave 3 session context:
- Verify Wave 1+2 changes exist
- Read: `frontend/src/app/components/TrackGrid.tsx`, `frontend/src/components/ContributePage.tsx`, `backend/app/core/discdb_submitter.py`
- Start with: `tests/unit/test_batch_submit.py`, `frontend/e2e/match-source-display.spec.ts`

---

## Summary

| Wave | Issues | New Columns | New Endpoints | Test Files |
|------|--------|-------------|---------------|------------|
| 1 | #93b, #95 | 4 on DiscTitle | 1 | 2 new |
| 2 | #94, #97, #98 | 0 | 3 | 3 new |
| 3 | #93f, #96 | 0 | 1 | 4 new |
| **Total** | **6** | **4** | **5** | **9 new** |

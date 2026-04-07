# Disc Re-Identification System

**Date**: 2026-04-07
**Issues**: #57, #86
**Status**: Design

## Context

Users reported two disc misidentification bugs:
- **#57 (Thunderbirds)**: DVD with 4 ~48min episodes classified as movie because TMDB matched "Thunderbird" (movie) over "Thunderbirds" (TV show). Heuristics detected TV but TMDB overrode at 56% confidence. User had no way to correct this.
- **#86 (WandaVision)**: Label `MARVEL_STUDIOS_WANDAVISION_D1` matched to "Marvel Studios Assembled" instead of "WandaVision". Correct type (TV) but wrong show, so matching produced wrong results.

Both share the same root problem: **once identification is done, users have no recourse to correct it**. The state machine is one-way (no path back to IDENTIFYING), and the review UI only supports episode/edition selection, not title or content type changes.

## Design

### 1. Classifier Fix: Respect Strong Heuristic Signals

**File**: `backend/app/core/analyst.py` (`_apply_tmdb_signal`, line 342)

When heuristics have high confidence (>=0.75) and TMDB disagrees:
- **Do NOT override content_type** - keep the heuristic classification
- **Always flag for review** regardless of TMDB confidence
- Set review_reason explaining the conflict

When heuristics have low confidence (<0.75) and TMDB disagrees:
- Current behavior (TMDB overrides) is appropriate
- Still flag for review if override confidence < 0.60

### 2. TMDB Name Similarity Fix

**File**: `backend/app/core/tmdb_classifier.py` (`_name_similarity`, line 23)

Current Jaccard tokenization treats "Thunderbird" and "Thunderbirds" as completely different tokens (similarity = 0.0). Fix by adding fuzzy token matching:
- If two tokens differ by <=2 characters and one is a prefix of the other, count as 0.8 match
- This makes "Thunderbird" vs "Thunderbirds" score ~0.8 instead of 0.0

### 3. Volume Label Parsing: Strip Studio Prefixes

**File**: `backend/app/core/analyst.py` (`_parse_volume_label`)

Add a list of known studio prefixes to strip from volume labels before title extraction:
- `MARVEL_STUDIOS_`, `WARNER_BROS_`, `UNIVERSAL_`, `PARAMOUNT_`, `DISNEY_`, `20TH_CENTURY_`, `COLUMBIA_`, `LIONSGATE_`, `MGM_`, `DREAMWORKS_`
- Apply before the existing name parsing logic
- Only strip if there's remaining content after the prefix

### 4. State Machine: Allow Re-Identification

**File**: `backend/app/services/job_state_machine.py`

Add new valid transitions:
```python
JobState.REVIEW_NEEDED: {
    JobState.IDENTIFYING,  # NEW: re-identify with corrected title
    JobState.RIPPING,
    JobState.MATCHING,     # NEW: re-match with corrected metadata
    JobState.COMPLETED,
    JobState.FAILED,
},
```

### 5. Backend API: Re-Identify Endpoint

**File**: `backend/app/api/routes.py`

New endpoint: `POST /api/jobs/{job_id}/re-identify`

Request body:
```json
{
  "title": "Thunderbirds",
  "content_type": "tv",
  "season": 4,
  "tmdb_id": 12345
}
```

- `title` (required): User-provided correct title
- `content_type` (required): "tv" or "movie"
- `season` (optional): Season number for TV
- `tmdb_id` (optional): If provided from TMDB search picker, skip fuzzy TMDB search

Behavior:
1. Validate job is in REVIEW_NEEDED (pre-rip or post-rip)
2. Update job: `detected_title`, `content_type`, `detected_season`, optionally `tmdb_id`
3. Transition job to IDENTIFYING
4. Re-run identification pipeline with user-provided metadata as authoritative input
5. If `tmdb_id` provided, skip TMDB search and use it directly
6. Pipeline resumes: IDENTIFYING -> RIPPING (if pre-rip) or IDENTIFYING -> MATCHING (if already ripped)

### 6. Backend API: TMDB Search Proxy

**File**: `backend/app/api/routes.py`

New endpoint: `GET /api/tmdb/search?query=thunderbirds`

Response:
```json
{
  "results": [
    {
      "tmdb_id": 1234,
      "name": "Thunderbirds",
      "type": "tv",
      "year": "1965",
      "poster_path": "/abc.jpg",
      "popularity": 42.5
    }
  ]
}
```

- Searches both TMDB TV and movie endpoints
- Merges and deduplicates results
- Sorts by name similarity to query, then popularity
- Returns top 10 results
- Requires TMDB API key configured (returns 400 if missing)

### 7. Frontend: "Wrong Title?" Modal with TMDB Search

**Files**: New component `frontend/src/components/ReIdentifyModal.tsx`, modifications to `DiscCard.tsx` and `ReviewQueue.tsx`

When a job is in REVIEW_NEEDED state, add a "Wrong title?" button. Clicking opens a modal with:

**Quick Fix section**:
- Content type toggle (TV / Movie buttons)
- Title text input (pre-filled with current detected_title)
- Season number input (shown only when TV selected)

**TMDB Search section**:
- Search input with debounced API call to `/api/tmdb/search`
- Results list showing: poster thumbnail, title, year, type badge (TV/Movie)
- Clicking a result auto-fills the quick fix fields + sets tmdb_id

**Submit button**: Calls `POST /api/jobs/{job_id}/re-identify` with collected data

Styling: Consistent with existing modals (NamePromptModal, ConfigWizard). Cyberpunk theme.

### 8. WebSocket Events

No new event types needed. The existing `job_update` event fires on every state transition, so the frontend automatically updates when:
- Job transitions REVIEW_NEEDED -> IDENTIFYING (shows "Identifying..." state)
- Job transitions IDENTIFYING -> RIPPING or back to REVIEW_NEEDED

### 9. IdentificationCoordinator Changes

**File**: `backend/app/services/identification_coordinator.py`

Add a `re_identify()` method that:
- Accepts user-provided title, content_type, season, optional tmdb_id
- If tmdb_id provided: creates TmdbSignal directly (no search)
- If no tmdb_id: runs TMDB search with user-provided title (better than original label)
- Runs analyst with user-provided content_type as strong prior
- Determines whether to proceed to RIPPING (pre-rip) or MATCHING (post-rip, files exist in staging)
- Post-rip re-identification preserves existing ripped files — only re-runs matching/organizing with corrected metadata

### 10. Testing

**Unit tests**:
- `_apply_tmdb_signal()` with high-confidence heuristic disagreement -> review flagged
- `_name_similarity()` with "Thunderbird" vs "Thunderbirds" -> reasonable similarity (>0.5)
- Re-identify endpoint with REVIEW_NEEDED job -> success
- Re-identify endpoint with non-REVIEW_NEEDED job -> 400 error
- TMDB search proxy returns merged results
- Volume label parsing strips studio prefixes

**Integration tests**:
- Thunderbirds scenario: insert disc -> misidentified -> re-identify -> correct flow
- WandaVision scenario: insert disc -> wrong title -> re-identify with TMDB ID -> correct

**E2E tests**:
- "Wrong title?" button visible on review-needed jobs
- TMDB search picker shows results
- Re-identification flow updates UI in real-time

## Verification

1. `cd backend && uv run pytest` - all tests pass
2. `cd backend && uv run ruff check . && uv run ruff format .` - lint clean
3. `cd frontend && npm run build` - TypeScript passes
4. Manual simulation test:
   ```bash
   curl -X POST localhost:8000/api/simulate/insert-disc \
     -H "Content-Type: application/json" \
     -d '{"volume_label":"THUNDERBIRDS4","content_type":"tv","simulate_ripping":true}'
   # Wait for review_needed state
   curl -X POST localhost:8000/api/jobs/1/re-identify \
     -H "Content-Type: application/json" \
     -d '{"title":"Thunderbirds","content_type":"tv","season":4}'
   ```

## Files to Modify

| File | Change |
|------|--------|
| `backend/app/core/analyst.py` | Fix `_apply_tmdb_signal` override logic, add studio prefix stripping to `_parse_volume_label` |
| `backend/app/core/tmdb_classifier.py` | Fix `_name_similarity` for single-token fuzzy matching |
| `backend/app/services/job_state_machine.py` | Add REVIEW_NEEDED -> IDENTIFYING and REVIEW_NEEDED -> MATCHING transitions |
| `backend/app/services/identification_coordinator.py` | Add `re_identify()` method |
| `backend/app/api/routes.py` | Add `POST /api/jobs/{job_id}/re-identify` and `GET /api/tmdb/search` endpoints |
| `frontend/src/components/ReIdentifyModal.tsx` | New component: title correction + TMDB search picker |
| `frontend/src/components/DiscCard.tsx` or `ReviewQueue.tsx` | Add "Wrong title?" button trigger |
| `frontend/src/app/hooks/useJobManagement.ts` | Add `reIdentifyJob()` API call |
| `backend/tests/unit/test_analyst.py` | Tests for classifier fix |
| `backend/tests/unit/test_tmdb_classifier.py` | Tests for similarity fix |
| `backend/tests/integration/test_re_identify.py` | Integration tests for re-identification flow |

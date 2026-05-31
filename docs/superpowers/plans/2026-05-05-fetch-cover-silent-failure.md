# Fix: fetch-cover silent failure breaks DiscDB image upload

**Date:** 2026-05-05
**Status:** Open — discovered during the For All Mankind S1 4-disc end-to-end test
**Branch context:** `feat/discdb-release-image-upload` (image upload code is correct; the bug is upstream of it)

## Symptom

User completed the full TheDiscDB submission flow for a 4-disc release group. All 4 disc payloads + scan logs landed at TheDiscDB. The "Submit group" banner showed `4 submitted, 0 failed`. But the resulting contribution page on TheDiscDB rendered `Front cover —` and `Back cover —` empty.

User had clicked "Fetch cover" in the EnhanceWizard for one of the discs and seen no error — they assumed it worked.

## Root cause

**No cover image ever made it to disk.** Verified by inspecting all four export dirs at `~/.engram/discdb-exports/{content_hash}/`: only `disc_data.json` and `makemkv_scan.log` present, no `cover.jpg`/`cover.png`. The image-upload code (`_find_release_image_files` + `_upload_release_images` in `app/core/discdb_submitter.py`) correctly looked for files, found none, and returned `{}` — working as designed.

Two layered bugs caused the cover to never reach the export dir:

### Bug 1: backend inconsistent path handling

`backend/app/api/routes.py:1711-1713` (the `fetch-cover` route):

```python
config = await get_db_config()
export_base = Path(config.discdb_export_path) if config.discdb_export_path else None
if not export_base:
    raise HTTPException(status_code=400, detail="No export path configured")
```

When `app_config.discdb_export_path` is empty string (the default), this returns `400`. **Inconsistent with the rest of the codebase** — every other call site uses `app.core.discdb_exporter.get_export_directory()`, which falls back to `~/.engram/discdb-exports/`. The fetch-cover route is the lone outlier.

The user's config has `discdb_export_path=""`, so every "Fetch cover" click returned `400 No export path configured` server-side.

### Bug 2: frontend silently swallows the error

`frontend/src/components/EnhanceWizard.tsx:179-192`:

```typescript
const handleFetchCover = async () => {
  if (!selectedImage) return;
  setCoverSaving(true);
  try {
    const res = await fetch(`/api/contributions/${job.id}/fetch-cover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_url: selectedImage }),
    });
    if (res.ok) setCoverSaved(true);
    // ← no else clause, no error state, no toast
  } finally {
    setCoverSaving(false);
  }
};
```

A 400 response looked indistinguishable from success — the button stopped spinning, no UI changed, no error appeared. Compare to `handleUpcLookup` in the same file, which has a `setLookupError` path.

### Bug 3 (instrumentation gap)

`_upload_release_images` in `app/core/discdb_submitter.py` returns `{}` silently when no covers are found. There's no log line indicating "scanned dirs X, Y, Z; no front/back covers found." If we'd had that line, the silent fetch-cover failure would have been visible in logs immediately. Working as designed, but unhelpful for diagnosis.

## Why my image-upload code didn't surface the issue

It's strictly downstream. Given the inputs it received (no cover files anywhere), returning `{}` and uploading nothing was correct behavior — submitting without cover art is a legitimate use case. The bug is in the data-gathering step (fetch-cover), not the submission step.

## Fixes

### 1. Use `get_export_directory()` in fetch-cover

`backend/app/api/routes.py:1697-1716` — replace inline path logic with the canonical helper:

```python
from app.core.discdb_exporter import get_export_directory

# ...
config = await get_db_config()
export_dir = get_export_directory(config) / job.content_hash
export_dir.mkdir(parents=True, exist_ok=True)
```

Drop the `400 No export path configured` HTTPException entirely — `get_export_directory` always returns a usable path.

### 2. Surface errors in `handleFetchCover`

`frontend/src/components/EnhanceWizard.tsx:179-192` — mirror the `handleUpcLookup` pattern. Add `coverError` state, render it inline near the Fetch cover button, and parse the error response body when `!res.ok`:

```typescript
const [coverError, setCoverError] = useState<string | null>(null);

const handleFetchCover = async () => {
  if (!selectedImage) return;
  setCoverSaving(true);
  setCoverError(null);
  try {
    const res = await fetch(`/api/contributions/${job.id}/fetch-cover`, { … });
    if (res.ok) {
      setCoverSaved(true);
    } else {
      const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      setCoverError(body.detail ?? "Cover fetch failed");
    }
  } catch (e) {
    setCoverError(e instanceof Error ? e.message : "Cover fetch failed");
  } finally {
    setCoverSaving(false);
  }
};
```

### 3. Log skipped image uploads

`backend/app/core/discdb_submitter.py` `_upload_release_images` — when the helper finds no images, log it once at INFO with the release_id and dirs scanned. This makes silent skips visible in `~/.engram/engram.log`.

```python
async def _upload_release_images(release_id, export_dirs, api_key, base_url):
    images = _find_release_image_files(export_dirs)
    if not any(images.values()):
        logger.info(
            f"No release cover images found for release {release_id} "
            f"(searched {len(list(export_dirs))} dirs); skipping image upload"
        )
        return {}
    # … existing upload loop
```

(Note: `export_dirs` is iterable; capture the count before the find call if iteration is destructive.)

### 4. (Optional) Echo the saved path on success

Have `fetch-cover` return `{"status": "saved", "filename": "cover.jpg", "path": str(filepath)}` and have `handleFetchCover` show "Saved to …/cover.jpg" as a positive confirmation. This makes success equally visible.

## Validation plan

After applying fixes 1+2:

1. `uv run pytest backend/tests/unit/test_discdb_*` should still pass (no test changes expected, but rerun to confirm).
2. With backend running and `DISCDB_ENABLED=True`, manually:
   - In the contribute UI, Enhance any pending disc.
   - Enter UPC, Lookup, pick a cover, click Fetch cover.
   - **Expect:** toast/inline message "Saved", and a `cover.jpg` appearing under `~/.engram/discdb-exports/{content_hash}/`.
   - **Expect on failure:** the inline error renders the backend's `detail` string (e.g., 400 / 502).
3. Group + Submit Group → `_upload_release_images` posts to `/api/engram/{releaseId}/images/front` with the image bytes. Verify on TheDiscDB contribute page that the cover renders.

## State of the For All Mankind submission

- All 4 discs at TheDiscDB with shared release_group_id `a1e5fa90...` — disc data + scan logs intact.
- No covers uploaded.
- After the fixes ship, two ways to add the cover:
  - Re-enhance one disc in the UI, then re-submit the group (TheDiscDB endpoint behavior on duplicate disc submit needs verification — may upsert or 409).
  - Manually `POST /api/engram/{releaseGroupId}/images/front` with `Content-Type: image/jpeg` once the cover is on disk. lfoust did not put the image endpoints behind auth, so a curl call against the existing release_group_id should work.

## Out of scope for this fix

- The `discdb_export_path` config field could be dropped entirely if no users set it manually — the default is good. Consider for cleanup later, not in this fix.
- Back-cover support: the UI only fetches a single front cover today. Adding `cover_back.jpg` flow is a separate feature.

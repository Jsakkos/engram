# Phase 3 Track B: Operationalize + Close Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. This plan runs **in parallel** with Track A (`2026-05-28-phase3-chromaprint-identification.md`); they share no code dependency. Track B seeds the catalog so Track A's identification has data to match at ship time.

**Goal:** Confirm the deployed fingerprint server is actually ingesting contributions, seed the canonical catalog (organically + a dev-promote path so Track A integration tests have real canonicals to hit), and close the two leftover Phase 1/2 deliverables — the privacy doc and the bootstrap library UI.

**Architecture:** Mostly operational verification plus two small build items. The catalog-seeding path reuses the existing Phase 1 `bootstrap_library.py` CLI and the server's real `runPromotion` pipeline (insert ≥3 independent contributions → promote). The privacy doc is a markdown deliverable enumerating the contribution schema. The bootstrap UI is a new React flow over a thin API layer wrapping the existing CLI helpers, built mock-first per `feedback_ui_mockups_first`.

**Tech Stack:** Python (`bootstrap_library.py`, FastAPI), Cloudflare Wrangler (D1 inspection, local dev), React + TypeScript (bootstrap UI), markdown (privacy doc).

---

## Cluster V: Verify ingestion + seed the catalog (operational)

### Task V1: Confirm the deployed server is reachable and ingesting

- [ ] **Step V1.1: Health-check the production worker**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://engram-fp-prod.jonathansakkos.workers.dev/v1/contribute -H "Content-Type: application/json" -d '{}'
```
Expected: `400` (schema rejects an empty body) — proves the route is live and validating, not 404/000.

- [ ] **Step V1.2: Drive a real local contribution end-to-end**

Per the `project_real_disc_testing_setup` memory: stop the released `engram.exe` (frees port 8000), run the worktree uvicorn with `DATABASE_URL` pointed at the real `backend/engram.db`, insert/rip a real disc (or use an already-ripped title), accept the JIT disclosure, and let `ContributionUploader` drain. Watch `~/.engram/cache/contribution_log.jsonl` for the upload entry.

- [ ] **Step V1.3: Confirm the row landed server-side**

```bash
cd /c/Github/engram-fingerprint-server
wrangler d1 execute engram-fingerprint --remote --command "SELECT id, tmdb_id, season, episode, match_source, poison_check FROM contribution ORDER BY id DESC LIMIT 5"
```
Expected: at least one recent row with `poison_check='pass'`. If empty, debug the uploader (disclosure gate, server URL, network) before proceeding.

### Task V2: Seed canonicals via the bootstrap CLI

- [ ] **Step V2.1: Dry-run the bootstrap CLI against the real TV library**

```bash
cd backend
uv run python -m app.scripts.bootstrap_library "C:/Users/jonat/Engram/TV" --dry-run 2>&1 | tail -30
```
Expected: a count of labeled MKVs that parse + resolve to TMDB IDs (`queued=N, skipped=M`). Note shows with the most episodes — those are the best canonical-seed candidates.

- [ ] **Step V2.2: Real bootstrap run (enqueues `match_source="bootstrap"` contributions)**

```bash
uv run python -m app.scripts.bootstrap_library "C:/Users/jonat/Engram/TV" 2>&1 | tail -30
```
Then let the uploader drain (or restart the backend to trigger it). Confirm rows reach the server as in V1.3 with `match_source='bootstrap'`.

- [ ] **Step V2.3: Understand the canonical bar**

CANONICAL requires `independent_count >= 3` (distinct `(pseudonym, disc_content_hash)` pairs) AND `mean_confidence >= 0.85` AND no flagged contributor (`promotion.ts:61-72`). A single user's bootstrap run is **one** pseudonym → it can only ever reach CANDIDATE. This is by design (anti-domination). So organic seeding is slow; Task V3 provides a dev-only path to fabricate canonicals for *testing* Track A.

### Task V3: Dev-only canonical seeding for Track A integration tests

**Files:**
- Create: `C:\Github\engram-fingerprint-server\scripts\dev_seed_canonical.ts`

- [ ] **Step V3.1: Write a local-D1 seed script**

This inserts real canonicals (with correct MinHash sketches) into a **local** dev D1 so `GET /v1/identify` and `GET /v1/pack` return real data during development. It reads chromaprints exported from the user's library (a JSON of `{tmdb_id, season, episode, hashes:[...]}` produced by a one-off `ChromaprintExtractor` run) and inserts via the same primitives the server uses.

```ts
// scripts/dev_seed_canonical.ts — run with: wrangler dev (then invoke via a temporary route)
// OR adapt into a vitest that writes to the local miniflare D1. Simplest: a vitest-style
// harness mirroring test/identify.test.ts's seedCanonical(), pointed at sample fixtures.
import { encodeZstdVarint } from "../src/codec";
import { minhash128 } from "../src/minhash";

export async function seedCanonical(db: D1Database, tmdb_id: number, season: number, episode: number, hashes: number[]) {
  const encoded = await encodeZstdVarint(hashes);
  const sketch = minhash128(hashes);
  await db.prepare(
    `INSERT OR REPLACE INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
     VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
  ).bind(tmdb_id, season, episode, encoded).run();
  await db.prepare(
    `INSERT OR REPLACE INTO canonical_sketch (tmdb_id, season, episode, sketch, hash_count, generated_at)
     VALUES (?, ?, ?, ?, ?, unixepoch())`,
  ).bind(tmdb_id, season, episode, sketch, hashes.length).run();
}
```

- [ ] **Step V3.2: Produce real fixture chromaprints from the local library**

```bash
cd backend
uv run python -c "
import asyncio, json
from app.matcher.chromaprint_extractor import ChromaprintExtractor
async def main():
    ex = ChromaprintExtractor(fpcalc_path='../spikes/chromaprint/bin/fpcalc.exe')
    files = {(95396,1,1): 'C:/Users/jonat/Engram/TV/Severance/Season 1/Severance - S01E01.mkv'}
    out = []
    for (t,s,e), path in files.items():
        r = await ex.extract(path)
        out.append({'tmdb_id': t, 'season': s, 'episode': e, 'hashes': r.hashes})
    open('../../engram-fingerprint-server/fixtures_canonical.json','w').write(json.dumps(out))
    print('wrote', len(out), 'fixtures')
asyncio.run(main())
"
```

- [ ] **Step V3.3: Build the local pack + verify identify locally**

```bash
cd /c/Github/engram-fingerprint-server
# After seeding canonicals into local D1 and running runPackBuilder locally:
pnpm dev   # local server at :8787
# In another shell, query identify with a fingerprint from the same episode:
curl -sS "http://localhost:8787/v1/identify?fp=<b64url-of-same-episode-window>&k=5" | jq
```
Expected: top candidate = the seeded episode with `hash_overlap_pct` near 1.0. This is the fixture data Track A's `test_chromaprint_cascade.py` and the manual e2e step rely on.

---

## Cluster D: Privacy documentation

### Task D1: `docs/development/fingerprint-privacy.md`

**Files:**
- Create: `docs/development/fingerprint-privacy.md`

- [ ] **Step D1.1: Enumerate the contribution schema field-by-field**

Write the doc covering, for every field actually sent on the wire (source of truth: `ContributionRequestSchema` in `engram-fingerprint-server/src/schemas.ts` + `contribution_uploader.py`): `wire_format_version`, `pseudonym` (per-install UUIDv4, rotatable, not tied to identity), `tmdb_id`/`season`/`episode`, `fingerprint_b64` (zstd-varint chromaprint hashes — a perceptual fingerprint, not audio), `fingerprint_sha256_b64` (dedup key), `disc_content_hash_b64`, `match_confidence`, `match_source`, `client_version`. For each: what it is and why it exists.

- [ ] **Step D1.2: Explicitly clarify the disc content hash**

State plainly: the disc content hash is an MD5 over the **m2ts file sizes** of the BluRay structure (per the TheDiscDB ContentHash definition) — it identifies a *disc release*, not the user's file or its contents. It cannot be reversed to anything the user owns.

- [ ] **Step D1.3: Document the privacy controls**

Cover: opt-out toggle (`enable_fingerprint_contributions`), JIT disclosure gate (`fingerprint_disclosure_accepted`), the local audit log (`~/.engram/cache/contribution_log.jsonl`), pseudonym rotation, and the `POST /api/fingerprint/forget` round-trip (server deletes raw rows; already-aggregated canonicals are immutable, per `forget.ts`). Note what is NOT sent: no filenames, no paths, no IP logging, no cross-query correlation.

- [ ] **Step D1.4: Link it from the developer docs index + commit**

Add a link from `docs/development/` index (and a one-liner in `README` privacy section if one exists).

```bash
git add docs/development/fingerprint-privacy.md
git commit -m "docs(fingerprint): privacy disclosure — every contribution field documented"
```

---

## Cluster U: Bootstrap library UI

Build mock-first per `feedback_ui_mockups_first`: present static direction mockups, let the user pick, then build production code. The UI needs a thin backend API wrapping the existing CLI helpers (`parse_episode_filename`, `walk_library`, `resolve_tmdb_id`, the enqueue path) — there is currently **no** bootstrap API surface (only `/api/fingerprint/contributions*`).

### Task U1: Mock the bootstrap flow (no production code)

- [ ] **Step U1.1: Build 2-3 static mockups** of the directory-pick → proposed-labels-table → accept/edit/skip flow (reusing the visual language of `ReviewQueue`/`TVTitleCard`). Save to `docs/design_handoff_synapse/explorations/`. Present to the user; capture the pick before writing production code.

### Task U2: Backend API for bootstrap scan + enqueue

**Files:**
- Modify: `backend/app/api/routes.py` (add endpoints near the other `/fingerprint/*` routes, ~line 1313)
- Test: `backend/tests/integration/test_bootstrap_api.py`

- [ ] **Step U2.1: Write failing tests** for `POST /api/fingerprint/bootstrap/scan` (body `{path}` → returns `[{file, show, season, episode, tmdb_id, proposed_label}]` using `walk_library` + `resolve_tmdb_id`) and `POST /api/fingerprint/bootstrap/accept` (body `{items:[...]}` → fingerprints each accepted file via `ChromaprintExtractor`, enqueues `match_source="bootstrap"` rows). Localhost-gated like the sibling routes (`Depends(require_localhost)`).

- [ ] **Step U2.2: Implement the endpoints** reusing `app.scripts.bootstrap_library` helpers (import `walk_library`, `parse_episode_filename`, `resolve_tmdb_id`) and the existing `ContributionQueue`. Throttle/limit scan size; never block on extraction (enqueue is fire-and-forget per accepted item).

- [ ] **Step U2.3: Run tests + commit.**

### Task U3: `BootstrapLibraryFlow.tsx`

**Files:**
- Create: `frontend/src/components/BootstrapLibraryFlow.tsx`
- Modify: `frontend/src/components/ConfigWizard.tsx` (surface an entry point)
- Test: `frontend/e2e/bootstrap-library.spec.ts`

- [ ] **Step U3.1: Build the component** per the chosen mockup: directory input → calls `/api/fingerprint/bootstrap/scan` → renders proposed labels in a review-queue-style table with per-row accept/edit/skip → calls `/api/fingerprint/bootstrap/accept` with the accepted set. Reuse existing review components and the Synapse panel primitives.

- [ ] **Step U3.2: Wire an entry point** in `ConfigWizard` (a "Contribute from existing library" action near the fingerprint settings).

- [ ] **Step U3.3: E2E test** the scan → accept round-trip with a temp fixture directory (Playwright, 2560×1440 per `feedback_playwright_viewport`). Run, then commit.

---

## Verification

- [ ] **Catalog seeded:** `wrangler d1 execute engram-fingerprint --remote --command "SELECT tier, COUNT(*) FROM episode_canonical GROUP BY tier"` shows rows (candidates from organic bootstrap; canonicals from dev-seed locally for Track A tests).
- [ ] **Privacy doc:** field list in `docs/development/fingerprint-privacy.md` matches `ContributionRequestSchema` exactly (no field present on the wire is undocumented).
- [ ] **Bootstrap UI:** scan a fixture directory in the UI, accept a few, confirm `fingerprint_contributions` rows appear with `match_source='bootstrap'` (GET `/api/fingerprint/contributions`).

---

## Risks / decisions

1. **Organic canonicals are slow** (one user = one pseudonym = CANDIDATE ceiling). Dev-seed (V3) unblocks Track A testing without waiting for real multi-user consensus. Do not ship dev-seeded canonicals to production.
2. **Bootstrap is TV-only** (the CLI resolves via TMDB *TV* search). The UI must communicate this; movie bootstrap is out of scope.
3. **Bootstrap extraction load:** fingerprinting thousands of files is CPU-heavy. The accept endpoint must throttle (the CLI already batches at 200); the UI should show progress and allow cancel.

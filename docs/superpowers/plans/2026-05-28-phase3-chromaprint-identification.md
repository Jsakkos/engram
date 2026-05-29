# Phase 3: Chromaprint Identification Implementation Plan (Track A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the collected chromaprint catalog *usable* — the server learns to answer "what episode is this windowed fingerprint?" and serve per-show packs, and the client runs chromaprint-first identification during rip matching, falling back to ASR and cross-validating the two.

**Architecture:** Server (`engram-fingerprint-server`, Cloudflare Worker + D1 + R2) gains `GET /v1/identify` (mirror image of the anti-poison screen — `screenAntiPoison` run to find the best *self*-match) and `GET /v1/pack/{tmdb_id}` (serve the R2 packs `PackBuilder` already writes, ETag-cached). Client gains pure scoring functions, a two-backend `ChromaprintMatcher` (local pack / remote identify), a pack downloader/cache, an additive extension to `calibrate_confidence`, and a chromaprint-first cascade in `EpisodeCurator.match_single_file` that reuses the existing windowed-voting machinery (`MatchCoverage`, `extract_audio_chunk`, `_attach_calibrated_confidence`).

**Tech Stack:** Server — TypeScript, Cloudflare Workers, D1 (SQLite), R2, Zod, `@bokuweb/zstd-wasm`, Vitest + `@cloudflare/vitest-pool-workers`. Client — Python 3.11+, `httpx`, `zstandard`, `fpcalc` (chromaprint 1.5.1), pytest.

**Critical design constraint (verified):** `episode_canonical.fingerprint` is an offset-less, deduplicated *hash set* (`promoteOne` keeps hashes appearing in ≥50% of contributors, discarding offsets and counts — `src/workers/promotion.ts:74-87`). Therefore: NO `hash_index` table, NO offset-based temporal alignment. "Temporal coherence" is redefined as **query-stream contiguity** (fpcalc emits an ordered stream; a true match is a contiguous run of reference-set members), and "rarity" is an **approximate IDF** from cross-episode document frequency. A real inverted index + wire-format-v2-with-offsets is deferred to Phase 4.

---

## File Structure

**Server new files (`C:\Github\engram-fingerprint-server`):**
- `src/db_identify.ts` — `screenIdentify`, `temporalCoherence`, `rarityWeightedOverlap`, `combinedScore`, `IdentifyCandidate`
- `src/routes/identify.ts` — `handleIdentify` (GET `?fp=<b64url>&k=5`)
- `src/routes/pack.ts` — `handlePack` (GET `/v1/pack/{tmdb_id}`, ETag/304)
- `test/identify.test.ts`, `test/db_identify.test.ts`, `test/pack.test.ts`

**Server modified files:**
- `src/schemas.ts` — `IdentifyResponseSchema` + candidate schema
- `src/index.ts` — route the two new endpoints
- `src/workers/pack_builder.ts` — append a document-frequency (DF) line; bump `pack_format_version`
- `test/pack_builder.test.ts` — assert the DF line

**Client new files (`backend/`):**
- `backend/app/matcher/chromaprint_scoring.py` — pure scoring (Python twin of `db_identify.ts`)
- `backend/app/matcher/chromaprint_matcher.py` — `WindowCandidate`, `LocalPackBackend`, `RemoteIdentifyBackend`, `ChromaprintMatcher`, `identify_episode_chromaprint`
- `backend/app/services/fingerprint_pack_cache.py` — `PackCache` (download + on-disk cache/manifest)
- `backend/tests/unit/test_chromaprint_scoring.py`
- `backend/tests/unit/test_chromaprint_matcher.py`
- `backend/tests/unit/test_fingerprint_pack_cache.py`
- `backend/tests/unit/test_calibrate_confidence_chromaprint.py`
- `backend/tests/integration/test_chromaprint_cascade.py`

**Client modified files:**
- `backend/app/matcher/episode_identification.py` — extend `calibrate_confidence` + `_attach_calibrated_confidence` with optional `chromaprint_signal`
- `backend/app/core/curator.py` — chromaprint-first cascade in `match_single_file` + `_chromaprint_prepass` helper; `CHROMAPRINT_GATE` constant
- `backend/app/services/matching_coordinator.py` — corroboration `match_source` mapping + shared `PackCache` wiring
- `backend/app/models/app_config.py` — add `enable_fingerprint_identification` toggle (default False until catalog seeded)

---

## Shared scoring definitions (authoritative — both repos implement identically)

These are implemented in TS (`src/db_identify.ts`) and Python (`chromaprint_scoring.py`). Constants and formulas MUST match.

- `hash_overlap_pct(query, ref)` = fraction of query hashes present in ref. Server reuses `exactOverlap` (Hamming ≤ 6). Client local backend uses exact-equality membership (faster; the local pack came from the same canonical source). Range [0, 1].
- `temporal_coherence(query, ref_set, min_run=3)` = walk the *ordered* query stream; a position is a "member" if its hash ∈ ref_set; find maximal contiguous member-runs; sum the lengths of runs with length ≥ `min_run`; divide by `len(query)`. Range [0, 1]. Rewards contiguous membership (true 30 s window) over scattered membership (commentary/noise).
- `rarity_weighted_overlap(query, ref_set, df_map, n_episodes)` = with smoothed IDF `idf(h) = ln((n_episodes + 1) / (df_map.get(h, 1) + 1)) + 1` (unseen query hashes default to **df = 1** — Laplace smoothing; this keeps the metric stable and the golden vector clean by giving absent hashes the same weight as df-1 hashes): `numerator = Σ idf(h) for h in query if h in ref_set`; `denominator = Σ idf(h) for h in query`; return `numerator / denominator` (0 if denominator 0). If `df_map` is empty/None, fall back to `hash_overlap_pct`.
- `combined_window_score(overlap, temporal, rarity)` = `clamp01(W_RARITY*rarity + W_OVERLAP*overlap + W_TEMPORAL*temporal)` with **W_RARITY=0.5, W_OVERLAP=0.3, W_TEMPORAL=0.2**.

**Golden parity vectors** (committed in both repos; hand-computable):
- Vector 1: `query=[1,2,3,4,5,6,7,8]`, `ref={1,2,3,5,6,7}`, `df={each matched hash:1}`, `n_episodes=10` → `overlap=0.75`, `temporal=0.75` (runs [1,2,3] and [5,6,7], both length 3), `rarity=0.75` (equal df ⇒ IDF cancels), `combined=0.75`.
- Vector 2: `query=[1,9,2,9,3,9,4]`, `ref={1,2,3,4}` → `overlap=4/7≈0.5714286`, `temporal=0.0` (all members isolated, no run ≥ 3).

---

## Pre-flight

- [ ] **Step 0.1: Confirm working directories**

Client worktree: `pwd` → `.../romantic-bhaskara-bdaa9b`. Server repo: `C:\Github\engram-fingerprint-server`.

- [ ] **Step 0.2: Server baseline green**

```bash
cd /c/Github/engram-fingerprint-server
pnpm install
pnpm test 2>&1 | tail -25
```
Expected: existing suites (codec, minhash, schemas, contribute_*, anti_poison_*, promotion, pack_builder, forget) all pass.

- [ ] **Step 0.3: Client baseline green**

```bash
cd backend
uv sync
uv run pytest tests/unit -q 2>&1 | tail -15
```
Expected: unit tests pass (note any pre-existing failures; do not fix here).

---

## Cluster S: Server `/v1/identify` + `/v1/pack`

### Task S1: `db_identify.ts` pure scoring + screen

**Files:**
- Create: `src/db_identify.ts`
- Test: `test/db_identify.test.ts`

- [ ] **Step S1.1: Write the failing tests**

Create `test/db_identify.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { temporalCoherence, rarityWeightedOverlap, combinedScore } from "../src/db_identify";

describe("temporalCoherence", () => {
  it("is high for a contiguous run of members", () => {
    const ref = new Set([1, 2, 3, 5, 6, 7]);
    expect(temporalCoherence([1, 2, 3, 4, 5, 6, 7, 8], ref)).toBeCloseTo(0.75, 9);
  });
  it("is zero when members are scattered (no run >= min_run)", () => {
    const ref = new Set([1, 2, 3, 4]);
    expect(temporalCoherence([1, 9, 2, 9, 3, 9, 4], ref)).toBeCloseTo(0.0, 9);
  });
});

describe("rarityWeightedOverlap", () => {
  it("collapses to plain overlap when df is uniform", () => {
    const ref = new Set([1, 2, 3, 5, 6, 7]);
    const df = new Map([1, 2, 3, 5, 6, 7].map((h) => [h, 1] as [number, number]));
    expect(rarityWeightedOverlap([1, 2, 3, 4, 5, 6, 7, 8], ref, df, 10)).toBeCloseTo(0.75, 9);
  });
  it("falls back to overlap when df map is empty", () => {
    const ref = new Set([1, 2, 3, 4]);
    expect(rarityWeightedOverlap([1, 9, 2, 9, 3, 9, 4], ref, new Map(), 10)).toBeCloseTo(4 / 7, 9);
  });
  it("upweights rare hashes over common ones", () => {
    const ref = new Set([1, 2]);
    // hash 1 rare (df 1), hash 2 common (df 9), n=10
    const dfRare = new Map<number, number>([[1, 1], [2, 9]]);
    const onlyRare = rarityWeightedOverlap([1, 3], ref, dfRare, 10); // matches rare 1, misses 3
    const onlyCommon = rarityWeightedOverlap([2, 3], ref, dfRare, 10); // matches common 2, misses 3
    expect(onlyRare).toBeGreaterThan(onlyCommon);
  });
});

describe("combinedScore", () => {
  it("weights rarity 0.5, overlap 0.3, temporal 0.2", () => {
    expect(combinedScore(0.75, 0.75, 0.75)).toBeCloseTo(0.75, 9);
    expect(combinedScore(1, 0, 0)).toBeCloseTo(0.3, 9);
  });
});
```

- [ ] **Step S1.2: Run (expect FAIL)**

```bash
cd /c/Github/engram-fingerprint-server
pnpm test db_identify 2>&1 | tail -20
```
Expected: module-not-found / import error.

- [ ] **Step S1.3: Implement `src/db_identify.ts`**

```ts
import { jaccardEstimate, minhash128 } from "./minhash";

export const W_RARITY = 0.5;
export const W_OVERLAP = 0.3;
export const W_TEMPORAL = 0.2;

export interface IdentifyCandidate {
  tmdb_id: number;
  season: number;
  episode: number;
  tier: string;
  hash_overlap_pct: number;
  temporal_coherence: number;
  rarity_weighted_score: number;
  combined_score: number;
}

const clamp01 = (x: number): number => Math.max(0, Math.min(1, x));

/** Fraction of ordered query hashes inside contiguous member-runs of length >= minRun. */
export function temporalCoherence(query: number[], refSet: Set<number>, minRun = 3): number {
  if (query.length === 0) return 0;
  let runLen = 0;
  let qualifying = 0;
  for (const h of query) {
    if (refSet.has(h)) {
      runLen++;
    } else {
      if (runLen >= minRun) qualifying += runLen;
      runLen = 0;
    }
  }
  if (runLen >= minRun) qualifying += runLen;
  return qualifying / query.length;
}

/** IDF-weighted overlap fraction. Falls back to plain overlap when df is unavailable. */
export function rarityWeightedOverlap(
  query: number[],
  refSet: Set<number>,
  dfMap: Map<number, number>,
  nEpisodes: number,
): number {
  if (query.length === 0) return 0;
  if (dfMap.size === 0 || nEpisodes <= 0) {
    let m = 0;
    for (const h of query) if (refSet.has(h)) m++;
    return m / query.length;
  }
  const idf = (h: number): number => Math.log((nEpisodes + 1) / ((dfMap.get(h) ?? 1) + 1)) + 1;
  let num = 0;
  let den = 0;
  for (const h of query) {
    const w = idf(h);
    den += w;
    if (refSet.has(h)) num += w;
  }
  return den > 0 ? num / den : 0;
}

export function combinedScore(overlap: number, temporal: number, rarity: number): number {
  return clamp01(W_RARITY * rarity + W_OVERLAP * overlap + W_TEMPORAL * temporal);
}

/**
 * Identify-mode screen: MinHash-screen ALL canonical sketches, return top-N by Jaccard.
 * This is screenAntiPoison without the self-exclusion clause, joined to tier.
 * Full-table scan is acceptable for the Phase 3 seed catalog (see plan risks).
 */
export async function screenIdentify(
  db: D1Database,
  queryHashes: number[],
  topN = 8,
): Promise<{ tmdb_id: number; season: number; episode: number; tier: string; jaccard: number }[]> {
  const querySketch = minhash128(queryHashes);
  const rows = await db.prepare(
    `SELECT cs.tmdb_id, cs.season, cs.episode, ec.tier, cs.sketch
     FROM canonical_sketch cs
     JOIN episode_canonical ec
       ON ec.tmdb_id = cs.tmdb_id AND ec.season = cs.season AND ec.episode = cs.episode`,
  ).all<{ tmdb_id: number; season: number; episode: number; tier: string; sketch: ArrayBuffer }>();

  const scored = rows.results.map((r) => ({
    tmdb_id: r.tmdb_id,
    season: r.season,
    episode: r.episode,
    tier: r.tier,
    jaccard: jaccardEstimate(querySketch, new Uint8Array(r.sketch)),
  }));
  scored.sort((a, b) => b.jaccard - a.jaccard);
  return scored.slice(0, topN);
}

/** Document-frequency map across a set of reference fingerprints (for rarity weighting). */
export async function buildDfMap(refHashesList: number[][]): Promise<Map<number, number>> {
  const df = new Map<number, number>();
  for (const hashes of refHashesList) {
    for (const h of new Set(hashes)) df.set(h, (df.get(h) ?? 0) + 1);
  }
  return df;
}
```

- [ ] **Step S1.4: Run (expect PASS)**

```bash
pnpm test db_identify 2>&1 | tail -20
```
Expected: all PASS.

- [ ] **Step S1.5: Commit**

```bash
git add src/db_identify.ts test/db_identify.test.ts
git commit -m "feat(identify): pure scoring (temporal/rarity/combined) + screenIdentify"
```

### Task S2: `IdentifyResponseSchema`

**Files:**
- Modify: `src/schemas.ts`
- Test: extend `test/schemas.test.ts`

- [ ] **Step S2.1: Write the failing test**

Append to `test/schemas.test.ts`:

```ts
import { IdentifyResponseSchema } from "../src/schemas";

describe("IdentifyResponseSchema", () => {
  it("accepts a well-formed identify response", () => {
    const ok = IdentifyResponseSchema.safeParse({
      candidates: [
        { tmdb_id: 1, season: 1, episode: 1, offset_seconds: null,
          hash_overlap_pct: 0.9, rarity_weighted_score: 0.8, tier: "canonical" },
      ],
    });
    expect(ok.success).toBe(true);
  });
});
```

- [ ] **Step S2.2: Run (expect FAIL)**

```bash
pnpm test schemas 2>&1 | tail -15
```
Expected: `IdentifyResponseSchema` import fails.

- [ ] **Step S2.3: Add the schema**

Append to `src/schemas.ts`:

```ts
export const IdentifyCandidateSchema = z.object({
  tmdb_id: z.number().int().positive(),
  season: z.number().int().min(0),
  episode: z.number().int().min(0),
  offset_seconds: z.number().nullable(),
  hash_overlap_pct: z.number().min(0).max(1),
  rarity_weighted_score: z.number().min(0).max(1),
  tier: z.enum(["candidate", "confirmed", "canonical"]),
});

export const IdentifyResponseSchema = z.object({
  candidates: z.array(IdentifyCandidateSchema),
});
```

- [ ] **Step S2.4: Run (expect PASS) + commit**

```bash
pnpm test schemas 2>&1 | tail -15
git add src/schemas.ts test/schemas.test.ts
git commit -m "feat(identify): IdentifyResponse schema"
```

### Task S3: `GET /v1/identify` route

**Files:**
- Create: `src/routes/identify.ts`
- Modify: `src/index.ts`
- Test: `test/identify.test.ts`

- [ ] **Step S3.1: Write the failing tests**

Create `test/identify.test.ts`:

```ts
import { describe, it, expect, beforeAll } from "vitest";
import { env, SELF } from "cloudflare:test";
import { minhash128 } from "../src/minhash";
import { encodeZstdVarint, initCodec } from "../src/codec";

beforeAll(async () => { await initCodec(); });

async function seedCanonical(tmdbId: number, season: number, episode: number, hashes: number[], tier = "canonical") {
  const encoded = await encodeZstdVarint(hashes);
  const sketch = minhash128(hashes);
  await env.DB.prepare(
    `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
     VALUES (?, ?, ?, ?, ?, 3, 0.9, unixepoch())`,
  ).bind(tmdbId, season, episode, tier, encoded).run();
  await env.DB.prepare(
    `INSERT INTO canonical_sketch (tmdb_id, season, episode, sketch, hash_count, generated_at)
     VALUES (?, ?, ?, ?, ?, unixepoch())`,
  ).bind(tmdbId, season, episode, sketch, hashes.length).run();
}

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

describe("GET /v1/identify", () => {
  it("returns the matching canonical episode as the top candidate", async () => {
    const hashes = Array.from({ length: 240 }, (_, i) => 5000 + i);
    await seedCanonical(77001, 1, 3, hashes);
    await seedCanonical(77001, 1, 4, Array.from({ length: 240 }, (_, i) => 900000 + i));

    const q = await encodeZstdVarint(hashes);
    const res = await SELF.fetch(`https://x.com/v1/identify?fp=${b64url(q)}&k=5`);
    expect(res.status).toBe(200);
    const data = await res.json() as any;
    expect(data.candidates.length).toBeGreaterThan(0);
    expect(data.candidates[0].season).toBe(1);
    expect(data.candidates[0].episode).toBe(3);
    expect(data.candidates[0].hash_overlap_pct).toBeGreaterThan(0.9);
    expect(data.candidates[0].tier).toBe("canonical");
  });

  it("returns 400 for a garbage fingerprint (never 500)", async () => {
    const res = await SELF.fetch(`https://x.com/v1/identify?fp=!!!notbase64!!!&k=5`);
    expect(res.status).toBe(400);
  });

  it("honors top_k", async () => {
    const res = await SELF.fetch(`https://x.com/v1/identify?fp=${b64url(await encodeZstdVarint([1, 2, 3]))}&k=1`);
    expect(res.status).toBe(200);
    const data = await res.json() as any;
    expect(data.candidates.length).toBeLessThanOrEqual(1);
  });
});
```

- [ ] **Step S3.2: Run (expect FAIL)**

```bash
pnpm test identify 2>&1 | tail -25
```
Expected: 404 (route not wired) → assertions fail.

- [ ] **Step S3.3: Implement `src/routes/identify.ts`**

```ts
import type { Env } from "./contribute";
import { decodeZstdVarint } from "../codec";
import { exactOverlap, loadCanonicalFingerprint } from "../db_anti_poison";
import {
  screenIdentify, temporalCoherence, rarityWeightedOverlap, combinedScore,
  buildDfMap, type IdentifyCandidate,
} from "../db_identify";

function fromB64Url(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

export async function handleIdentify(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const fp = url.searchParams.get("fp");
  const rawK = Number(url.searchParams.get("k") ?? "5");
  const topK = Math.max(1, Math.min(20, Number.isFinite(rawK) && rawK > 0 ? rawK : 5));
  if (!fp) return Response.json({ error: "missing fp" }, { status: 400 });

  let queryHashes: number[];
  try {
    queryHashes = await decodeZstdVarint(fromB64Url(fp));
  } catch {
    // A fingerprint that won't decode is a malformed request — 400, matching contribute.ts.
    return new Response("invalid fingerprint payload", { status: 400 });
  }
  if (queryHashes.length === 0) return Response.json({ candidates: [] }, { status: 200 });

  // Stage 1: MinHash screen across all canonical sketches.
  const screened = await screenIdentify(env.DB, queryHashes, 8);
  if (screened.length === 0) return Response.json({ candidates: [] }, { status: 200 });

  // Stage 2: exact-confirm each candidate; build a DF map over the candidate refs for rarity.
  const refs: { cand: typeof screened[number]; hashes: number[] }[] = [];
  for (const c of screened) {
    const refHashes = await loadCanonicalFingerprint(env.DB, c.tmdb_id, c.season, c.episode);
    if (refHashes) refs.push({ cand: c, hashes: refHashes });
  }
  const dfMap = await buildDfMap(refs.map((r) => r.hashes));

  const candidates: IdentifyCandidate[] = refs.map(({ cand, hashes }) => {
    const refSet = new Set(hashes);
    const overlap = exactOverlap(queryHashes, hashes);
    const temporal = temporalCoherence(queryHashes, refSet);
    const rarity = rarityWeightedOverlap(queryHashes, refSet, dfMap, refs.length);
    return {
      tmdb_id: cand.tmdb_id, season: cand.season, episode: cand.episode, tier: cand.tier,
      hash_overlap_pct: overlap, temporal_coherence: temporal, rarity_weighted_score: rarity,
      combined_score: combinedScore(overlap, temporal, rarity),
    };
  });
  candidates.sort((a, b) => b.combined_score - a.combined_score);

  return Response.json(
    {
      candidates: candidates.slice(0, topK).map((c) => ({
        tmdb_id: c.tmdb_id, season: c.season, episode: c.episode,
        offset_seconds: null,
        hash_overlap_pct: c.hash_overlap_pct,
        rarity_weighted_score: c.rarity_weighted_score,
        tier: c.tier,
      })),
    },
    { status: 200 },
  );
}
```

- [ ] **Step S3.4: Wire the route in `src/index.ts`**

Add the import at the top and a route arm before the `404` return:

```ts
import { handleIdentify } from "./routes/identify";
```

```ts
    if (url.pathname === "/v1/identify") {
      if (request.method !== "GET") return new Response("Method Not Allowed", { status: 405 });
      return handleIdentify(request, env);
    }
```

- [ ] **Step S3.5: Run (expect PASS) + typecheck + commit**

```bash
pnpm test identify 2>&1 | tail -25
pnpm typecheck
git add src/routes/identify.ts src/index.ts test/identify.test.ts
git commit -m "feat(identify): GET /v1/identify endpoint with screen+confirm scoring"
```

### Task S4: `GET /v1/pack/{tmdb_id}`

**Files:**
- Create: `src/routes/pack.ts`
- Modify: `src/index.ts`
- Test: `test/pack.test.ts`

- [ ] **Step S4.1: Write the failing tests**

Create `test/pack.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { env, SELF } from "cloudflare:test";

describe("GET /v1/pack/{tmdb_id}", () => {
  it("serves an existing pack with an ETag and supports 304", async () => {
    await env.PACKS.put("88001.zstd", new Uint8Array([1, 2, 3, 4]), {
      customMetadata: { tmdb_id: "88001", n_episodes: "2", generated_at: "1700000000" },
    });

    const res = await SELF.fetch("https://x.com/v1/pack/88001");
    expect(res.status).toBe(200);
    const etag = res.headers.get("ETag");
    expect(etag).toBeTruthy();
    const body = new Uint8Array(await res.arrayBuffer());
    expect(body.byteLength).toBe(4);

    const res304 = await SELF.fetch("https://x.com/v1/pack/88001", {
      headers: { "If-None-Match": etag! },
    });
    expect(res304.status).toBe(304);
  });

  it("404s for an unknown tmdb_id", async () => {
    const res = await SELF.fetch("https://x.com/v1/pack/999999");
    expect(res.status).toBe(404);
  });

  it("405s for non-GET", async () => {
    const res = await SELF.fetch("https://x.com/v1/pack/88001", { method: "POST" });
    expect(res.status).toBe(405);
  });
});
```

- [ ] **Step S4.2: Run (expect FAIL)**

```bash
pnpm test pack.test 2>&1 | tail -20
```

- [ ] **Step S4.3: Implement `src/routes/pack.ts`**

```ts
import type { Env } from "./contribute";

export async function handlePack(env: Env, tmdbId: number, ifNoneMatch: string | null): Promise<Response> {
  const obj = await env.PACKS.get(`${tmdbId}.zstd`);
  if (!obj) return new Response("Not Found", { status: 404 });

  // R2 provides httpEtag; fall back to generated_at metadata.
  const etag = obj.httpEtag ?? `"${obj.customMetadata?.generated_at ?? "0"}"`;
  if (ifNoneMatch && ifNoneMatch === etag) {
    return new Response(null, { status: 304, headers: { ETag: etag } });
  }
  return new Response(obj.body, {
    status: 200,
    headers: {
      ETag: etag,
      "Content-Type": "application/zstd",
      "Cache-Control": "public, max-age=3600",
      "X-Pack-Generated-At": obj.customMetadata?.generated_at ?? "",
    },
  });
}
```

- [ ] **Step S4.4: Wire the route in `src/index.ts`**

```ts
import { handlePack } from "./routes/pack";
```

```ts
    const packMatch = url.pathname.match(/^\/v1\/pack\/(\d+)$/);
    if (packMatch) {
      if (request.method !== "GET") return new Response("Method Not Allowed", { status: 405 });
      return handlePack(env, Number(packMatch[1]), request.headers.get("If-None-Match"));
    }
```

- [ ] **Step S4.5: Run (expect PASS) + commit**

```bash
pnpm test pack.test 2>&1 | tail -20
pnpm typecheck
git add src/routes/pack.ts src/index.ts test/pack.test.ts
git commit -m "feat(pack): GET /v1/pack/{tmdb_id} with ETag/304"
```

### Task S5: Embed document-frequency in packs

**Files:**
- Modify: `src/workers/pack_builder.ts`
- Test: `test/pack_builder.test.ts`

- [ ] **Step S5.1: Write the failing test**

Append to `test/pack_builder.test.ts` (add `decodeZstdVarint`/zstd decompress import at top: `import { decodeZstdVarint } from "../src/codec";` and `import { decompress } from "@bokuweb/zstd-wasm";`):

```ts
  it("embeds a document-frequency line and a pack_format_version", async () => {
    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
       VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
    ).bind(88800, 1, 1, await encodeZstdVarint([1, 2, 3])).run();
    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
       VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
    ).bind(88800, 1, 2, await encodeZstdVarint([2, 3, 4])).run();

    await runPackBuilder(env);
    const obj = await env.PACKS.get("88800.zstd");
    const raw = decompress(new Uint8Array(await obj!.arrayBuffer()));
    const lines = new TextDecoder().decode(raw).split("\n");
    const header = JSON.parse(lines[0]);
    expect(header.pack_format_version).toBe(2);
    const dfLine = lines.map((l) => JSON.parse(l)).find((o) => o.kind === "df");
    expect(dfLine).toBeTruthy();
    expect(dfLine.n_episodes).toBe(2);
    // hash 2 and 3 appear in both episodes -> df 2; hash 1 and 4 -> df 1
    const df = new Map<number, number>(dfLine.df);
    expect(df.get(2)).toBe(2);
    expect(df.get(1)).toBe(1);
  });
```

- [ ] **Step S5.2: Run (expect FAIL)**

```bash
pnpm test pack_builder 2>&1 | tail -20
```

- [ ] **Step S5.3: Modify `buildPack` in `src/workers/pack_builder.ts`**

Add `decodeZstdVarint` to the codec import (`import { initCodec } from "../codec";` → `import { initCodec, decodeZstdVarint } from "../codec";`). Replace the header + lines build with:

```ts
  await initCodec();
  // Build cross-episode document-frequency for rarity weighting (Phase 3).
  const df = new Map<number, number>();
  const decoded: { season: number; episode: number; fpB64: string }[] = [];
  for (const e of eps.results) {
    const bytes = new Uint8Array(e.fingerprint);
    const hashes = await decodeZstdVarint(bytes);
    for (const h of new Set(hashes)) df.set(h, (df.get(h) ?? 0) + 1);
    decoded.push({
      season: e.season,
      episode: e.episode,
      fpB64: btoa(String.fromCharCode(...bytes)),
    });
  }

  const header = JSON.stringify({
    wire_format_version: 1,
    pack_format_version: 2,
    tmdb_id,
    n_episodes: eps.results.length,
    generated_at: Math.floor(Date.now() / 1000),
  });
  const lines = [header];
  for (const e of decoded) {
    lines.push(JSON.stringify({ season: e.season, episode: e.episode, fingerprint_b64: e.fpB64 }));
  }
  lines.push(JSON.stringify({ kind: "df", n_episodes: eps.results.length, df: [...df.entries()] }));
  const raw = new TextEncoder().encode(lines.join("\n"));
  const { compress } = await import("@bokuweb/zstd-wasm");
  const compressed = compress(raw, 11);
```

(Delete the now-duplicated old header/lines/raw/compress block below it.)

- [ ] **Step S5.4: Run (expect PASS) + commit**

```bash
pnpm test pack_builder 2>&1 | tail -20
pnpm typecheck
git add src/workers/pack_builder.ts test/pack_builder.test.ts
git commit -m "feat(pack): embed cross-episode document-frequency (pack_format_version 2)"
```

---

## Cluster C: Client core (scoring, calibration, pack cache, matcher)

### Task C1: `chromaprint_scoring.py`

**Files:**
- Create: `backend/app/matcher/chromaprint_scoring.py`
- Test: `backend/tests/unit/test_chromaprint_scoring.py`

- [ ] **Step C1.1: Write the failing tests**

Create `backend/tests/unit/test_chromaprint_scoring.py`:

```python
"""Pure scoring functions — Python twin of src/db_identify.ts. Golden vectors locked."""

import math

from app.matcher.chromaprint_scoring import (
    hash_overlap_pct,
    temporal_coherence,
    rarity_weighted_overlap,
    combined_window_score,
)


def test_golden_vector_1():
    query = [1, 2, 3, 4, 5, 6, 7, 8]
    ref = {1, 2, 3, 5, 6, 7}
    df = dict.fromkeys(ref, 1)
    assert hash_overlap_pct(query, ref) == 0.75
    assert temporal_coherence(query, ref) == 0.75
    assert math.isclose(rarity_weighted_overlap(query, ref, df, 10), 0.75, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.75, 0.75, 0.75), 0.75, abs_tol=1e-9)


def test_golden_vector_2_scattered():
    query = [1, 9, 2, 9, 3, 9, 4]
    ref = {1, 2, 3, 4}
    assert math.isclose(hash_overlap_pct(query, ref), 4 / 7, abs_tol=1e-9)
    assert temporal_coherence(query, ref) == 0.0


def test_rarity_falls_back_to_overlap_without_df():
    query = [1, 9, 2, 9, 3, 9, 4]
    ref = {1, 2, 3, 4}
    assert math.isclose(rarity_weighted_overlap(query, ref, {}, 10), 4 / 7, abs_tol=1e-9)


def test_rarity_upweights_rare_hashes():
    ref = {1, 2}
    df = {1: 1, 2: 9}  # hash 1 rare, hash 2 common
    only_rare = rarity_weighted_overlap([1, 3], ref, df, 10)
    only_common = rarity_weighted_overlap([2, 3], ref, df, 10)
    assert only_rare > only_common


def test_combined_weights():
    assert math.isclose(combined_window_score(1.0, 0.0, 0.0), 0.3, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.0, 1.0, 0.0), 0.2, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.0, 0.0, 1.0), 0.5, abs_tol=1e-9)
```

- [ ] **Step C1.2: Run (expect FAIL)**

```bash
cd backend && uv run pytest tests/unit/test_chromaprint_scoring.py -v 2>&1 | tail -15
```

- [ ] **Step C1.3: Implement `backend/app/matcher/chromaprint_scoring.py`**

```python
"""Per-window chromaprint scoring — the Python twin of the server's src/db_identify.ts.

Definitions are authoritative and MUST match the TypeScript implementation
(golden parity vectors enforce this). See the Phase 3 plan "Shared scoring
definitions" section.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

W_RARITY = 0.5
W_OVERLAP = 0.3
W_TEMPORAL = 0.2


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def hash_overlap_pct(query: list[int], ref_set: Iterable[int] | set[int]) -> float:
    """Fraction of query hashes present in ref_set (exact-equality membership)."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    matches = sum(1 for h in query if h in refs)
    return matches / len(query)


def temporal_coherence(query: list[int], ref_set: Iterable[int] | set[int], min_run: int = 3) -> float:
    """Fraction of ordered query hashes inside contiguous member-runs of length >= min_run."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    run_len = 0
    qualifying = 0
    for h in query:
        if h in refs:
            run_len += 1
        else:
            if run_len >= min_run:
                qualifying += run_len
            run_len = 0
    if run_len >= min_run:
        qualifying += run_len
    return qualifying / len(query)


def rarity_weighted_overlap(
    query: list[int],
    ref_set: Iterable[int] | set[int],
    df_map: Mapping[int, int] | None,
    n_episodes: int,
) -> float:
    """IDF-weighted overlap fraction; falls back to plain overlap when df is unavailable."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    if not df_map or n_episodes <= 0:
        return hash_overlap_pct(query, refs)

    def idf(h: int) -> float:
        return math.log((n_episodes + 1) / (df_map.get(h, 1) + 1)) + 1.0

    num = 0.0
    den = 0.0
    for h in query:
        w = idf(h)
        den += w
        if h in refs:
            num += w
    return num / den if den > 0 else 0.0


def combined_window_score(overlap: float, temporal: float, rarity: float) -> float:
    return _clamp01(W_RARITY * rarity + W_OVERLAP * overlap + W_TEMPORAL * temporal)
```

- [ ] **Step C1.4: Run (expect PASS) + commit**

```bash
uv run pytest tests/unit/test_chromaprint_scoring.py -v 2>&1 | tail -15
git add app/matcher/chromaprint_scoring.py tests/unit/test_chromaprint_scoring.py
git commit -m "feat(matcher): chromaprint per-window scoring (parity with server)"
```

### Task C2: Extend `calibrate_confidence` with chromaprint signal

**Files:**
- Modify: `backend/app/matcher/episode_identification.py` (`calibrate_confidence` ~281, `_attach_calibrated_confidence` ~399)
- Test: `backend/tests/unit/test_calibrate_confidence_chromaprint.py`

- [ ] **Step C2.1: Write the failing tests**

Create `backend/tests/unit/test_calibrate_confidence_chromaprint.py`:

```python
"""Chromaprint signal is additive to calibrate_confidence — never lowers, no-op when absent."""

from app.matcher.episode_identification import calibrate_confidence


BASE = dict(score=0.5, score_gap=0.4, vote_count=8, target_votes=10, processed_coverage=0.5)


def test_absent_signal_is_byte_identical():
    a = calibrate_confidence(**BASE)
    b = calibrate_confidence(**BASE, chromaprint_signal=None)
    assert a == b


def test_strong_signal_raises_confidence():
    base_conf, _ = calibrate_confidence(score=0.2, score_gap=0.05, vote_count=3,
                                        target_votes=10, processed_coverage=0.3)
    cp_conf, comps = calibrate_confidence(
        score=0.2, score_gap=0.05, vote_count=3, target_votes=10, processed_coverage=0.3,
        chromaprint_signal={"hash_overlap": 0.95, "temporal_coherence": 0.9, "rarity_weighted_score": 0.9},
    )
    assert cp_conf > base_conf
    assert "cp_confidence" in comps
    assert "hash_overlap" in comps


def test_weak_signal_never_lowers_asr_strong():
    strong, _ = calibrate_confidence(score=0.9, score_gap=0.8, vote_count=20,
                                     target_votes=20, processed_coverage=0.9)
    with_weak, _ = calibrate_confidence(
        score=0.9, score_gap=0.8, vote_count=20, target_votes=20, processed_coverage=0.9,
        chromaprint_signal={"hash_overlap": 0.1, "temporal_coherence": 0.0, "rarity_weighted_score": 0.0},
    )
    assert with_weak >= strong
```

- [ ] **Step C2.2: Run (expect FAIL)**

```bash
cd backend && uv run pytest tests/unit/test_calibrate_confidence_chromaprint.py -v 2>&1 | tail -15
```
Expected: `TypeError: unexpected keyword argument 'chromaprint_signal'`.

- [ ] **Step C2.3: Add the parameter + path to `calibrate_confidence`**

In `backend/app/matcher/episode_identification.py`, change the signature (add the last keyword param):

```python
def calibrate_confidence(
    *,
    score: float,
    score_gap: float,
    vote_count: int,
    target_votes: int,
    processed_coverage: float,
    runner_up_votes: int = 0,
    chromaprint_signal: dict | None = None,
) -> tuple[float, dict[str, float]]:
```

Then, immediately after the existing line `confidence = max(base_confidence, ratio_confidence)` (currently line 380) and before the `components = {` dict, insert:

```python
    # Chromaprint path (Phase 3) — additive. Absent signal is a no-op; a present
    # signal can only raise confidence (max), never lower an ASR-strong result.
    cp_overlap = cp_temporal = cp_rarity = cp_confidence = 0.0
    if chromaprint_signal:
        cp_overlap = _clamp01(float(chromaprint_signal.get("hash_overlap", 0.0)))
        cp_temporal = _clamp01(float(chromaprint_signal.get("temporal_coherence", 0.0)))
        cp_rarity = _clamp01(float(chromaprint_signal.get("rarity_weighted_score", 0.0)))
        cp_evidence = EVIDENCE_FLOOR + (1.0 - EVIDENCE_FLOOR) * cp_temporal
        cp_confidence = _clamp01(cp_overlap * cp_evidence * (0.5 + 0.5 * cp_rarity))
        confidence = max(confidence, cp_confidence)
```

And add these keys to the returned `components` dict (after `"ratio_confidence": ratio_confidence,`):

```python
        "hash_overlap": cp_overlap,
        "temporal_coherence": cp_temporal,
        "rarity_weighted_score": cp_rarity,
        "cp_confidence": cp_confidence,
```

- [ ] **Step C2.4: Thread the signal through `_attach_calibrated_confidence`**

Change its signature to accept the optional signal:

```python
def _attach_calibrated_confidence(
    best_match: dict,
    results_summary: list[dict],
    video_duration: float,
    chunk_len: int = 30,
    chromaprint_signal: dict | None = None,
) -> None:
```

Find the `calibrate_confidence(...)` call inside this function and add `chromaprint_signal=chromaprint_signal,` to its keyword arguments.

- [ ] **Step C2.5: Run (expect PASS) — including the existing calibration regression suite**

```bash
uv run pytest tests/unit/test_calibrate_confidence_chromaprint.py -v 2>&1 | tail -15
uv run pytest tests/unit -k "calibrat or confidence" -v 2>&1 | tail -20
```
Expected: new tests PASS; all pre-existing calibration tests still PASS (regression lock).

- [ ] **Step C2.6: Commit**

```bash
git add app/matcher/episode_identification.py tests/unit/test_calibrate_confidence_chromaprint.py
git commit -m "feat(matcher): additive chromaprint signal in calibrate_confidence"
```

### Task C3: `fingerprint_pack_cache.py`

**Files:**
- Create: `backend/app/services/fingerprint_pack_cache.py`
- Test: `backend/tests/unit/test_fingerprint_pack_cache.py`

- [ ] **Step C3.1: Write the failing tests**

Create `backend/tests/unit/test_fingerprint_pack_cache.py`:

```python
"""PackCache: decode the server pack format, honor ETag/304, expire on TTL."""

import base64
import json

import pytest
import zstandard

from app.services.fingerprint_pack_cache import PackCache, DecodedPack


def _make_pack(tmdb_id: int) -> bytes:
    header = {"wire_format_version": 1, "pack_format_version": 2, "tmdb_id": tmdb_id,
              "n_episodes": 2, "generated_at": 1700000000}
    # episode fingerprints: zstd-varint blobs base64'd, exactly as pack_builder writes.
    from app.services.zstd_varint_codec import encode_zstd_varint
    e1 = base64.b64encode(encode_zstd_varint([1, 2, 3])).decode()
    e2 = base64.b64encode(encode_zstd_varint([2, 3, 4])).decode()
    lines = [
        json.dumps(header),
        json.dumps({"season": 1, "episode": 1, "fingerprint_b64": e1}),
        json.dumps({"season": 1, "episode": 2, "fingerprint_b64": e2}),
        json.dumps({"kind": "df", "n_episodes": 2, "df": [[1, 1], [2, 2], [3, 2], [4, 1]]}),
    ]
    return zstandard.ZstdCompressor().compress("\n".join(lines).encode())


def test_load_decodes_episodes_and_df(tmp_path):
    cache = PackCache(base_dir=tmp_path)
    cache.path(55).write_bytes(_make_pack(55))
    pack: DecodedPack = cache.load(55)
    assert pack is not None
    assert (1, 1) in pack.episodes
    assert pack.episodes[(1, 1)] == {1, 2, 3}
    assert pack.df_map[2] == 2
    assert pack.n_episodes == 2


def test_has_false_when_absent(tmp_path):
    assert PackCache(base_dir=tmp_path).has(999) is False


@pytest.mark.asyncio
async def test_ensure_writes_on_200_and_keeps_on_304(tmp_path, monkeypatch):
    cache = PackCache(base_dir=tmp_path)
    blob = _make_pack(77)

    class FakeResp:
        def __init__(self, status, content=b"", etag=None):
            self.status_code = status
            self.content = content
            self.headers = {"ETag": etag} if etag else {}

    calls = {"n": 0}

    async def fake_get(self, url, headers=None):
        calls["n"] += 1
        if headers and headers.get("If-None-Match") == '"v1"':
            return FakeResp(304)
        return FakeResp(200, blob, '"v1"')

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    assert await cache.ensure(77, "https://server") is True
    assert cache.has(77)
    # Second call sends If-None-Match and gets 304 → keeps file, no rewrite error.
    assert await cache.ensure(77, "https://server") is True
    assert calls["n"] == 2
```

- [ ] **Step C3.2: Run (expect FAIL)**

```bash
uv run pytest tests/unit/test_fingerprint_pack_cache.py -v 2>&1 | tail -15
```

- [ ] **Step C3.3: Implement `backend/app/services/fingerprint_pack_cache.py`**

```python
"""Local fingerprint-pack cache (Phase 3).

Downloads per-show packs from GET /v1/pack/{tmdb_id} and caches them under
~/.engram/cache/fingerprint_packs/. The pack format mirrors the server's
pack_builder.ts: zstd of newline-JSON (header line, per-episode lines, optional
df line). manifest.json carries per-show ETag + timestamps for 304 revalidation.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import zstandard
from loguru import logger

from app.services.zstd_varint_codec import decode_zstd_varint

DEFAULT_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class DecodedPack:
    tmdb_id: int
    n_episodes: int
    episodes: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    df_map: dict[int, int] = field(default_factory=dict)


class PackCache:
    def __init__(self, base_dir: Path | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path("~/.engram/cache/fingerprint_packs").expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def path(self, tmdb_id: int) -> Path:
        return self.base_dir / f"{tmdb_id}.zstd"

    def _manifest_path(self) -> Path:
        return self.base_dir / "manifest.json"

    def manifest(self) -> dict:
        p = self._manifest_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_manifest(self, m: dict) -> None:
        self._manifest_path().write_text(json.dumps(m, separators=(",", ":")))

    def has(self, tmdb_id: int) -> bool:
        if not self.path(tmdb_id).exists():
            return False
        entry = self.manifest().get(str(tmdb_id))
        if not entry:
            return True  # present but unmanifested — treat as usable
        age = time.time() - entry.get("downloaded_at", 0)
        return age <= self.ttl_seconds

    def load(self, tmdb_id: int) -> DecodedPack | None:
        p = self.path(tmdb_id)
        if not p.exists():
            return None
        try:
            raw = zstandard.ZstdDecompressor().decompress(p.read_bytes())
        except zstandard.ZstdError as e:
            logger.warning(f"Corrupt pack for {tmdb_id}: {e}")
            return None
        lines = raw.decode("utf-8").split("\n")
        header = json.loads(lines[0])
        pack = DecodedPack(tmdb_id=int(header["tmdb_id"]), n_episodes=int(header.get("n_episodes", 0)))
        for line in lines[1:]:
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("kind") == "df":
                pack.df_map = {int(h): int(c) for h, c in obj.get("df", [])}
                continue
            blob = base64.b64decode(obj["fingerprint_b64"])
            pack.episodes[(int(obj["season"]), int(obj["episode"]))] = set(decode_zstd_varint(blob))
        return pack

    async def ensure(self, tmdb_id: int, server_url: str) -> bool:
        """Download/refresh the pack. Returns True if a usable pack is present after the call."""
        url = f"{server_url.rstrip('/')}/v1/pack/{tmdb_id}"
        entry = self.manifest().get(str(tmdb_id), {})
        headers = {}
        if self.path(tmdb_id).exists() and entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            logger.info(f"Pack fetch failed for {tmdb_id}: {e}")
            return self.path(tmdb_id).exists()

        if resp.status_code == 304:
            entry["downloaded_at"] = time.time()
            m = self.manifest(); m[str(tmdb_id)] = entry; self._write_manifest(m)
            return True
        if resp.status_code == 200:
            self.path(tmdb_id).write_bytes(resp.content)
            m = self.manifest()
            m[str(tmdb_id)] = {"etag": resp.headers.get("ETag"), "downloaded_at": time.time()}
            self._write_manifest(m)
            return True
        if resp.status_code == 404:
            return False
        logger.info(f"Pack fetch for {tmdb_id} returned {resp.status_code}")
        return self.path(tmdb_id).exists()
```

- [ ] **Step C3.4: Run (expect PASS) + commit**

```bash
uv run pytest tests/unit/test_fingerprint_pack_cache.py -v 2>&1 | tail -15
git add app/services/fingerprint_pack_cache.py tests/unit/test_fingerprint_pack_cache.py
git commit -m "feat(services): fingerprint pack cache (download, decode, ETag/TTL)"
```

### Task C4: `chromaprint_matcher.py` — backends + per-window classify

**Files:**
- Create: `backend/app/matcher/chromaprint_matcher.py`
- Test: `backend/tests/unit/test_chromaprint_matcher.py`

- [ ] **Step C4.1: Write the failing tests**

Create `backend/tests/unit/test_chromaprint_matcher.py`:

```python
"""ChromaprintMatcher backends: local pack ranking, remote URL/JSON mapping, selection."""

import pytest

from app.matcher.chromaprint_matcher import (
    WindowCandidate, LocalPackBackend, RemoteIdentifyBackend, ChromaprintMatcher,
)
from app.services.fingerprint_pack_cache import DecodedPack


def _pack() -> DecodedPack:
    p = DecodedPack(tmdb_id=42, n_episodes=2)
    p.episodes = {(1, 1): set(range(100, 340)), (1, 2): set(range(900, 1140))}
    p.df_map = {}
    return p


@pytest.mark.asyncio
async def test_local_backend_ranks_correct_episode():
    backend = LocalPackBackend(_pack())
    query = list(range(100, 340))  # exactly episode (1,1)
    cands = await backend.classify_window(query, top_k=2)
    assert cands[0].season == 1 and cands[0].episode == 1
    assert cands[0].hash_overlap_pct > 0.9


@pytest.mark.asyncio
async def test_remote_backend_builds_url_and_maps_json(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"candidates": [
                {"tmdb_id": 42, "season": 1, "episode": 5, "offset_seconds": None,
                 "hash_overlap_pct": 0.88, "rarity_weighted_score": 0.7, "tier": "canonical"}]}
        def raise_for_status(self):
            pass

    async def fake_get(self, url, params=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    backend = RemoteIdentifyBackend("https://server")
    cands = await backend.classify_window([1, 2, 3], top_k=5)
    assert "/v1/identify" in captured["url"]
    assert captured["params"]["k"] == 5
    assert cands[0].episode == 5 and cands[0].tier == "canonical"


def test_select_backend_prefers_local_when_pack_present():
    pack = _pack()

    class FakeCache:
        def has(self, tmdb_id): return True
        def load(self, tmdb_id): return pack

    m = ChromaprintMatcher(tmdb_id=42, server_url="https://s", pack_cache=FakeCache())
    assert isinstance(m.select_backend(), LocalPackBackend)


def test_select_backend_remote_when_no_pack():
    class FakeCache:
        def has(self, tmdb_id): return False
        def load(self, tmdb_id): return None

    m = ChromaprintMatcher(tmdb_id=42, server_url="https://s", pack_cache=FakeCache())
    assert isinstance(m.select_backend(), RemoteIdentifyBackend)
```

- [ ] **Step C4.2: Run (expect FAIL)**

```bash
uv run pytest tests/unit/test_chromaprint_matcher.py -v 2>&1 | tail -15
```

- [ ] **Step C4.3: Implement `backend/app/matcher/chromaprint_matcher.py`**

```python
"""Chromaprint query side (Phase 3).

Per-window classifier with two backends behind one interface:
- LocalPackBackend: queries a decoded on-disk pack (shows the user owns).
- RemoteIdentifyBackend: GET /v1/identify for shows without a local pack.

The title-level orchestration (identify_episode_chromaprint) reuses the existing
EpisodeMatcher windowed-voting machinery — see the Phase 3 plan, Cluster G.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from loguru import logger

from app.matcher.chromaprint_scoring import (
    combined_window_score, hash_overlap_pct, rarity_weighted_overlap, temporal_coherence,
)
from app.services.fingerprint_pack_cache import DecodedPack
from app.services.zstd_varint_codec import encode_zstd_varint
import base64


@dataclass
class WindowCandidate:
    tmdb_id: int
    season: int
    episode: int
    tier: str
    hash_overlap_pct: float
    temporal_coherence: float
    rarity_weighted_score: float
    combined_score: float
    offset_seconds: float | None = None


class ChromaprintMatcherBackend(Protocol):
    async def classify_window(self, query_hashes: list[int], *, top_k: int = 5) -> list[WindowCandidate]:
        ...


class LocalPackBackend:
    """Score a window against every episode in a decoded local pack."""

    def __init__(self, pack: DecodedPack) -> None:
        self.pack = pack

    async def classify_window(self, query_hashes: list[int], *, top_k: int = 5) -> list[WindowCandidate]:
        out: list[WindowCandidate] = []
        for (season, episode), ref_set in self.pack.episodes.items():
            overlap = hash_overlap_pct(query_hashes, ref_set)
            if overlap == 0.0:
                continue
            temporal = temporal_coherence(query_hashes, ref_set)
            rarity = rarity_weighted_overlap(query_hashes, ref_set, self.pack.df_map, self.pack.n_episodes)
            out.append(WindowCandidate(
                tmdb_id=self.pack.tmdb_id, season=season, episode=episode, tier="canonical",
                hash_overlap_pct=overlap, temporal_coherence=temporal, rarity_weighted_score=rarity,
                combined_score=combined_window_score(overlap, temporal, rarity),
            ))
        out.sort(key=lambda c: c.combined_score, reverse=True)
        return out[:top_k]


class RemoteIdentifyBackend:
    """Query GET /v1/identify for a window."""

    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")

    async def classify_window(self, query_hashes: list[int], *, top_k: int = 5) -> list[WindowCandidate]:
        blob = encode_zstd_varint(query_hashes)
        fp = base64.urlsafe_b64encode(blob).decode().rstrip("=")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.server_url}/v1/identify", params={"fp": fp, "k": top_k})
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.info(f"Remote identify failed: {e}")
            return []
        out: list[WindowCandidate] = []
        for c in data.get("candidates", []):
            overlap = float(c.get("hash_overlap_pct", 0.0))
            rarity = float(c.get("rarity_weighted_score", 0.0))
            # Server does not return temporal_coherence in the wire shape; treat overlap as the
            # combined proxy (temporal already folded into the server-side ranking).
            out.append(WindowCandidate(
                tmdb_id=int(c["tmdb_id"]), season=int(c["season"]), episode=int(c["episode"]),
                tier=str(c.get("tier", "canonical")),
                hash_overlap_pct=overlap, temporal_coherence=0.0, rarity_weighted_score=rarity,
                combined_score=combined_window_score(overlap, 0.0, rarity),
                offset_seconds=c.get("offset_seconds"),
            ))
        return out


class ChromaprintMatcher:
    """Owns backend selection for one show (tmdb_id)."""

    def __init__(self, *, tmdb_id: int, server_url: str, pack_cache, allow_remote_fallthrough: bool = False) -> None:
        self.tmdb_id = tmdb_id
        self.server_url = server_url
        self.pack_cache = pack_cache
        self.allow_remote_fallthrough = allow_remote_fallthrough
        self._local: LocalPackBackend | None = None
        self._remote = RemoteIdentifyBackend(server_url)

    def select_backend(self) -> ChromaprintMatcherBackend:
        if self.pack_cache is not None and self.pack_cache.has(self.tmdb_id):
            pack = self.pack_cache.load(self.tmdb_id)
            if pack is not None:
                self._local = LocalPackBackend(pack)
                return self._local
        return self._remote

    async def classify_window(self, query_hashes: list[int], *, top_k: int = 5) -> list[WindowCandidate]:
        backend = self.select_backend()
        cands = await backend.classify_window(query_hashes, top_k=top_k)
        if not cands and isinstance(backend, LocalPackBackend) and self.allow_remote_fallthrough:
            return await self._remote.classify_window(query_hashes, top_k=top_k)
        return cands
```

- [ ] **Step C4.4: Run (expect PASS) + commit**

```bash
uv run pytest tests/unit/test_chromaprint_matcher.py -v 2>&1 | tail -15
git add app/matcher/chromaprint_matcher.py tests/unit/test_chromaprint_matcher.py
git commit -m "feat(matcher): ChromaprintMatcher local/remote backends + window classify"
```

---

## Cluster G: Cascade integration

### Task G1: `identify_episode_chromaprint` orchestration

**Files:**
- Modify: `backend/app/matcher/chromaprint_matcher.py` (append the orchestration function)
- Test: extend `backend/tests/unit/test_chromaprint_matcher.py`

Reuses the existing `EpisodeMatcher` helpers: `extract_audio_chunk` (cached 16 kHz WAV — `episode_identification.py:726`), `MatchCoverage` (`:581`), `_attach_calibrated_confidence` (`:399`), `chunk_duration`/`skip_initial_duration`. The classifier per window is `ChromaprintMatcher.classify_window`; the fingerprint per WAV comes from `ChromaprintExtractor.extract`.

- [ ] **Step G1.1: Write the failing test**

Append to `backend/tests/unit/test_chromaprint_matcher.py`:

```python
@pytest.mark.asyncio
async def test_identify_episode_chromaprint_votes_winner(monkeypatch, tmp_path):
    """A matcher whose windows all classify to (1,1) yields a (1,1) result with chromaprint_signal."""
    from app.matcher.chromaprint_matcher import identify_episode_chromaprint

    class FakeMatcher:
        chunk_duration = 30
        skip_initial_duration = 90
        def extract_audio_chunk(self, mkv, start, duration=None):
            return tmp_path / f"chunk_{start}.wav"

    class FakeExtractor:
        async def extract(self, wav_path):
            from app.matcher.chromaprint_extractor import ChromaprintResult
            return ChromaprintResult(hashes=list(range(100, 340)), duration_seconds=30.0, fpcalc_version="t")

    cm = ChromaprintMatcher(tmdb_id=42, server_url="https://s", pack_cache=type("C", (), {
        "has": lambda self, t: True, "load": lambda self, t: _pack()})())

    result = await identify_episode_chromaprint(
        matcher=FakeMatcher(), video_file=str(tmp_path / "v.mkv"), season_number=1,
        chromaprint_matcher=cm, extractor=FakeExtractor(), video_duration=1800.0, num_points=6,
    )
    assert result is not None
    assert result["season"] == 1 and result["episode"] == 1
    assert result["match_details"]["match_source"] == "chromaprint"
    assert "chromaprint_signal" in result["match_details"]
    assert result["tier"] == "canonical"
```

- [ ] **Step G1.2: Run (expect FAIL)**

```bash
cd backend && uv run pytest tests/unit/test_chromaprint_matcher.py::test_identify_episode_chromaprint_votes_winner -v 2>&1 | tail -15
```

- [ ] **Step G1.3: Append `identify_episode_chromaprint` to `chromaprint_matcher.py`**

```python
def _scan_points(video_duration: float, num_points: int, skip_initial: float, chunk_duration: int) -> list[float]:
    """Evenly-spaced start times across the body of the file (mirrors identify_episode)."""
    usable_start = min(skip_initial, max(0.0, video_duration - chunk_duration))
    usable_end = max(usable_start, video_duration - chunk_duration)
    if num_points <= 1 or usable_end <= usable_start:
        return [usable_start]
    step = (usable_end - usable_start) / (num_points - 1)
    return [usable_start + i * step for i in range(num_points)]


async def identify_episode_chromaprint(
    *,
    matcher,
    video_file: str,
    season_number: int,
    chromaprint_matcher: "ChromaprintMatcher",
    extractor,
    video_duration: float,
    num_points: int = 10,
    min_vote_count: int = 2,
    per_window_floor: float = 0.30,
):
    """Chromaprint-first episode identification reusing EpisodeMatcher voting machinery.

    Returns a dict shaped like EpisodeMatcher.identify_episode (season, episode,
    confidence, score, tier, match_details, runner_ups), or None on no usable votes.
    """
    from app.matcher.episode_identification import MatchCoverage, _attach_calibrated_confidence

    coverages: dict[str, MatchCoverage] = {}
    tiers: dict[str, str] = {}
    # Per-episode accumulators for the aggregate chromaprint signal.
    sig_acc: dict[str, dict[str, float]] = {}
    chunk_len = matcher.chunk_duration

    points = _scan_points(video_duration, num_points, matcher.skip_initial_duration, chunk_len)
    for start in points:
        try:
            wav = matcher.extract_audio_chunk(video_file, start, chunk_len)
            fp = await extractor.extract(str(wav))
        except Exception as e:  # noqa: BLE001 — best-effort per window
            logger.debug(f"chromaprint window {start:.0f}s skipped: {e}")
            continue
        cands = await chromaprint_matcher.classify_window(fp.hashes, top_k=3)
        # Season-scope to match the ASR path (which only loads the target season's
        # references) — a multi-season pack must not vote for a wrong-season episode.
        cands = [c for c in cands if c.season == season_number]
        if not cands:
            continue
        best = cands[0]
        if best.combined_score < per_window_floor:
            continue
        key = f"S{best.season:02d}E{best.episode:02d}"
        if key not in coverages:
            coverages[key] = MatchCoverage(key, video_duration, video_duration)
            tiers[key] = best.tier
            sig_acc[key] = {"overlap": 0.0, "temporal": 0.0, "rarity": 0.0, "n": 0.0}
        coverages[key].add_match(start, chunk_len, best.combined_score)
        acc = sig_acc[key]
        acc["overlap"] += best.hash_overlap_pct
        acc["temporal"] += best.temporal_coherence
        acc["rarity"] += best.rarity_weighted_score
        acc["n"] += 1

    if not coverages:
        return None

    results_summary = sorted(
        ({"episode_name": k, "score": c.ranked_voting_score, "vote_count": len(c.matched_chunks)}
         for k, c in coverages.items()),
        key=lambda r: r["score"], reverse=True,
    )
    winner = results_summary[0]
    win_key = winner["episode_name"]
    if winner["vote_count"] < min_vote_count:
        return None

    acc = sig_acc[win_key]
    n = max(1.0, acc["n"])
    chromaprint_signal = {
        "hash_overlap": acc["overlap"] / n,
        "temporal_coherence": acc["temporal"] / n,
        "rarity_weighted_score": acc["rarity"] / n,
    }

    season = int(win_key[1:3])
    episode = int(win_key[4:6])
    best_match = {
        "season": season,
        "episode": episode,
        "score": winner["score"],
        "match_details": {
            "match_source": "chromaprint",
            "target_votes": len(points),
            "vote_count": winner["vote_count"],
            "chromaprint_signal": chromaprint_signal,
            "candidate_scores": {r["episode_name"]: r["score"] for r in results_summary},
        },
    }
    _attach_calibrated_confidence(best_match, results_summary, video_duration, chunk_len, chromaprint_signal)
    best_match["confidence"] = best_match.get("confidence", winner["score"])
    best_match["tier"] = tiers[win_key]
    best_match["runner_ups"] = [
        {"episode_name": r["episode_name"], "score": r["score"], "vote_count": r["vote_count"]}
        for r in results_summary[1:]
    ]
    return best_match
```

- [ ] **Step G1.4: Run (expect PASS) + commit**

```bash
uv run pytest tests/unit/test_chromaprint_matcher.py -v 2>&1 | tail -15
git add app/matcher/chromaprint_matcher.py tests/unit/test_chromaprint_matcher.py
git commit -m "feat(matcher): identify_episode_chromaprint windowed-voting orchestration"
```

### Task G2: Chromaprint-first cascade in `match_single_file`

**Files:**
- Modify: `backend/app/core/curator.py`
- Modify: `backend/app/models/app_config.py` (add `enable_fingerprint_identification`)
- Test: `backend/tests/integration/test_chromaprint_cascade.py`

- [ ] **Step G2.1: Add the identification opt-in config flag**

In `backend/app/models/app_config.py`, near the other fingerprint fields, add:

```python
    # Phase 3: chromaprint identification (default OFF until the catalog is seeded).
    enable_fingerprint_identification: bool = Field(default=False)
```

(The `database.py` reconciler picks this up automatically; also add a one-line Alembic migration mirroring the Phase 1/2 pattern — `add_column("app_config", "enable_fingerprint_identification", Boolean, server_default="0")`.)

- [ ] **Step G2.2: Write the failing integration tests**

Create `backend/tests/integration/test_chromaprint_cascade.py`:

```python
"""Cascade: chromaprint-first accept, ASR fallback on miss, cross-validation on conflict."""

from pathlib import Path

import pytest

from app.core.curator import EpisodeCurator, MatchResult


def _cp_result(season, episode, confidence, tier="canonical"):
    return {
        "season": season, "episode": episode, "confidence": confidence,
        "score": confidence, "tier": tier,
        "match_details": {"match_source": "chromaprint",
                          "chromaprint_signal": {"hash_overlap": 0.95, "temporal_coherence": 0.9,
                                                 "rarity_weighted_score": 0.9}},
        "runner_ups": [],
    }


@pytest.mark.asyncio
async def test_canonical_high_confidence_accepts_without_asr(monkeypatch):
    curator = EpisodeCurator()

    async def fake_prepass(self, **kwargs):
        return _cp_result(1, 3, 0.93)

    def fail_asr(*a, **k):
        raise AssertionError("ASR must not run on a confident chromaprint canonical hit")

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", fake_prepass)
    monkeypatch.setattr(curator, "_run_asr_identify", fail_asr, raising=False)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.episode_code == "S01E03"
    assert result.needs_review is False
    assert result.match_details.get("chromaprint_accepted") is True


@pytest.mark.asyncio
async def test_conflict_forces_review(monkeypatch):
    curator = EpisodeCurator()

    async def cp(self, **kwargs):
        return _cp_result(1, 3, 0.93)

    # ASR disagrees (says episode 7)
    async def asr(self, *a, **k):
        return MatchResult(file_path=Path("/x/ep.mkv"), episode_code="S01E07",
                           episode_title=None, confidence=0.8, needs_review=False,
                           match_details={"match_source": "engram_asr"})

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", cp)
    monkeypatch.setattr(EpisodeCurator, "_run_asr_identify", asr, raising=False)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.needs_review is True
    assert "chromaprint_vs_asr_conflict" in (result.match_details or {})
```

- [ ] **Step G2.3: Refactor `match_single_file` for the cascade**

The current `match_single_file` body (lines 207-280) becomes the ASR path, extracted into `_run_asr_identify`. Add the chromaprint pre-pass at the top.

First, extract the existing matcher call into a helper. Add this method to `EpisodeCurator` (paste the current try/except body from `match_single_file` verbatim into it, returning a `MatchResult`):

```python
    async def _run_asr_identify(
        self, file_path: Path, series_name: str | None, season: int | None,
        progress_callback=None, num_points=None, min_vote_count=None,
    ) -> MatchResult:
        """Existing ASR+TF-IDF identification path (was the body of match_single_file)."""
        # MOVE the current `try: match = await asyncio.to_thread(self._matcher.identify_episode, ...)`
        # block (curator.py:207-280) here unchanged, returning its MatchResult/fallback.
```

Then add the pre-pass helper:

```python
    CHROMAPRINT_GATE = 0.90

    async def _chromaprint_prepass(
        self, *, file_path: Path, series_name: str, season: int,
    ) -> dict | None:
        """Run chromaprint identification; return its result dict or None if unavailable/empty."""
        from app.services.config_service import get_config
        from app.matcher.chromaprint_extractor import ChromaprintExtractor
        from app.matcher.chromaprint_matcher import ChromaprintMatcher, identify_episode_chromaprint
        from app.matcher.tmdb_client import fetch_show_id
        from app.api.validation import detect_fpcalc

        cfg = await get_config()
        if not cfg or not getattr(cfg, "enable_fingerprint_identification", False):
            return None
        fpcalc = cfg.fpcalc_path or (detect_fpcalc().path if detect_fpcalc().found else None)
        if not fpcalc or self._matcher is None:
            return None
        tmdb_id = await asyncio.to_thread(fetch_show_id, series_name)
        if not tmdb_id:
            return None

        pack_cache = getattr(self, "_pack_cache", None)
        server_url = cfg.fingerprint_server_url or "https://engram-fp-prod.jonathansakkos.workers.dev"
        if pack_cache is not None:
            try:
                await pack_cache.ensure(int(tmdb_id), server_url)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"pack ensure failed: {e}")

        cm = ChromaprintMatcher(tmdb_id=int(tmdb_id), server_url=server_url, pack_cache=pack_cache)
        extractor = ChromaprintExtractor(fpcalc_path=fpcalc)
        from app.matcher.episode_identification import get_video_duration
        video_duration = await asyncio.to_thread(get_video_duration, str(file_path))
        return await identify_episode_chromaprint(
            matcher=self._matcher, video_file=str(file_path), season_number=season,
            chromaprint_matcher=cm, extractor=extractor, video_duration=video_duration,
        )
```

Now rewrite `match_single_file`'s body (after the `_ensure_initialized` / matcher-availability guards) to:

```python
        # Phase 3 cascade: chromaprint first.
        cp = None
        if series_name and season:
            try:
                cp = await self._chromaprint_prepass(file_path=file_path, series_name=series_name, season=season)
            except Exception as e:  # noqa: BLE001 — never block matching
                logger.warning(f"chromaprint prepass failed: {e}")

        if cp and cp.get("episode") is not None:
            cp_conf = cp.get("confidence", 0.0)
            cp_code = f"S{cp['season']:02d}E{cp['episode']:02d}"
            if cp.get("tier") == "canonical" and cp_conf >= self.CHROMAPRINT_GATE:
                details = dict(cp.get("match_details") or {})
                details["chromaprint_accepted"] = True
                details["match_source"] = "engram_chromaprint"
                return MatchResult(file_path=file_path, episode_code=cp_code, episode_title=None,
                                   confidence=cp_conf, needs_review=False, match_details=details)

        # Fall through to ASR.
        asr = await self._run_asr_identify(file_path, series_name, season, progress_callback,
                                           num_points, min_vote_count)

        # Cross-validate when both produced an episode.
        if cp and cp.get("episode") is not None and asr.episode_code:
            cp_code = f"S{cp['season']:02d}E{cp['episode']:02d}"
            details = dict(asr.match_details or {})
            if cp_code == asr.episode_code:
                details["chromaprint_asr_agreement"] = True
                return MatchResult(file_path=file_path, episode_code=asr.episode_code,
                                   episode_title=asr.episode_title,
                                   confidence=max(asr.confidence, cp.get("confidence", 0.0)),
                                   needs_review=False, match_details=details)
            details["chromaprint_vs_asr_conflict"] = {
                "chromaprint": {"episode_code": cp_code, "confidence": cp.get("confidence")},
                "asr": {"episode_code": asr.episode_code, "confidence": asr.confidence},
            }
            return MatchResult(file_path=file_path, episode_code=asr.episode_code,
                               episode_title=asr.episode_title, confidence=asr.confidence,
                               needs_review=True, match_details=details)
        return asr
```

- [ ] **Step G2.4: Run (expect PASS) + commit**

```bash
cd backend && uv run pytest tests/integration/test_chromaprint_cascade.py -v 2>&1 | tail -20
uv run pytest tests/unit -k curator -v 2>&1 | tail -15
git add app/core/curator.py app/models/app_config.py backend/migrations/versions/*phase3* tests/integration/test_chromaprint_cascade.py
git commit -m "feat(curator): chromaprint-first cascade with ASR fallback + cross-validation"
```

### Task G3: Corroboration upload + shared PackCache wiring

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`
- Test: extend `backend/tests/integration/test_chromaprint_cascade.py`

- [ ] **Step G3.1: Map the chromaprint match source for corroboration**

In `backend/app/services/matching_coordinator.py`, extend `_MATCH_SOURCE_TO_CONTRIB` (~line 65) so a chromaprint win uploads as corroboration:

```python
    "engram_chromaprint": "engram_chromaprint_corroboration",
```

(`engram_chromaprint_corroboration` is already in the server's `MATCH_SOURCE_ALLOWLIST` — `src/types.ts:16`.)

- [ ] **Step G3.2: Set `title.match_source` when chromaprint won**

In the MATCHED branch where `title.match_source` is assigned, derive it from `match_details`:

```python
            if (result.match_details or {}).get("chromaprint_accepted"):
                title.match_source = "engram_chromaprint"
```

so the existing `ContributionQueue().enqueue(...)` call carries `engram_chromaprint_corroboration` via the source map. No uploader change is needed.

- [ ] **Step G3.3: Provide a shared PackCache to the curator**

Where the coordinator constructs/holds `episode_curator`, attach a process-shared cache once:

```python
        from app.services.fingerprint_pack_cache import PackCache
        if not hasattr(episode_curator, "_pack_cache"):
            episode_curator._pack_cache = PackCache()
```

- [ ] **Step G3.4: Write the failing integration test**

Append to `backend/tests/integration/test_chromaprint_cascade.py`:

```python
@pytest.mark.asyncio
async def test_chromaprint_accept_enqueues_corroboration(monkeypatch):
    """A chromaprint-accepted title maps to the corroboration contribution source."""
    from app.services.matching_coordinator import _MATCH_SOURCE_TO_CONTRIB
    assert _MATCH_SOURCE_TO_CONTRIB.get("engram_chromaprint") == "engram_chromaprint_corroboration"
```

- [ ] **Step G3.5: Run (expect PASS) + commit**

```bash
cd backend && uv run pytest tests/integration/test_chromaprint_cascade.py -v 2>&1 | tail -20
git add app/services/matching_coordinator.py tests/integration/test_chromaprint_cascade.py
git commit -m "feat(matching): corroboration uploads + shared PackCache wiring"
```

---

## Final verification

- [ ] **Server full suite + typecheck**

```bash
cd /c/Github/engram-fingerprint-server && pnpm test 2>&1 | tail -25 && pnpm typecheck
```

- [ ] **Client full suite + lint**

```bash
cd backend && uv run pytest tests/unit tests/integration -q 2>&1 | tail -25
uv run ruff check app/matcher/chromaprint_scoring.py app/matcher/chromaprint_matcher.py app/services/fingerprint_pack_cache.py
```

- [ ] **Manual end-to-end (after Track B seeds the catalog):** Deploy the server (`pnpm deploy`), enable `enable_fingerprint_identification`, set a dev `fingerprint_server_url`, and run a real rip of a seeded show. Confirm chromaprint identifies it (logs show `match_source=engram_chromaprint`, no ASR), `/v1/pack/{tmdb_id}` is fetched once, and a corroboration contribution flows. See `project_real_disc_testing_setup` memory for the worktree-uvicorn + real-DB setup.

---

## Risks / decisions

1. **Offset-less canonical data** → no `hash_index`, contiguity-based temporal coherence, approximate IDF rarity. Real inverted index + offsets is Phase 4.
2. **`enable_fingerprint_identification` defaults OFF.** Identification only activates once a seeded catalog exists (Track B). This avoids shipping a feature that returns empty candidates and wastes a remote round-trip on every window.
3. **`CHROMAPRINT_GATE = 0.90` + `tier == "canonical"`.** Conservative. Lowering to 0.80 is a Phase 4 data-driven task.
4. **D1 full-table scan** in `screenIdentify` (no partition key for identify). Fine for the seed catalog; cap exact-confirm candidates at 8; target < 200 ms p95. Phase 4 inverted index removes the ceiling.
5. **Client/server scoring asymmetry:** local backend uses exact membership; server uses Hamming ≤ 6 via `exactOverlap`. Golden parity vectors lock the *formula*; the membership tolerance difference is accepted (local pack = high-fidelity same-source).
6. **`get_video_duration` import path** in `_chromaprint_prepass` — confirm it's exported from `episode_identification.py` during execution (it is used internally there); if module-private, expose it or call `ffprobe` directly.

---

## Self-review notes

- **Spec coverage:** server identify (S1-S3), pack serving (S4), pack DF for rarity (S5); client scoring (C1), calibration (C2), pack cache (C3), matcher backends (C4), orchestration (G1), cascade + cross-validate (G2), corroboration (G3). Bootstrap UI + privacy doc + catalog seeding are Track B (separate plan).
- **Type consistency:** `WindowCandidate` fields match across backends and `identify_episode_chromaprint`; `combined_window_score`/`combinedScore` use identical weights; `chromaprint_signal` keys (`hash_overlap`, `temporal_coherence`, `rarity_weighted_score`) match between `identify_episode_chromaprint`, `calibrate_confidence`, and the cascade.
- **Golden vectors** are hand-computable and identical in both repos.

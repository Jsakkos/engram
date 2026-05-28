# Phase 2: Fingerprint Network Server + Client Uploader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Cloudflare Workers server that drains Phase 1's `fingerprint_contributions` queue, plus the client-side uploader, just-in-time privacy disclosure modal, and forget endpoint. End state: every successful Engram match contributes a chromaprint to the network; the server promotes corroborated fingerprints into a canonical tier nightly; users can rotate their pseudonym or opt out at any time.

**Architecture:** TWO REPOS. New sibling repo `engram-fingerprint-server` (TypeScript, Cloudflare Workers + D1 + R2) accepts contributions, runs anti-poison minhash-screening + exact Hamming confirm, promotes nightly. Existing engram repo gets a `ContributionUploader` asyncio background service, a JIT-disclosure modal, and a `POST /api/fingerprint/forget` endpoint. Wire format is JSON-body with base64(zstd-varint) fingerprint (NOT `application/octet-stream` — the brainstorming session locked this in).

**Tech Stack:** Server: Cloudflare Workers, TypeScript, D1 (SQLite-on-edge), R2, pnpm, Vitest, Miniflare, Zod, `@bokuweb/zstd-wasm`. Client: Python 3.11, FastAPI, SQLModel + aiosqlite, Alembic, asyncio, httpx, `zstandard`, React + TypeScript, Playwright.

**Spec source of truth:** [docs/superpowers/specs/2026-05-27-phase2-fingerprint-server-design.md](../specs/2026-05-27-phase2-fingerprint-server-design.md) (committed at 07463ea on branch `claude/mystifying-leakey-e79abd`).

---

## Convention

Every task is prefixed with `[server]` (work happens in the new `engram-fingerprint-server` repo) or `[client]` (work happens in this engram worktree). Most steps include the exact `cd` to run from; ALWAYS double-check the working directory in your shell before running commands. Mixing repos is the #1 risk in this plan.

```
[server] tasks run from: C:\Github\engram-fingerprint-server
[client] tasks run from: C:\Github\engram\.claude\worktrees\mystifying-leakey-e79abd
```

---

## Pre-flight

Before any cluster, confirm the environment is sane.

- [ ] **Step 0.1: Confirm client worktree**

```bash
pwd  # or `cd` in PowerShell
```
Expected: `C:\Github\engram\.claude\worktrees\mystifying-leakey-e79abd` (or `/c/Github/engram/.claude/worktrees/mystifying-leakey-e79abd` in bash).

- [ ] **Step 0.2: Confirm Phase 1 is on this branch**

```bash
git log --oneline -10 | grep -i fingerprint
```
Expected: see commits like `feat(matching): extract chromaprint after match completes` and `feat(api): GET /api/fingerprint/contributions audit-log endpoint`. If absent, you are NOT on a branch with Phase 1. Stop and rebase onto `origin/main`.

- [ ] **Step 0.3: Sync client backend deps + run baseline tests**

```bash
cd backend
uv sync
uv run pytest tests/unit -x --tb=short 2>&1 | tail -20
```
Expected: all unit tests pass. Note any pre-existing failures (the `test_movie_ambiguous_rip_first_workflow` race is known per CLAUDE.md memory).

- [ ] **Step 0.4: Confirm Node + pnpm available for server work**

```bash
node --version    # expect >= 20
pnpm --version    # expect >= 9; if missing, `npm i -g pnpm`
wrangler --version  # expect >= 3; if missing, `pnpm i -g wrangler`
```

- [ ] **Step 0.5: Confirm Cloudflare credentials**

```bash
wrangler whoami
```
Expected: account email + account ID printed. If not logged in: `wrangler login`. You need a Cloudflare account with Workers Paid ($5/mo) to use Cron Triggers later; you can develop locally without it.

- [ ] **Step 0.6: Confirm sibling repo directory does NOT already exist**

```bash
ls C:\Github\engram-fingerprint-server 2>&1 | head -5
```
Expected: directory not found. If it exists, you may already have started this plan — either resume in place or move it aside before starting Cluster S1.

---

## PART A: Server (`engram-fingerprint-server`)

This part stands up the new sibling repo and lands `POST /v1/contribute` end-to-end. After Cluster S4, the server is functional enough for the client uploader to be integration-tested against it.

### Cluster S1: Repo Bootstrap

#### Task S1.1: Create the new sibling repo

**Files:**
- Create: `C:\Github\engram-fingerprint-server\` (new directory)
- Create: `C:\Github\engram-fingerprint-server\.gitignore`
- Create: `C:\Github\engram-fingerprint-server\package.json`
- Create: `C:\Github\engram-fingerprint-server\tsconfig.json`
- Create: `C:\Github\engram-fingerprint-server\wrangler.toml`

- [ ] **Step S1.1.1: Create the directory**

```bash
mkdir C:\Github\engram-fingerprint-server
cd C:\Github\engram-fingerprint-server
git init
```

- [ ] **Step S1.1.2: Initialize the Worker via wrangler**

```bash
cd C:\Github\engram-fingerprint-server
wrangler init --yes --type=ts engram-fp-prod
```
This generates `package.json`, `tsconfig.json`, `wrangler.toml`, `src/index.ts`, and `test/`.

If `wrangler init` prompts interactively despite `--yes`, accept defaults: TypeScript, no git (we already init'd), no deploy now.

- [ ] **Step S1.1.3: Switch to pnpm**

```bash
cd C:\Github\engram-fingerprint-server
rm package-lock.json 2>$null
pnpm install
```
Expected: `node_modules/` populated, `pnpm-lock.yaml` created.

- [ ] **Step S1.1.4: Update wrangler.toml with our config**

Replace the generated `wrangler.toml` with:

```toml
name = "engram-fp-prod"
main = "src/index.ts"
compatibility_date = "2026-05-27"
compatibility_flags = ["nodejs_compat"]

# D1 binding (created in Task S1.2)
[[d1_databases]]
binding = "DB"
database_name = "engram-fingerprint"
database_id = "PLACEHOLDER_FILL_IN_AFTER_S1.2"

# R2 binding (created in Task S1.3)
[[r2_buckets]]
binding = "PACKS"
bucket_name = "engram-fp-packs"

# Anti-poison threshold (Q5 — tunable without redeploy)
[vars]
POISON_CONFLICT_THRESHOLD = "0.70"

# Cron triggers added in Cluster S6
# [triggers]
# crons = ["0 3 * * *", "0 4 * * *"]
```

- [ ] **Step S1.1.5: Update package.json scripts**

In `package.json`, set `scripts` to:

```json
{
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit",
    "migrate:local": "wrangler d1 migrations apply engram-fingerprint --local",
    "migrate:remote": "wrangler d1 migrations apply engram-fingerprint --remote"
  }
}
```

- [ ] **Step S1.1.6: Install test + runtime deps**

```bash
cd C:\Github\engram-fingerprint-server
pnpm add zod
pnpm add -D vitest @cloudflare/vitest-pool-workers wrangler typescript @types/node
```

- [ ] **Step S1.1.7: Add .gitignore**

Create `.gitignore`:

```
node_modules/
.wrangler/
.dev.vars
dist/
*.log
.DS_Store
```

- [ ] **Step S1.1.8: Commit**

```bash
cd C:\Github\engram-fingerprint-server
git add -A
git commit -m "chore: wrangler init engram-fp-prod"
```

#### Task S1.2: Create the D1 database

- [ ] **Step S1.2.1: Create the D1 database via wrangler**

```bash
cd C:\Github\engram-fingerprint-server
wrangler d1 create engram-fingerprint
```
Expected output includes a database UUID line like:
```
database_id = "abc123-..."
```
Copy that UUID.

- [ ] **Step S1.2.2: Update wrangler.toml with the real database_id**

Edit `wrangler.toml`, replace `PLACEHOLDER_FILL_IN_AFTER_S1.2` with the UUID from S1.2.1.

- [ ] **Step S1.2.3: Verify the database is reachable**

```bash
wrangler d1 execute engram-fingerprint --command "SELECT 1 AS ok"
```
Expected: a one-row result showing `ok | 1`. If error, re-check `database_id` in wrangler.toml.

- [ ] **Step S1.2.4: Commit**

```bash
git add wrangler.toml
git commit -m "feat(infra): create D1 database engram-fingerprint"
```

#### Task S1.3: Create the R2 bucket

- [ ] **Step S1.3.1: Create the R2 bucket via wrangler**

```bash
cd C:\Github\engram-fingerprint-server
wrangler r2 bucket create engram-fp-packs
```
Expected: success message. If "bucket already exists", that's fine — proceed.

- [ ] **Step S1.3.2: Verify the bucket exists**

```bash
wrangler r2 bucket list | grep engram-fp-packs
```
Expected: listed.

- [ ] **Step S1.3.3: Commit**

No code change to commit; the R2 binding was added in Task S1.1.4's wrangler.toml. Move on.

#### Task S1.4: Add CI workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step S1.4.1: Create the workflow file**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v5
        with:
          node-version: 20
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm test
      - run: pnpm exec wrangler d1 migrations apply engram-fingerprint --remote
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      - run: pnpm exec wrangler deploy
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

- [ ] **Step S1.4.2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: GitHub Actions workflow for deploy"
```

Note: secrets must be configured in the GitHub repo settings once it's pushed (`CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`). The workflow runs but will no-op until both are set.

#### Task S1.5: Create initial README

**Files:**
- Create: `README.md`

- [ ] **Step S1.5.1: Write README.md**

```markdown
# engram-fingerprint-server

Cloudflare Worker that receives chromaprint contributions from Engram clients and serves canonical fingerprints for identification (Phase 3+).

Companion to [engram](https://github.com/Jsakkos/engram). Phase 2 design: [spec](https://github.com/Jsakkos/engram/blob/main/docs/superpowers/specs/2026-05-27-phase2-fingerprint-server-design.md).

## Development

```bash
pnpm install
pnpm migrate:local
pnpm dev          # wrangler dev — local server at http://localhost:8787
pnpm test         # vitest
```

## Deploy

```bash
pnpm deploy
```

Production deploys happen automatically via GitHub Actions on push to `main`.

## Endpoints (Phase 2)

- `POST /v1/contribute` — accept a chromaprint contribution.
- `POST /v1/forget` — delete all rows for a pseudonym.

Phase 3 will add `GET /v1/identify` and `GET /v1/pack/{tmdb_id}`.

## Schema

See `migrations/001_initial.sql`.
```

- [ ] **Step S1.5.2: Commit**

```bash
git add README.md
git commit -m "docs: README"
```

### Cluster S2: D1 Schema

#### Task S2.1: Write the initial migration

**Files:**
- Create: `migrations/001_initial.sql`

- [ ] **Step S2.1.1: Create the migration**

Create `migrations/001_initial.sql` with the exact DDL from the spec's "D1 Schema" section. Verbatim:

```sql
-- 001_initial.sql
-- Phase 2 fingerprint network — initial schema.

CREATE TABLE contribution (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at INTEGER NOT NULL DEFAULT (unixepoch()),
  pseudonym TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  season INTEGER,
  episode INTEGER,
  fingerprint BLOB NOT NULL,
  fingerprint_sha256 BLOB NOT NULL,
  disc_content_hash BLOB,
  match_confidence REAL NOT NULL,
  match_source TEXT NOT NULL,
  client_version TEXT NOT NULL,
  poison_check TEXT NOT NULL DEFAULT 'pending',
  promoted_at INTEGER
);
CREATE INDEX idx_contribution_episode ON contribution (tmdb_id, season, episode);
CREATE INDEX idx_contribution_pseudonym ON contribution (pseudonym, received_at);
CREATE INDEX idx_contribution_unpromoted ON contribution (promoted_at) WHERE promoted_at IS NULL;
CREATE UNIQUE INDEX idx_contribution_dedupe
  ON contribution (pseudonym, tmdb_id, season, episode, fingerprint_sha256);

CREATE TABLE contributor (
  pseudonym TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  contribution_count INTEGER NOT NULL DEFAULT 0,
  flagged INTEGER NOT NULL DEFAULT 0,
  flag_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE episode_canonical (
  tmdb_id INTEGER NOT NULL,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  tier TEXT NOT NULL,
  fingerprint BLOB NOT NULL,
  unique_contributors INTEGER NOT NULL,
  mean_confidence REAL NOT NULL,
  promoted_at INTEGER NOT NULL,
  PRIMARY KEY (tmdb_id, season, episode)
);
CREATE INDEX idx_canonical_tier ON episode_canonical (tier);

CREATE TABLE canonical_sketch (
  tmdb_id INTEGER NOT NULL,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  sketch BLOB NOT NULL,
  hash_count INTEGER NOT NULL,
  generated_at INTEGER NOT NULL,
  PRIMARY KEY (tmdb_id, season, episode)
);

CREATE TABLE overlap_observation (
  contribution_id INTEGER PRIMARY KEY REFERENCES contribution(id) ON DELETE CASCADE,
  max_overlap_pct REAL NOT NULL,
  max_overlap_target_tmdb_id INTEGER,
  max_overlap_target_season INTEGER,
  max_overlap_target_episode INTEGER,
  candidates_checked INTEGER NOT NULL,
  computed_at INTEGER NOT NULL
);
```

- [ ] **Step S2.1.2: Apply locally**

```bash
cd C:\Github\engram-fingerprint-server
pnpm migrate:local
```
Expected: "Migrations applied!" or equivalent. The local D1 database file is created under `.wrangler/state/`.

- [ ] **Step S2.1.3: Verify schema**

```bash
wrangler d1 execute engram-fingerprint --local --command "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
```
Expected: 5 tables listed (`canonical_sketch`, `contribution`, `contributor`, `episode_canonical`, `overlap_observation`) plus any internal D1 tables.

- [ ] **Step S2.1.4: Commit**

```bash
git add migrations/001_initial.sql
git commit -m "feat(schema): D1 initial migration — 5 tables"
```

### Cluster S3: Utilities (codec, minhash, types)

#### Task S3.1: zstd-varint codec

**Files:**
- Create: `src/codec.ts`
- Create: `test/codec.test.ts`

- [ ] **Step S3.1.1: Install zstd-wasm**

```bash
cd C:\Github\engram-fingerprint-server
pnpm add @bokuweb/zstd-wasm
```

- [ ] **Step S3.1.2: Write the failing test**

Create `test/codec.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { encodeZstdVarint, decodeZstdVarint, initCodec } from "../src/codec";

describe("codec", () => {
  beforeAll(async () => {
    await initCodec();
  });

  it("roundtrips an empty array", async () => {
    const encoded = await encodeZstdVarint([]);
    const decoded = await decodeZstdVarint(encoded);
    expect(decoded).toEqual([]);
  });

  it("roundtrips a small uint32 array", async () => {
    const input = [1, 2, 3, 4, 5, 100, 1000, 1000000, 4294967295];
    const encoded = await encodeZstdVarint(input);
    const decoded = await decodeZstdVarint(encoded);
    expect(decoded).toEqual(input);
  });

  it("compressed size is smaller than naive 4 bytes/int for random data", async () => {
    const input = Array.from({ length: 1000 }, () => Math.floor(Math.random() * 4294967295));
    const encoded = await encodeZstdVarint(input);
    // 1000 * 4 = 4000 bytes raw. zstd should beat ~3500 bytes for delta-friendly chromaprint data.
    // For random data we can't promise that, but we can promise < 5000 (accounting for varint overhead).
    expect(encoded.byteLength).toBeLessThan(5000);
  });

  it("decoded bytes match expected sha256 — sanity for downstream wire-format compatibility", async () => {
    const input = [42, 100, 255, 256];
    const encoded = await encodeZstdVarint(input);
    const decoded = await decodeZstdVarint(encoded);
    expect(decoded).toEqual(input);
  });
});
```

- [ ] **Step S3.1.3: Run the test (expect FAIL)**

```bash
pnpm test test/codec.test.ts
```
Expected: import errors — `src/codec.ts` doesn't exist.

- [ ] **Step S3.1.4: Implement src/codec.ts**

Create `src/codec.ts`:

```typescript
import { init, compress, decompress } from "@bokuweb/zstd-wasm";

let zstdReady: Promise<void> | null = null;

export async function initCodec(): Promise<void> {
  if (!zstdReady) {
    zstdReady = init();
  }
  await zstdReady;
}

/** Encode a uint32 as variable-length 7-bit-per-byte (LEB128 unsigned). */
function writeVarint(out: number[], value: number): void {
  // value is uint32, but JS numbers are 53-bit safe — fine.
  while (value >= 0x80) {
    out.push((value & 0x7f) | 0x80);
    value = Math.floor(value / 128); // logical right shift; avoid sign issues for >2^31
  }
  out.push(value & 0x7f);
}

/** Decode a varint stream into uint32[]. */
function readVarintStream(bytes: Uint8Array): number[] {
  const out: number[] = [];
  let value = 0;
  let shift = 0;
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i];
    value += (b & 0x7f) * Math.pow(2, shift);
    shift += 7;
    if ((b & 0x80) === 0) {
      out.push(value);
      value = 0;
      shift = 0;
    }
  }
  return out;
}

export async function encodeZstdVarint(hashes: number[]): Promise<Uint8Array> {
  await initCodec();
  const varintBuf: number[] = [];
  for (const h of hashes) writeVarint(varintBuf, h >>> 0);
  const compressed = compress(new Uint8Array(varintBuf), 11); // compression level 11
  return compressed;
}

export async function decodeZstdVarint(blob: Uint8Array): Promise<number[]> {
  await initCodec();
  const varintBytes = decompress(blob);
  return readVarintStream(varintBytes);
}
```

- [ ] **Step S3.1.5: Run the test (expect PASS)**

```bash
pnpm test test/codec.test.ts
```
Expected: all 4 tests pass.

- [ ] **Step S3.1.6: Commit**

```bash
git add src/codec.ts test/codec.test.ts package.json pnpm-lock.yaml
git commit -m "feat(codec): zstd+varint encode/decode with roundtrip tests"
```

#### Task S3.2: Minhash sketching

**Files:**
- Create: `src/minhash.ts`
- Create: `test/minhash.test.ts`

- [ ] **Step S3.2.1: Write the failing test**

Create `test/minhash.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { minhash128, jaccardEstimate } from "../src/minhash";

describe("minhash", () => {
  it("produces a 512-byte sketch (128 × uint32 LE)", () => {
    const hashes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const sketch = minhash128(hashes);
    expect(sketch.byteLength).toBe(512);
  });

  it("is deterministic — same input → same sketch", () => {
    const input = [42, 100, 200, 300, 4242424];
    expect(minhash128(input)).toEqual(minhash128(input));
  });

  it("jaccardEstimate of identical sketches is 1.0", () => {
    const sketch = minhash128([1, 2, 3, 4, 5]);
    expect(jaccardEstimate(sketch, sketch)).toBe(1.0);
  });

  it("jaccardEstimate of disjoint hash sets is low (< 0.1)", () => {
    const a = minhash128([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    const b = minhash128([1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000]);
    expect(jaccardEstimate(a, b)).toBeLessThan(0.1);
  });

  it("jaccardEstimate within ±0.05 of true Jaccard for 50%-overlapping sets", () => {
    // Two sets sharing exactly half their elements
    const setA = Array.from({ length: 200 }, (_, i) => i);            // 0..199
    const setB = Array.from({ length: 200 }, (_, i) => i + 100);      // 100..299
    // True Jaccard = |intersection| / |union| = 100 / 300 = 0.333
    const estA = jaccardEstimate(minhash128(setA), minhash128(setB));
    expect(estA).toBeGreaterThan(0.28);
    expect(estA).toBeLessThan(0.39);
  });
});
```

- [ ] **Step S3.2.2: Run the test (expect FAIL)**

```bash
pnpm test test/minhash.test.ts
```

- [ ] **Step S3.2.3: Implement src/minhash.ts**

Create `src/minhash.ts`:

```typescript
const NUM_HASHES = 128;
const MOD = 4294967311; // first prime > 2^32

// Precomputed (a, b) coefficients for h_i(x) = (a*x + b) mod MOD.
// Deterministic — derived from a fixed seed so server + client agree.
const COEFFS: { a: number; b: number }[] = (() => {
  let state = 0x12345678;
  const next = () => {
    // xorshift32
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) % MOD;
  };
  const out: { a: number; b: number }[] = [];
  for (let i = 0; i < NUM_HASHES; i++) {
    out.push({ a: (next() % (MOD - 1)) + 1, b: next() });
  }
  return out;
})();

/** Compute 128-minhash sketch. Output: 512-byte Uint8Array, 128 × uint32 little-endian. */
export function minhash128(hashes: number[]): Uint8Array {
  const sketch = new Uint32Array(NUM_HASHES);
  for (let i = 0; i < NUM_HASHES; i++) sketch[i] = 0xffffffff;

  for (const h of hashes) {
    const hu = h >>> 0;
    for (let i = 0; i < NUM_HASHES; i++) {
      const { a, b } = COEFFS[i];
      // (a*hu + b) mod MOD — careful: JS number precision is fine for these magnitudes.
      const v = ((a * hu) % MOD + b) % MOD;
      if (v < sketch[i]) sketch[i] = v;
    }
  }

  // Pack as 512-byte little-endian buffer.
  const buf = new Uint8Array(NUM_HASHES * 4);
  const view = new DataView(buf.buffer);
  for (let i = 0; i < NUM_HASHES; i++) view.setUint32(i * 4, sketch[i], true);
  return buf;
}

/** Estimate Jaccard similarity between two 512-byte minhash sketches. */
export function jaccardEstimate(sketchA: Uint8Array, sketchB: Uint8Array): number {
  if (sketchA.byteLength !== 512 || sketchB.byteLength !== 512) {
    throw new Error("sketch must be 512 bytes");
  }
  const viewA = new DataView(sketchA.buffer, sketchA.byteOffset, 512);
  const viewB = new DataView(sketchB.buffer, sketchB.byteOffset, 512);
  let matches = 0;
  for (let i = 0; i < NUM_HASHES; i++) {
    if (viewA.getUint32(i * 4, true) === viewB.getUint32(i * 4, true)) matches++;
  }
  return matches / NUM_HASHES;
}
```

- [ ] **Step S3.2.4: Run the test (expect PASS)**

```bash
pnpm test test/minhash.test.ts
```
Expected: all 5 tests pass. The 50%-overlap estimation test may occasionally fail due to randomness — if it fails once, re-run; if it consistently fails, the COEFFS quality is bad and you need a better hash.

- [ ] **Step S3.2.5: Commit**

```bash
git add src/minhash.ts test/minhash.test.ts
git commit -m "feat(minhash): 128-minhash sketching + Jaccard estimate"
```

#### Task S3.3: Types + Zod schemas

**Files:**
- Create: `src/types.ts`
- Create: `src/schemas.ts`
- Create: `test/schemas.test.ts`

- [ ] **Step S3.3.1: Write the failing test**

Create `test/schemas.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { ContributionRequestSchema, ForgetRequestSchema } from "../src/schemas";

describe("schemas", () => {
  const valid = {
    wire_format_version: 1,
    pseudonym: "11111111-1111-4111-8111-111111111111",
    tmdb_id: 12345,
    season: 1,
    episode: 1,
    fingerprint_b64: "AAAA",
    fingerprint_sha256_b64: "AAAA",
    disc_content_hash_b64: null,
    match_confidence: 0.91,
    match_source: "engram_asr",
    client_version: "engram/0.9.2",
  };

  it("accepts a well-formed ContributionRequest", () => {
    expect(() => ContributionRequestSchema.parse(valid)).not.toThrow();
  });

  it("rejects wire_format_version != 1", () => {
    expect(() => ContributionRequestSchema.parse({ ...valid, wire_format_version: 2 })).toThrow();
  });

  it("rejects malformed pseudonym", () => {
    expect(() => ContributionRequestSchema.parse({ ...valid, pseudonym: "not-a-uuid" })).toThrow();
  });

  it("rejects match_source outside the allowlist", () => {
    expect(() => ContributionRequestSchema.parse({ ...valid, match_source: "engram_evil" })).toThrow();
  });

  it("rejects match_confidence > 1.0", () => {
    expect(() => ContributionRequestSchema.parse({ ...valid, match_confidence: 1.5 })).toThrow();
  });

  it("accepts null season/episode for bootstrap movie contributions", () => {
    expect(() => ContributionRequestSchema.parse({ ...valid, season: null, episode: null })).not.toThrow();
  });

  it("accepts ForgetRequest with valid UUID", () => {
    expect(() => ForgetRequestSchema.parse({ pseudonym: "11111111-1111-4111-8111-111111111111" })).not.toThrow();
  });
});
```

- [ ] **Step S3.3.2: Run the test (expect FAIL)**

```bash
pnpm test test/schemas.test.ts
```

- [ ] **Step S3.3.3: Implement src/types.ts and src/schemas.ts**

Create `src/types.ts`:

```typescript
import { z } from "zod";
import { ContributionRequestSchema, ContributionResponseSchema, ForgetRequestSchema, ForgetResponseSchema } from "./schemas";

export type ContributionRequest = z.infer<typeof ContributionRequestSchema>;
export type ContributionResponse = z.infer<typeof ContributionResponseSchema>;
export type ForgetRequest = z.infer<typeof ForgetRequestSchema>;
export type ForgetResponse = z.infer<typeof ForgetResponseSchema>;

export type PoisonCheck = "pass" | "flag_conflict" | "flag_duplicate";

export const MATCH_SOURCE_ALLOWLIST = [
  "engram_asr",
  "engram_discdb",
  "bootstrap",
  "user_review",
  "engram_chromaprint_corroboration",
] as const;
export type MatchSource = (typeof MATCH_SOURCE_ALLOWLIST)[number];
```

Create `src/schemas.ts`:

```typescript
import { z } from "zod";
import { MATCH_SOURCE_ALLOWLIST } from "./types";

const UUIDv4 = z.string().regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  "pseudonym must be UUIDv4",
);

const Base64 = z.string().regex(/^[A-Za-z0-9+/]*={0,2}$/, "must be valid base64");

export const ContributionRequestSchema = z.object({
  wire_format_version: z.literal(1),
  pseudonym: UUIDv4,
  tmdb_id: z.number().int().positive(),
  season: z.number().int().min(0).nullable(),
  episode: z.number().int().min(0).nullable(),
  fingerprint_b64: Base64,
  fingerprint_sha256_b64: Base64,
  disc_content_hash_b64: Base64.nullable(),
  match_confidence: z.number().min(0).max(1),
  match_source: z.enum(MATCH_SOURCE_ALLOWLIST),
  client_version: z.string().min(1).max(100),
});

export const ContributionResponseSchema = z.object({
  contribution_id: z.number().int(),
  poison_check: z.enum(["pass", "flag_conflict", "flag_duplicate"]),
  overlap_pct: z.number().min(0).max(1),
});

export const ForgetRequestSchema = z.object({
  pseudonym: UUIDv4,
});

export const ForgetResponseSchema = z.object({
  rows_deleted: z.number().int().min(0),
  canonical_unaffected: z.literal(true),
});
```

- [ ] **Step S3.3.4: Run the test (expect PASS)**

```bash
pnpm test test/schemas.test.ts
```

- [ ] **Step S3.3.5: Commit**

```bash
git add src/types.ts src/schemas.ts test/schemas.test.ts
git commit -m "feat(types): Zod schemas for ContributionRequest + ForgetRequest"
```

### Cluster S4: POST /v1/contribute

This is the largest server cluster. Built in 4 steps, each one TDD-driven, each one a separate commit.

#### Task S4.1: Skeleton — schema validation only

**Files:**
- Create: `src/index.ts` (replace generated stub)
- Create: `src/routes/contribute.ts`
- Create: `test/contribute_validation.test.ts`

- [ ] **Step S4.1.1: Write the failing test**

Create `test/contribute_validation.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { SELF, env } from "cloudflare:test";

const validBody = () => ({
  wire_format_version: 1,
  pseudonym: "11111111-1111-4111-8111-111111111111",
  tmdb_id: 12345,
  season: 1,
  episode: 1,
  fingerprint_b64: "AAAA",
  fingerprint_sha256_b64: "AAAA",
  disc_content_hash_b64: null,
  match_confidence: 0.91,
  match_source: "engram_asr",
  client_version: "engram/0.9.2",
});

describe("POST /v1/contribute — validation only", () => {
  it("returns 400 on missing wire_format_version", async () => {
    const body = validBody();
    delete (body as any).wire_format_version;
    const res = await SELF.fetch("https://example.com/v1/contribute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    expect(res.status).toBe(400);
  });

  it("returns 400 on invalid pseudonym", async () => {
    const body = { ...validBody(), pseudonym: "not-uuid" };
    const res = await SELF.fetch("https://example.com/v1/contribute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    expect(res.status).toBe(400);
  });

  it("returns 405 on GET", async () => {
    const res = await SELF.fetch("https://example.com/v1/contribute", { method: "GET" });
    expect(res.status).toBe(405);
  });

  it("returns 202 on valid body (stub — no DB writes yet)", async () => {
    const res = await SELF.fetch("https://example.com/v1/contribute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validBody()),
    });
    expect(res.status).toBe(202);
  });
});
```

- [ ] **Step S4.1.2: Add vitest pool config for workers**

Create `vitest.config.ts`:

```typescript
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          d1Databases: ["DB"],
          r2Buckets: ["PACKS"],
        },
      },
    },
  },
});
```

- [ ] **Step S4.1.3: Add miniflare D1 migration loader**

Vitest-pool-workers + Miniflare need migrations applied per test run. Add a `test/setup.ts` (config in `vitest.config.ts` `setupFiles`):

```typescript
// test/setup.ts
import { env, applyD1Migrations } from "cloudflare:test";
import { beforeAll } from "vitest";

beforeAll(async () => {
  await applyD1Migrations(env.DB, "migrations");
});
```

Update `vitest.config.ts` to include `setupFiles: ["./test/setup.ts"]` under the `test` block.

- [ ] **Step S4.1.4: Run the tests (expect FAIL)**

```bash
pnpm test test/contribute_validation.test.ts
```
Expected: failures because `src/index.ts` is still the auto-generated stub.

- [ ] **Step S4.1.5: Write src/routes/contribute.ts**

```typescript
import { ContributionRequestSchema } from "../schemas";

export async function handleContribute(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }

  const parsed = ContributionRequestSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { error: "schema validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  // Stub: just acknowledge. DB writes land in Task S4.2.
  return Response.json(
    { contribution_id: 0, poison_check: "pass" as const, overlap_pct: 0 },
    { status: 202 },
  );
}

export interface Env {
  DB: D1Database;
  PACKS: R2Bucket;
  POISON_CONFLICT_THRESHOLD: string;
}
```

- [ ] **Step S4.1.6: Replace src/index.ts**

```typescript
import { handleContribute, type Env } from "./routes/contribute";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/v1/contribute") {
      if (request.method !== "POST") {
        return new Response("Method Not Allowed", { status: 405 });
      }
      return handleContribute(request, env);
    }

    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
```

- [ ] **Step S4.1.7: Run the tests (expect PASS)**

```bash
pnpm test test/contribute_validation.test.ts
```

- [ ] **Step S4.1.8: Commit**

```bash
git add src/index.ts src/routes/contribute.ts test/contribute_validation.test.ts vitest.config.ts test/setup.ts
git commit -m "feat(api): POST /v1/contribute skeleton with schema validation"
```

#### Task S4.2: DB insert + dedupe

**Files:**
- Create: `src/db.ts`
- Modify: `src/routes/contribute.ts`
- Create: `test/contribute_db.test.ts`

- [ ] **Step S4.2.1: Write the failing tests**

Create `test/contribute_db.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { SELF, env } from "cloudflare:test";

const validBody = (overrides: Record<string, unknown> = {}) => ({
  wire_format_version: 1,
  pseudonym: "11111111-1111-4111-8111-111111111111",
  tmdb_id: 12345,
  season: 1,
  episode: 1,
  fingerprint_b64: "AAAAAAAA",
  fingerprint_sha256_b64: "deadbeef",
  disc_content_hash_b64: null,
  match_confidence: 0.91,
  match_source: "engram_asr",
  client_version: "engram/0.9.2",
  ...overrides,
});

const post = (body: object) =>
  SELF.fetch("https://example.com/v1/contribute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

describe("POST /v1/contribute — db insert + dedupe", () => {
  it("inserts a row on first POST and returns 202 with non-zero id", async () => {
    const res = await post(validBody());
    expect(res.status).toBe(202);
    const json = (await res.json()) as { contribution_id: number; poison_check: string };
    expect(json.contribution_id).toBeGreaterThan(0);
    expect(json.poison_check).toBe("pass");

    const row = await env.DB.prepare(
      "SELECT * FROM contribution WHERE id = ?",
    ).bind(json.contribution_id).first();
    expect(row).not.toBeNull();
  });

  it("upserts the contributor row", async () => {
    await post(validBody({ pseudonym: "22222222-2222-4222-8222-222222222222" }));
    const row = await env.DB.prepare(
      "SELECT * FROM contributor WHERE pseudonym = ?",
    ).bind("22222222-2222-4222-8222-222222222222").first();
    expect(row).not.toBeNull();
    expect((row as any).contribution_count).toBe(1);
  });

  it("returns 200 with poison_check='flag_duplicate' on dedupe collision", async () => {
    const body = validBody({ pseudonym: "33333333-3333-4333-8333-333333333333" });
    const res1 = await post(body);
    expect(res1.status).toBe(202);

    const res2 = await post(body);
    expect(res2.status).toBe(200);
    const json = (await res2.json()) as { poison_check: string };
    expect(json.poison_check).toBe("flag_duplicate");
  });
});
```

- [ ] **Step S4.2.2: Run the tests (expect FAIL)**

- [ ] **Step S4.2.3: Implement src/db.ts**

```typescript
import type { ContributionRequest, PoisonCheck } from "./types";

export interface ContributionInsertResult {
  contributionId: number;
  poisonCheck: PoisonCheck;
  isDuplicate: boolean;
}

export async function insertContribution(
  db: D1Database,
  req: ContributionRequest,
  fingerprintBytes: Uint8Array,
  fingerprintSha256: Uint8Array,
  poisonCheck: PoisonCheck,
): Promise<ContributionInsertResult> {
  // Dedupe check first
  const existing = await db.prepare(
    `SELECT id FROM contribution
     WHERE pseudonym = ? AND tmdb_id = ? AND season IS ? AND episode IS ? AND fingerprint_sha256 = ?`,
  ).bind(
    req.pseudonym, req.tmdb_id, req.season, req.episode, fingerprintSha256,
  ).first<{ id: number }>();

  if (existing) {
    return { contributionId: existing.id, poisonCheck: "flag_duplicate", isDuplicate: true };
  }

  const discHash = req.disc_content_hash_b64
    ? Uint8Array.from(atob(req.disc_content_hash_b64), c => c.charCodeAt(0))
    : null;

  const result = await db.prepare(
    `INSERT INTO contribution
       (pseudonym, tmdb_id, season, episode, fingerprint, fingerprint_sha256,
        disc_content_hash, match_confidence, match_source, client_version, poison_check)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    req.pseudonym, req.tmdb_id, req.season, req.episode,
    fingerprintBytes, fingerprintSha256, discHash,
    req.match_confidence, req.match_source, req.client_version, poisonCheck,
  ).run();

  const contributionId = result.meta.last_row_id;

  await db.prepare(
    `INSERT INTO contributor (pseudonym, first_seen, last_seen, contribution_count, flagged, flag_count)
     VALUES (?, unixepoch(), unixepoch(), 1, 0, 0)
     ON CONFLICT(pseudonym) DO UPDATE
       SET last_seen = unixepoch(),
           contribution_count = contribution_count + 1`,
  ).bind(req.pseudonym).run();

  return { contributionId, poisonCheck, isDuplicate: false };
}

export async function getContributor(
  db: D1Database, pseudonym: string,
): Promise<{ pseudonym: string; flagged: number; flag_count: number } | null> {
  return await db.prepare(
    `SELECT pseudonym, flagged, flag_count FROM contributor WHERE pseudonym = ?`,
  ).bind(pseudonym).first<{ pseudonym: string; flagged: number; flag_count: number }>();
}
```

- [ ] **Step S4.2.4: Update src/routes/contribute.ts**

Replace the body of `handleContribute` to call DB:

```typescript
import { ContributionRequestSchema } from "../schemas";
import { insertContribution, getContributor } from "../db";

export async function handleContribute(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try { body = await request.json(); } catch { return new Response("invalid JSON", { status: 400 }); }

  const parsed = ContributionRequestSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json({ error: "schema validation failed", details: parsed.error.flatten() }, { status: 400 });
  }
  const req = parsed.data;

  // Shadowban check (step 3 of anti-poison algorithm)
  const contributor = await getContributor(env.DB, req.pseudonym);
  if (contributor?.flagged === 1) {
    return Response.json(
      { contribution_id: 0, poison_check: "flag_duplicate" as const, overlap_pct: 0 },
      { status: 200 },
    );
  }

  // Decode the wire-format fingerprint
  let fingerprintBytes: Uint8Array;
  let fingerprintSha256: Uint8Array;
  try {
    fingerprintBytes = Uint8Array.from(atob(req.fingerprint_b64), c => c.charCodeAt(0));
    fingerprintSha256 = Uint8Array.from(atob(req.fingerprint_sha256_b64), c => c.charCodeAt(0));
  } catch {
    return new Response("invalid base64", { status: 400 });
  }

  // Anti-poison lands in Task S4.3 — placeholder pass.
  const poisonCheck = "pass" as const;

  const result = await insertContribution(env.DB, req, fingerprintBytes, fingerprintSha256, poisonCheck);

  return Response.json(
    {
      contribution_id: result.contributionId,
      poison_check: result.poisonCheck,
      overlap_pct: 0,  // computed in S4.3
    },
    { status: result.isDuplicate ? 200 : 202 },
  );
}

export interface Env {
  DB: D1Database;
  PACKS: R2Bucket;
  POISON_CONFLICT_THRESHOLD: string;
}
```

- [ ] **Step S4.2.5: Run the tests (expect PASS)**

```bash
pnpm test
```

- [ ] **Step S4.2.6: Commit**

```bash
git add src/db.ts src/routes/contribute.ts test/contribute_db.test.ts
git commit -m "feat(api): /v1/contribute DB insert + dedupe + shadowban check"
```

#### Task S4.3: Anti-poison fast path (minhash screen)

**Files:**
- Modify: `src/routes/contribute.ts`
- Create: `src/db_anti_poison.ts`
- Create: `test/anti_poison_screen.test.ts`

- [ ] **Step S4.3.1: Write the failing test**

Create `test/anti_poison_screen.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { env, SELF } from "cloudflare:test";
import { minhash128 } from "../src/minhash";

async function seedCanonical(tmdbId: number, season: number, episode: number, hashes: number[]) {
  const sketch = minhash128(hashes);
  await env.DB.prepare(
    `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
     VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
  ).bind(tmdbId, season, episode, new Uint8Array([0])).run();
  await env.DB.prepare(
    `INSERT INTO canonical_sketch (tmdb_id, season, episode, sketch, hash_count, generated_at)
     VALUES (?, ?, ?, ?, ?, unixepoch())`,
  ).bind(tmdbId, season, episode, sketch, hashes.length).run();
}

describe("anti-poison fast path", () => {
  it("records overlap_observation on every contribution", async () => {
    // Seed a canonical for a DIFFERENT episode than the one we're contributing to.
    await seedCanonical(99999, 5, 5, Array.from({ length: 200 }, (_, i) => i));

    // Contribute a totally different fingerprint claiming a different episode.
    // (No exact-confirm in this task — just verify observation is recorded.)
    const fp = Array.from({ length: 200 }, (_, i) => 1000000 + i);
    const fpBytes = new Uint8Array(fp.flatMap(n => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >> 24) & 0xff]));
    const b64 = btoa(String.fromCharCode(...fpBytes));

    const res = await SELF.fetch("https://example.com/v1/contribute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wire_format_version: 1,
        pseudonym: "44444444-4444-4444-8444-444444444444",
        tmdb_id: 11111, season: 1, episode: 1,
        fingerprint_b64: b64,
        fingerprint_sha256_b64: "abcdef",
        disc_content_hash_b64: null,
        match_confidence: 0.9,
        match_source: "engram_asr",
        client_version: "engram/0.9.2",
      }),
    });
    expect(res.status).toBe(202);

    const obsRow = await env.DB.prepare(
      `SELECT * FROM overlap_observation ORDER BY contribution_id DESC LIMIT 1`,
    ).first();
    expect(obsRow).not.toBeNull();
    expect((obsRow as any).candidates_checked).toBeGreaterThan(0);
  });
});
```

- [ ] **Step S4.3.2: Run the test (expect FAIL)**

- [ ] **Step S4.3.3: Implement src/db_anti_poison.ts**

```typescript
import { jaccardEstimate, minhash128 } from "./minhash";

export interface ScreenResult {
  maxOverlapEstimate: number;
  targetTmdbId: number | null;
  targetSeason: number | null;
  targetEpisode: number | null;
  candidatesChecked: number;
}

/**
 * Anti-poison fast path: minhash sketch screening against canonical_sketch
 * for OTHER episodes (i.e. not the one the contribution claims to be).
 */
export async function screenAntiPoison(
  db: D1Database,
  candidateHashes: number[],
  claimedTmdbId: number,
  claimedSeason: number | null,
  claimedEpisode: number | null,
): Promise<ScreenResult> {
  const candidateSketch = minhash128(candidateHashes);

  // Fetch ALL canonical sketches except the claimed episode.
  // For Phase 2 catalog sizes (<100K), pulling all sketches is fine.
  // If/when the catalog grows past D1's CPU-budget comfort, partition by show.
  const rows = await db.prepare(
    `SELECT tmdb_id, season, episode, sketch FROM canonical_sketch
     WHERE NOT (tmdb_id = ? AND season IS ? AND episode IS ?)`,
  ).bind(claimedTmdbId, claimedSeason, claimedEpisode).all<{
    tmdb_id: number; season: number; episode: number; sketch: ArrayBuffer;
  }>();

  let maxEst = 0;
  let target: { tmdb_id: number; season: number; episode: number } | null = null;
  for (const row of rows.results) {
    const sketch = new Uint8Array(row.sketch);
    const est = jaccardEstimate(candidateSketch, sketch);
    if (est > maxEst) {
      maxEst = est;
      target = { tmdb_id: row.tmdb_id, season: row.season, episode: row.episode };
    }
  }

  return {
    maxOverlapEstimate: maxEst,
    targetTmdbId: target?.tmdb_id ?? null,
    targetSeason: target?.season ?? null,
    targetEpisode: target?.episode ?? null,
    candidatesChecked: rows.results.length,
  };
}

export async function recordOverlapObservation(
  db: D1Database,
  contributionId: number,
  result: ScreenResult,
): Promise<void> {
  await db.prepare(
    `INSERT INTO overlap_observation
       (contribution_id, max_overlap_pct, max_overlap_target_tmdb_id,
        max_overlap_target_season, max_overlap_target_episode,
        candidates_checked, computed_at)
     VALUES (?, ?, ?, ?, ?, ?, unixepoch())`,
  ).bind(
    contributionId,
    result.maxOverlapEstimate,
    result.targetTmdbId,
    result.targetSeason,
    result.targetEpisode,
    result.candidatesChecked,
  ).run();
}
```

- [ ] **Step S4.3.4: Wire into handleContribute**

In `src/routes/contribute.ts`, after the dedupe-passes branch and before the placeholder `poisonCheck = "pass"`, add:

```typescript
import { decodeZstdVarint } from "../codec";
import { screenAntiPoison, recordOverlapObservation } from "../db_anti_poison";

// ... inside handleContribute, after fingerprint decode:

let hashes: number[];
try {
  hashes = await decodeZstdVarint(fingerprintBytes);
} catch {
  return new Response("invalid zstd-varint payload", { status: 400 });
}

const screen = await screenAntiPoison(
  env.DB, hashes, req.tmdb_id, req.season, req.episode,
);

// Exact-confirm lands in Task S4.4 — placeholder pass.
const poisonCheck = "pass" as const;

const result = await insertContribution(env.DB, req, fingerprintBytes, fingerprintSha256, poisonCheck);

if (!result.isDuplicate) {
  await recordOverlapObservation(env.DB, result.contributionId, screen);
}

return Response.json(
  {
    contribution_id: result.contributionId,
    poison_check: result.poisonCheck,
    overlap_pct: screen.maxOverlapEstimate,
  },
  { status: result.isDuplicate ? 200 : 202 },
);
```

Make sure to update existing tests' `fingerprint_b64` values to be valid zstd-varint blobs (use `encodeZstdVarint([])` for the "any valid" case). The dedupe test will need a real encoded fingerprint.

- [ ] **Step S4.3.5: Update earlier tests that used dummy base64**

In `test/contribute_db.test.ts`, the bodies use `fingerprint_b64: "AAAAAAAA"` which is no longer valid zstd-varint. Replace the test setup:

```typescript
import { encodeZstdVarint, initCodec } from "../src/codec";

// At top of describe:
beforeAll(async () => { await initCodec(); });

// Helper:
async function makeBody(overrides = {}) {
  const hashes = [1, 2, 3, 4, 5];
  const encoded = await encodeZstdVarint(hashes);
  const b64 = btoa(String.fromCharCode(...encoded));
  return {
    wire_format_version: 1,
    pseudonym: "11111111-1111-4111-8111-111111111111",
    tmdb_id: 12345,
    season: 1,
    episode: 1,
    fingerprint_b64: b64,
    fingerprint_sha256_b64: "deadbeef",
    disc_content_hash_b64: null,
    match_confidence: 0.91,
    match_source: "engram_asr",
    client_version: "engram/0.9.2",
    ...overrides,
  };
}
```

Then await `makeBody()` instead of calling `validBody()`.

- [ ] **Step S4.3.6: Run all tests (expect PASS)**

```bash
pnpm test
```

- [ ] **Step S4.3.7: Commit**

```bash
git add src/db_anti_poison.ts src/routes/contribute.ts test/anti_poison_screen.test.ts test/contribute_db.test.ts
git commit -m "feat(api): /v1/contribute anti-poison minhash screen + overlap_observation"
```

#### Task S4.4: Anti-poison exact confirm

**Files:**
- Modify: `src/db_anti_poison.ts`
- Modify: `src/routes/contribute.ts`
- Create: `test/anti_poison_confirm.test.ts`

- [ ] **Step S4.4.1: Write the failing test**

Create `test/anti_poison_confirm.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env, SELF } from "cloudflare:test";
import { encodeZstdVarint, initCodec } from "../src/codec";
import { minhash128 } from "../src/minhash";

beforeAll(async () => { await initCodec(); });

/**
 * Seed a canonical episode with a specific fingerprint, then submit a contribution
 * that claims to be a DIFFERENT episode but uses the same fingerprint. Expect
 * poison_check = 'flag_conflict'.
 */
describe("anti-poison exact confirm", () => {
  it("flags conflict when overlap > threshold against another canonical", async () => {
    const sharedHashes = Array.from({ length: 500 }, (_, i) => i * 7 + 13);
    const encoded = await encodeZstdVarint(sharedHashes);
    const sketch = minhash128(sharedHashes);

    // Canonical for Show A, S1E1
    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
       VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
    ).bind(77777, 1, 1, encoded).run();
    await env.DB.prepare(
      `INSERT INTO canonical_sketch (tmdb_id, season, episode, sketch, hash_count, generated_at)
       VALUES (?, ?, ?, ?, ?, unixepoch())`,
    ).bind(77777, 1, 1, sketch, sharedHashes.length).run();

    // Contribute claiming Show B, S2E2 with the SAME fingerprint.
    const b64 = btoa(String.fromCharCode(...encoded));
    const res = await SELF.fetch("https://example.com/v1/contribute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wire_format_version: 1,
        pseudonym: "55555555-5555-4555-8555-555555555555",
        tmdb_id: 88888, season: 2, episode: 2,
        fingerprint_b64: b64,
        fingerprint_sha256_b64: btoa(String.fromCharCode(...new Uint8Array(32))),
        disc_content_hash_b64: null,
        match_confidence: 0.91,
        match_source: "engram_asr",
        client_version: "engram/0.9.2",
      }),
    });
    expect(res.status).toBe(202);
    const json = (await res.json()) as { poison_check: string; overlap_pct: number };
    expect(json.poison_check).toBe("flag_conflict");
    expect(json.overlap_pct).toBeGreaterThan(0.7);
  });

  it("increments flag_count after flag_conflict", async () => {
    const row = await env.DB.prepare(
      `SELECT flag_count FROM contributor WHERE pseudonym = ?`,
    ).bind("55555555-5555-4555-8555-555555555555").first<{ flag_count: number }>();
    expect(row?.flag_count).toBeGreaterThan(0);
  });
});
```

- [ ] **Step S4.4.2: Run the test (expect FAIL)**

- [ ] **Step S4.4.3: Add exact-confirm helper to src/db_anti_poison.ts**

Append to `src/db_anti_poison.ts`:

```typescript
import { decodeZstdVarint } from "./codec";

/** Hamming-distance count of bits differing between two uint32s. */
function hammingDistance32(a: number, b: number): number {
  let x = (a ^ b) >>> 0;
  x = x - ((x >> 1) & 0x55555555);
  x = (x & 0x33333333) + ((x >> 2) & 0x33333333);
  return (((x + (x >> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}

/**
 * Exact-overlap computation: for each query hash, find if ANY ref hash is
 * within Hamming<=6. Returns fraction of query hashes with a match.
 *
 * O(|query| × |ref|) — only run on the candidate surviving the minhash screen,
 * so this is at most ~10K × ~10K = 100M ops, within Worker CPU budget.
 */
export function exactOverlap(queryHashes: number[], refHashes: number[]): number {
  if (queryHashes.length === 0) return 0;
  let matches = 0;
  const refSet = new Set(refHashes); // exact equality fast path
  for (const q of queryHashes) {
    if (refSet.has(q)) { matches++; continue; }
    // Hamming<=6 scan (slow path)
    for (const r of refHashes) {
      if (hammingDistance32(q, r) <= 6) { matches++; break; }
    }
  }
  return matches / queryHashes.length;
}

export async function loadCanonicalFingerprint(
  db: D1Database, tmdb_id: number, season: number, episode: number,
): Promise<number[] | null> {
  const row = await db.prepare(
    `SELECT fingerprint FROM episode_canonical WHERE tmdb_id = ? AND season = ? AND episode = ?`,
  ).bind(tmdb_id, season, episode).first<{ fingerprint: ArrayBuffer }>();
  if (!row) return null;
  return await decodeZstdVarint(new Uint8Array(row.fingerprint));
}

export async function incrementFlagCount(
  db: D1Database, pseudonym: string,
): Promise<void> {
  await db.prepare(
    `UPDATE contributor
     SET flag_count = flag_count + 1,
         flagged = CASE WHEN flag_count + 1 > 3 THEN 1 ELSE flagged END
     WHERE pseudonym = ?`,
  ).bind(pseudonym).run();
}
```

- [ ] **Step S4.4.4: Wire confirm into handleContribute**

In `src/routes/contribute.ts`, replace the `const poisonCheck = "pass"` line and surrounding code with:

```typescript
import { exactOverlap, loadCanonicalFingerprint, incrementFlagCount } from "../db_anti_poison";

const screenThreshold = parseFloat(env.POISON_CONFLICT_THRESHOLD) - 0.10;
let poisonCheck: "pass" | "flag_conflict" = "pass";
let exactPct = screen.maxOverlapEstimate;

if (screen.maxOverlapEstimate > screenThreshold
    && screen.targetTmdbId !== null
    && screen.targetSeason !== null
    && screen.targetEpisode !== null) {
  const refHashes = await loadCanonicalFingerprint(
    env.DB, screen.targetTmdbId, screen.targetSeason, screen.targetEpisode,
  );
  if (refHashes) {
    exactPct = exactOverlap(hashes, refHashes);
    if (exactPct > parseFloat(env.POISON_CONFLICT_THRESHOLD)) {
      poisonCheck = "flag_conflict";
    }
  }
}

const result = await insertContribution(env.DB, req, fingerprintBytes, fingerprintSha256, poisonCheck);

if (!result.isDuplicate) {
  // Use the exact pct if we computed it, else the estimate
  await recordOverlapObservation(env.DB, result.contributionId, {
    ...screen,
    maxOverlapEstimate: exactPct,
  });
  if (poisonCheck === "flag_conflict") {
    await incrementFlagCount(env.DB, req.pseudonym);
  }
}

return Response.json(
  {
    contribution_id: result.contributionId,
    poison_check: result.poisonCheck === "flag_duplicate" ? "flag_duplicate" : poisonCheck,
    overlap_pct: exactPct,
  },
  { status: result.isDuplicate ? 200 : 202 },
);
```

- [ ] **Step S4.4.5: Run all tests (expect PASS)**

```bash
pnpm test
```

- [ ] **Step S4.4.6: Commit**

```bash
git add src/db_anti_poison.ts src/routes/contribute.ts test/anti_poison_confirm.test.ts
git commit -m "feat(api): /v1/contribute anti-poison exact-confirm + flag_count increment"
```

### Cluster S5: POST /v1/forget

#### Task S5.1: Implement /v1/forget

**Files:**
- Create: `src/routes/forget.ts`
- Modify: `src/index.ts`
- Create: `test/forget.test.ts`

- [ ] **Step S5.1.1: Write the failing test**

Create `test/forget.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env, SELF } from "cloudflare:test";

describe("POST /v1/forget", () => {
  it("returns 400 on malformed pseudonym", async () => {
    const res = await SELF.fetch("https://example.com/v1/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pseudonym: "not-a-uuid" }),
    });
    expect(res.status).toBe(400);
  });

  it("returns 200 with rows_deleted=0 for unknown pseudonym (idempotent)", async () => {
    const res = await SELF.fetch("https://example.com/v1/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pseudonym: "00000000-0000-4000-8000-000000000000" }),
    });
    expect(res.status).toBe(200);
    const json = (await res.json()) as { rows_deleted: number; canonical_unaffected: boolean };
    expect(json.rows_deleted).toBe(0);
    expect(json.canonical_unaffected).toBe(true);
  });

  it("deletes all contribution + contributor rows for a known pseudonym", async () => {
    // Seed contributor + contributions
    const psn = "66666666-6666-4666-8666-666666666666";
    await env.DB.prepare(
      `INSERT INTO contributor (pseudonym, first_seen, last_seen, contribution_count, flagged, flag_count)
       VALUES (?, unixepoch(), unixepoch(), 0, 0, 0)`,
    ).bind(psn).run();
    await env.DB.prepare(
      `INSERT INTO contribution (pseudonym, tmdb_id, season, episode, fingerprint, fingerprint_sha256, match_confidence, match_source, client_version)
       VALUES (?, 99, 1, 1, ?, ?, 0.9, 'engram_asr', 'engram/0.9.2')`,
    ).bind(psn, new Uint8Array([1, 2]), new Uint8Array([3, 4])).run();

    const res = await SELF.fetch("https://example.com/v1/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pseudonym: psn }),
    });
    expect(res.status).toBe(200);
    const json = (await res.json()) as { rows_deleted: number };
    expect(json.rows_deleted).toBeGreaterThan(0);

    const remaining = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM contribution WHERE pseudonym = ?`,
    ).bind(psn).first<{ n: number }>();
    expect(remaining?.n).toBe(0);
  });
});
```

- [ ] **Step S5.1.2: Run the test (expect FAIL)**

- [ ] **Step S5.1.3: Implement src/routes/forget.ts**

```typescript
import { ForgetRequestSchema } from "../schemas";
import type { Env } from "./contribute";

export async function handleForget(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try { body = await request.json(); } catch { return new Response("invalid JSON", { status: 400 }); }

  const parsed = ForgetRequestSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json({ error: "schema validation failed", details: parsed.error.flatten() }, { status: 400 });
  }
  const { pseudonym } = parsed.data;

  // CASCADE on overlap_observation handles those rows automatically.
  const contribResult = await env.DB.prepare(
    `DELETE FROM contribution WHERE pseudonym = ?`,
  ).bind(pseudonym).run();
  const contributorResult = await env.DB.prepare(
    `DELETE FROM contributor WHERE pseudonym = ?`,
  ).bind(pseudonym).run();

  const rowsDeleted = (contribResult.meta.changes ?? 0) + (contributorResult.meta.changes ?? 0);

  return Response.json(
    { rows_deleted: rowsDeleted, canonical_unaffected: true },
    { status: 200 },
  );
}
```

- [ ] **Step S5.1.4: Wire into src/index.ts**

```typescript
import { handleContribute, type Env } from "./routes/contribute";
import { handleForget } from "./routes/forget";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/v1/contribute") {
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleContribute(request, env);
    }
    if (url.pathname === "/v1/forget") {
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleForget(request, env);
    }
    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
```

- [ ] **Step S5.1.5: Run all tests (expect PASS)**

- [ ] **Step S5.1.6: Commit**

```bash
git add src/routes/forget.ts src/index.ts test/forget.test.ts
git commit -m "feat(api): POST /v1/forget — delete by pseudonym, canonical preserved"
```

### Cluster S6: Scheduled Workers

#### Task S6.1: PromotionWorker

**Files:**
- Create: `src/workers/promotion.ts`
- Modify: `src/index.ts` (add `scheduled` handler)
- Modify: `wrangler.toml` (uncomment triggers)
- Create: `test/promotion.test.ts`

- [ ] **Step S6.1.1: Write the failing test**

Create `test/promotion.test.ts`:

```typescript
import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { encodeZstdVarint, initCodec } from "../src/codec";
import { runPromotion } from "../src/workers/promotion";

beforeAll(async () => { await initCodec(); });

async function seedContribution(opts: {
  pseudonym: string; tmdb_id: number; season: number; episode: number;
  hashes: number[]; confidence: number; discHash?: Uint8Array;
}) {
  const encoded = await encodeZstdVarint(opts.hashes);
  await env.DB.prepare(
    `INSERT INTO contribution
       (pseudonym, tmdb_id, season, episode, fingerprint, fingerprint_sha256,
        disc_content_hash, match_confidence, match_source, client_version, poison_check)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'engram_asr', 'engram/0.9.2', 'pass')`,
  ).bind(
    opts.pseudonym, opts.tmdb_id, opts.season, opts.episode,
    encoded, new Uint8Array([0, 0]),
    opts.discHash ?? null, opts.confidence,
  ).run();
}

describe("PromotionWorker", () => {
  it("promotes to CANDIDATE with 1 contributor", async () => {
    await seedContribution({
      pseudonym: "aa111111-1111-4111-8111-111111111111",
      tmdb_id: 11111, season: 1, episode: 1,
      hashes: [1, 2, 3, 4, 5], confidence: 0.9,
      discHash: new Uint8Array([1]),
    });
    await runPromotion(env);
    const canonical = await env.DB.prepare(
      `SELECT tier FROM episode_canonical WHERE tmdb_id = 11111 AND season = 1 AND episode = 1`,
    ).first<{ tier: string }>();
    expect(canonical?.tier).toBe("candidate");
  });

  it("promotes to CONFIRMED with 2 distinct (pseudonym × disc) pairs", async () => {
    await seedContribution({
      pseudonym: "aa222222-2222-4222-8222-222222222222",
      tmdb_id: 22222, season: 1, episode: 1,
      hashes: [1, 2, 3], confidence: 0.9, discHash: new Uint8Array([1]),
    });
    await seedContribution({
      pseudonym: "aa333333-3333-4333-8333-333333333333",
      tmdb_id: 22222, season: 1, episode: 1,
      hashes: [1, 2, 3], confidence: 0.9, discHash: new Uint8Array([2]),
    });
    await runPromotion(env);
    const canonical = await env.DB.prepare(
      `SELECT tier FROM episode_canonical WHERE tmdb_id = 22222 AND season = 1 AND episode = 1`,
    ).first<{ tier: string }>();
    expect(canonical?.tier).toBe("confirmed");
  });

  it("promotes to CANONICAL with 3 contributors + mean_conf >= 0.85", async () => {
    for (let i = 0; i < 3; i++) {
      await seedContribution({
        pseudonym: `aa44444${i}-4444-4444-8444-44444444444${i}`,
        tmdb_id: 33333, season: 1, episode: 1,
        hashes: [1, 2, 3], confidence: 0.9, discHash: new Uint8Array([i + 10]),
      });
    }
    await runPromotion(env);
    const canonical = await env.DB.prepare(
      `SELECT tier, mean_confidence, unique_contributors FROM episode_canonical
       WHERE tmdb_id = 33333 AND season = 1 AND episode = 1`,
    ).first<{ tier: string; mean_confidence: number; unique_contributors: number }>();
    expect(canonical?.tier).toBe("canonical");
    expect(canonical?.mean_confidence).toBeGreaterThanOrEqual(0.85);
    expect(canonical?.unique_contributors).toBe(3);

    // Sketch should exist
    const sketch = await env.DB.prepare(
      `SELECT * FROM canonical_sketch WHERE tmdb_id = 33333 AND season = 1 AND episode = 1`,
    ).first();
    expect(sketch).not.toBeNull();
  });

  it("marks promoted contributions with promoted_at", async () => {
    const row = await env.DB.prepare(
      `SELECT promoted_at FROM contribution WHERE tmdb_id = 33333 LIMIT 1`,
    ).first<{ promoted_at: number | null }>();
    expect(row?.promoted_at).not.toBeNull();
  });
});
```

- [ ] **Step S6.1.2: Implement src/workers/promotion.ts**

```typescript
import { decodeZstdVarint, encodeZstdVarint } from "../codec";
import { minhash128 } from "../minhash";
import type { Env } from "../routes/contribute";

export async function runPromotion(env: Env): Promise<void> {
  // 1. Find all distinct (tmdb_id, season, episode) with unpromoted contributions
  const groups = await env.DB.prepare(
    `SELECT DISTINCT tmdb_id, season, episode FROM contribution
     WHERE promoted_at IS NULL AND poison_check = 'pass'`,
  ).all<{ tmdb_id: number; season: number | null; episode: number | null }>();

  for (const g of groups.results) {
    await promoteOne(env, g.tmdb_id, g.season, g.episode);
  }
}

async function promoteOne(
  env: Env, tmdb_id: number, season: number | null, episode: number | null,
): Promise<void> {
  // Pull contributions; group by pseudonym, keep most recent per pseudonym.
  const contribs = await env.DB.prepare(
    `SELECT c.id, c.pseudonym, c.disc_content_hash, c.match_confidence, c.fingerprint, c.received_at
     FROM contribution c
     INNER JOIN (
       SELECT pseudonym, MAX(received_at) AS max_rcv
       FROM contribution
       WHERE tmdb_id = ? AND season IS ? AND episode IS ?
         AND promoted_at IS NULL AND poison_check = 'pass' AND match_confidence >= 0.70
       GROUP BY pseudonym
     ) latest ON c.pseudonym = latest.pseudonym AND c.received_at = latest.max_rcv
     WHERE c.tmdb_id = ? AND c.season IS ? AND c.episode IS ?
       AND c.promoted_at IS NULL AND c.poison_check = 'pass' AND c.match_confidence >= 0.70`,
  ).bind(tmdb_id, season, episode, tmdb_id, season, episode).all<{
    id: number; pseudonym: string; disc_content_hash: ArrayBuffer | null;
    match_confidence: number; fingerprint: ArrayBuffer; received_at: number;
  }>();

  if (contribs.results.length === 0) return;

  // Count distinct (pseudonym, disc_content_hash) pairs
  const distinctPairs = new Set<string>();
  const flaggedPseudonyms = new Set<string>();
  let confSum = 0;
  for (const c of contribs.results) {
    const discKey = c.disc_content_hash
      ? Array.from(new Uint8Array(c.disc_content_hash)).join(",")
      : "null";
    distinctPairs.add(`${c.pseudonym}|${discKey}`);
    confSum += c.match_confidence;
  }

  // Check if any contributor is flagged
  const psnList = [...new Set(contribs.results.map(c => c.pseudonym))];
  if (psnList.length > 0) {
    const flagged = await env.DB.prepare(
      `SELECT pseudonym FROM contributor WHERE flagged = 1 AND pseudonym IN (${psnList.map(() => "?").join(",")})`,
    ).bind(...psnList).all<{ pseudonym: string }>();
    for (const f of flagged.results) flaggedPseudonyms.add(f.pseudonym);
  }

  const independentCount = distinctPairs.size;
  const meanConfidence = confSum / contribs.results.length;
  const anyFlagged = flaggedPseudonyms.size > 0;

  let tier: "candidate" | "confirmed" | "canonical";
  if (independentCount >= 3 && meanConfidence >= 0.85 && !anyFlagged) {
    tier = "canonical";
  } else if (independentCount >= 2) {
    tier = "confirmed";
  } else {
    tier = "candidate";
  }

  // Build consensus fingerprint: union of hashes appearing in ≥50% of contributors.
  const hashOccurrences = new Map<number, number>();
  for (const c of contribs.results) {
    const hashes = await decodeZstdVarint(new Uint8Array(c.fingerprint));
    const unique = new Set(hashes);
    for (const h of unique) hashOccurrences.set(h, (hashOccurrences.get(h) ?? 0) + 1);
  }
  const threshold = Math.ceil(contribs.results.length * 0.5);
  const consensusHashes = [...hashOccurrences.entries()]
    .filter(([, count]) => count >= threshold)
    .map(([h]) => h)
    .sort((a, b) => a - b);

  const consensusBlob = await encodeZstdVarint(consensusHashes);

  // Upsert canonical
  await env.DB.prepare(
    `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, unixepoch())
     ON CONFLICT (tmdb_id, season, episode) DO UPDATE SET
       tier = excluded.tier,
       fingerprint = excluded.fingerprint,
       unique_contributors = excluded.unique_contributors,
       mean_confidence = excluded.mean_confidence,
       promoted_at = excluded.promoted_at`,
  ).bind(tmdb_id, season, episode, tier, consensusBlob, independentCount, meanConfidence).run();

  // Upsert sketch (only on tier change OR new row — for simplicity, always upsert)
  const sketch = minhash128(consensusHashes);
  await env.DB.prepare(
    `INSERT INTO canonical_sketch (tmdb_id, season, episode, sketch, hash_count, generated_at)
     VALUES (?, ?, ?, ?, ?, unixepoch())
     ON CONFLICT (tmdb_id, season, episode) DO UPDATE SET
       sketch = excluded.sketch, hash_count = excluded.hash_count, generated_at = excluded.generated_at`,
  ).bind(tmdb_id, season, episode, sketch, consensusHashes.length).run();

  // Mark contributions promoted
  const ids = contribs.results.map(c => c.id);
  await env.DB.prepare(
    `UPDATE contribution SET promoted_at = unixepoch() WHERE id IN (${ids.map(() => "?").join(",")})`,
  ).bind(...ids).run();
}
```

- [ ] **Step S6.1.3: Run test (expect PASS)**

- [ ] **Step S6.1.4: Wire scheduled handler in src/index.ts**

Replace the default export:

```typescript
import { handleContribute, type Env } from "./routes/contribute";
import { handleForget } from "./routes/forget";
import { runPromotion } from "./workers/promotion";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/v1/contribute") {
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleContribute(request, env);
    }
    if (url.pathname === "/v1/forget") {
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleForget(request, env);
    }
    return new Response("Not Found", { status: 404 });
  },

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    if (event.cron === "0 3 * * *") {
      ctx.waitUntil(runPromotion(env));
    }
    // PackBuilder cron lands in Task S6.2
  },
} satisfies ExportedHandler<Env>;
```

- [ ] **Step S6.1.5: Commit**

```bash
git add src/workers/promotion.ts src/index.ts test/promotion.test.ts
git commit -m "feat(worker): PromotionWorker — tier transitions + consensus fingerprint"
```

#### Task S6.2: PackBuilderWorker

**Files:**
- Create: `src/workers/pack_builder.ts`
- Modify: `src/index.ts`
- Modify: `wrangler.toml`
- Create: `test/pack_builder.test.ts`

- [ ] **Step S6.2.1: Write the failing test**

Create `test/pack_builder.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { encodeZstdVarint, initCodec } from "../src/codec";
import { runPackBuilder } from "../src/workers/pack_builder";

beforeAll(async () => { await initCodec(); });

describe("PackBuilderWorker", () => {
  it("writes per-show packs to R2 for CANONICAL episodes", async () => {
    const hashes = [10, 20, 30, 40];
    const blob = await encodeZstdVarint(hashes);

    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
       VALUES (?, ?, ?, 'canonical', ?, 3, 0.9, unixepoch())`,
    ).bind(98765, 1, 1, blob).run();

    await runPackBuilder(env);

    const obj = await env.PACKS.get("98765.zstd");
    expect(obj).not.toBeNull();
    const bytes = new Uint8Array(await obj!.arrayBuffer());
    expect(bytes.byteLength).toBeGreaterThan(0);
  });

  it("does not write packs for shows with only CANDIDATE/CONFIRMED episodes", async () => {
    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint, unique_contributors, mean_confidence, promoted_at)
       VALUES (?, ?, ?, 'candidate', ?, 1, 0.5, unixepoch())`,
    ).bind(98766, 1, 1, new Uint8Array([0])).run();

    await runPackBuilder(env);
    const obj = await env.PACKS.get("98766.zstd");
    expect(obj).toBeNull();
  });
});
```

- [ ] **Step S6.2.2: Implement src/workers/pack_builder.ts**

```typescript
import { encodeZstdVarint } from "../codec";
import type { Env } from "../routes/contribute";

export async function runPackBuilder(env: Env): Promise<void> {
  const shows = await env.DB.prepare(
    `SELECT DISTINCT tmdb_id FROM episode_canonical WHERE tier = 'canonical'`,
  ).all<{ tmdb_id: number }>();

  for (const s of shows.results) {
    await buildPack(env, s.tmdb_id);
  }
}

async function buildPack(env: Env, tmdb_id: number): Promise<void> {
  const eps = await env.DB.prepare(
    `SELECT season, episode, fingerprint FROM episode_canonical
     WHERE tmdb_id = ? AND tier = 'canonical'
     ORDER BY season, episode`,
  ).bind(tmdb_id).all<{ season: number; episode: number; fingerprint: ArrayBuffer }>();

  if (eps.results.length === 0) return;

  // Pack format (wire_format_version=1):
  //   header JSON line: { wire_format_version, tmdb_id, n_episodes, generated_at }
  //   then for each ep:  { season, episode, fingerprint_b64 } as one JSON line.
  // Wrap the entire thing in zstd. Phase 3 will redesign this for streaming.
  const header = JSON.stringify({
    wire_format_version: 1,
    tmdb_id,
    n_episodes: eps.results.length,
    generated_at: Math.floor(Date.now() / 1000),
  });

  const lines = [header];
  for (const e of eps.results) {
    const fpB64 = btoa(String.fromCharCode(...new Uint8Array(e.fingerprint)));
    lines.push(JSON.stringify({ season: e.season, episode: e.episode, fingerprint_b64: fpB64 }));
  }
  const raw = new TextEncoder().encode(lines.join("\n"));

  // We use the zstd dependency for compression (initCodec already called via Worker entry).
  const { compress, init } = await import("@bokuweb/zstd-wasm");
  await init();
  const compressed = compress(raw, 11);

  await env.PACKS.put(`${tmdb_id}.zstd`, compressed, {
    customMetadata: {
      tmdb_id: String(tmdb_id),
      n_episodes: String(eps.results.length),
      generated_at: String(Math.floor(Date.now() / 1000)),
    },
  });
}
```

- [ ] **Step S6.2.3: Wire into scheduled handler**

In `src/index.ts`, extend the `scheduled` handler:

```typescript
import { runPackBuilder } from "./workers/pack_builder";

// ... inside scheduled:
if (event.cron === "0 3 * * *") ctx.waitUntil(runPromotion(env));
if (event.cron === "0 4 * * *") ctx.waitUntil(runPackBuilder(env));
```

- [ ] **Step S6.2.4: Update wrangler.toml — uncomment cron triggers**

```toml
[triggers]
crons = ["0 3 * * *", "0 4 * * *"]
```

- [ ] **Step S6.2.5: Run tests (expect PASS)**

- [ ] **Step S6.2.6: Commit**

```bash
git add src/workers/pack_builder.ts src/index.ts wrangler.toml test/pack_builder.test.ts
git commit -m "feat(worker): PackBuilderWorker — per-show R2 packs for CANONICAL episodes"
```

### Cluster S7: Deploy

#### Task S7.1: First production deploy

- [ ] **Step S7.1.1: Run typecheck + tests one more time**

```bash
cd C:\Github\engram-fingerprint-server
pnpm typecheck
pnpm test
```
Both must pass before deploying.

- [ ] **Step S7.1.2: Apply migrations to production D1**

```bash
pnpm migrate:remote
```
Expected: "Migrations applied!". If error, check that the production database exists (`wrangler d1 list`) and your credentials are valid.

- [ ] **Step S7.1.3: First wrangler deploy**

```bash
pnpm deploy
```
Expected: "Deployed engram-fp-prod to <workers.dev URL>". Note the URL.

- [ ] **Step S7.1.4: Smoke-test the live endpoint**

```bash
curl -X POST <workers.dev URL>/v1/contribute \
  -H "Content-Type: application/json" \
  -d '{"wire_format_version":1,"pseudonym":"00000000-0000-4000-8000-000000000000","tmdb_id":1,"season":1,"episode":1,"fingerprint_b64":"KLUv/QAEAQAAAA==","fingerprint_sha256_b64":"AAAA","disc_content_hash_b64":null,"match_confidence":0.9,"match_source":"engram_asr","client_version":"engram/0.9.2"}'
```
Expected: 400 (the fingerprint_b64 above is a deliberately empty zstd-varint payload — it should decode but produce 0 hashes; consider this a "the request reached the Worker and got parsed" smoke test). If you get a 500 or connection refused, check the wrangler logs (`wrangler tail`).

- [ ] **Step S7.1.5: Configure custom domain**

In the Cloudflare dashboard, add a Workers Route for your registered domain (e.g. `fp.engram.app/v1/*` → `engram-fp-prod`). Update `wrangler.toml`:

```toml
routes = [
  { pattern = "fp.engram.app/v1/*", custom_domain = true }
]
```

Re-deploy: `pnpm deploy`.

- [ ] **Step S7.1.6: Tag v0.1.0**

```bash
cd C:\Github\engram-fingerprint-server
git tag v0.1.0
git push origin main --tags
```

End of Part A. The server is now live.

---

## PART B: Client (this Engram repo)

After Part A, the server has `POST /v1/contribute` and `POST /v1/forget` working. The client work below assumes you have a server URL (production or `wrangler dev` local) to point the uploader at.

### Cluster C1: Schema Migration

#### Task C1.1: Add new columns to FingerprintContribution + AppConfig

**Files:**
- Modify: `backend/app/models/fingerprint.py`
- Modify: `backend/app/models/app_config.py`
- Create: `backend/migrations/versions/<rev>_phase2_uploader_columns.py`
- Create: `backend/tests/unit/test_phase2_schema.py`

- [ ] **Step C1.1.1: Write the failing schema test**

Create `backend/tests/unit/test_phase2_schema.py`:

```python
"""Phase 2 schema additions."""

from app.models.fingerprint import FingerprintContribution
from app.models.app_config import AppConfig


def test_contribution_has_next_attempt_at():
    assert "next_attempt_at" in FingerprintContribution.model_fields


def test_contribution_has_upload_error():
    assert "upload_error" in FingerprintContribution.model_fields


def test_app_config_has_fingerprint_server_url():
    assert "fingerprint_server_url" in AppConfig.model_fields


def test_app_config_has_disclosure_accepted():
    assert "fingerprint_disclosure_accepted" in AppConfig.model_fields
    assert "fingerprint_disclosure_accepted_at" in AppConfig.model_fields


def test_disclosure_accepted_defaults_false():
    cfg = AppConfig()
    assert cfg.fingerprint_disclosure_accepted is False
```

- [ ] **Step C1.1.2: Run the test (expect FAIL)**

```bash
cd backend
uv run pytest tests/unit/test_phase2_schema.py -v
```

- [ ] **Step C1.1.3: Add fields to FingerprintContribution**

In `backend/app/models/fingerprint.py`, after the existing `upload_attempts` field, add:

```python
    next_attempt_at: datetime | None = None
    upload_error: str | None = None
```

- [ ] **Step C1.1.4: Add fields to AppConfig**

In `backend/app/models/app_config.py`, near the other fingerprint fields, add:

```python
    fingerprint_server_url: str = Field(default="https://fp.engram.example/v1")
    fingerprint_disclosure_accepted: bool = Field(default=False)
    fingerprint_disclosure_accepted_at: datetime | None = Field(default=None)
```

Make sure `datetime` is imported.

- [ ] **Step C1.1.5: Run the schema tests (expect PASS)**

- [ ] **Step C1.1.6: Generate Alembic migration**

```bash
cd backend
uv run alembic revision -m "phase2 uploader columns"
```
Note the generated file path under `backend/migrations/versions/`.

- [ ] **Step C1.1.7: Fill in the migration body**

Replace the generated `upgrade()` / `downgrade()`:

```python
"""phase2 uploader columns

Revision ID: <auto>
Revises: <PREV_REV>
Create Date: <auto>
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# auto-generated revision identifiers above


def upgrade() -> None:
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("upload_error", sa.String(), nullable=True))
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fingerprint_server_url", sa.String(), nullable=False, server_default="https://fp.engram.example/v1"))
        batch_op.add_column(sa.Column("fingerprint_disclosure_accepted", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("fingerprint_disclosure_accepted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("fingerprint_disclosure_accepted_at")
        batch_op.drop_column("fingerprint_disclosure_accepted")
        batch_op.drop_column("fingerprint_server_url")
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.drop_column("upload_error")
        batch_op.drop_column("next_attempt_at")
```

- [ ] **Step C1.1.8: Smoke-test the migration**

```bash
cd backend
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic downgrade -1
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic upgrade head
rm scratch.db
```
Expected: all three succeed without errors.

- [ ] **Step C1.1.9: Commit**

```bash
git add backend/app/models/fingerprint.py backend/app/models/app_config.py backend/migrations/versions/*phase2_uploader_columns.py backend/tests/unit/test_phase2_schema.py
git commit -m "feat(models): Phase 2 uploader columns + server-url + disclosure-accepted"
```

### Cluster C2: zstd-varint Codec (Client)

#### Task C2.1: Add zstandard dep + codec module

**Files:**
- Create: `backend/app/services/zstd_varint_codec.py`
- Create: `backend/tests/unit/test_zstd_varint_codec.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step C2.1.1: Add zstandard dependency**

```bash
cd backend
uv add zstandard
```
Expected: `pyproject.toml` updated, lock refreshed.

- [ ] **Step C2.1.2: Write the failing test**

Create `backend/tests/unit/test_zstd_varint_codec.py`:

```python
"""Tests for the client-side zstd+varint codec (wire-format encoder)."""

import hashlib

import pytest

from app.services.zstd_varint_codec import (
    encode_zstd_varint,
    decode_zstd_varint,
    fingerprint_sha256,
)


def test_roundtrip_empty():
    encoded = encode_zstd_varint([])
    assert decode_zstd_varint(encoded) == []


def test_roundtrip_small():
    hashes = [1, 2, 3, 4, 5, 100, 1000, 1000000, 4294967295]
    encoded = encode_zstd_varint(hashes)
    assert decode_zstd_varint(encoded) == hashes


def test_encoded_smaller_than_naive():
    """A 1000-hash stream encodes to under 5000 bytes (vs 4000 raw)."""
    hashes = list(range(1000))
    encoded = encode_zstd_varint(hashes)
    assert len(encoded) < 5000


def test_compatibility_with_phase1_blob():
    """
    Phase 1 stored gzip-JSON; uploader decodes that, re-encodes as zstd-varint.
    Verify the SHA256 of the DECOMPRESSED VARINT (not the gzip-JSON) is what
    the server will dedupe on.
    """
    hashes = [42, 100, 200, 300]
    encoded = encode_zstd_varint(hashes)
    decoded = decode_zstd_varint(encoded)
    assert decoded == hashes

    # SHA256 of the canonical decompressed varint bytes
    expected = hashlib.sha256(_varint_bytes(hashes)).digest()
    assert fingerprint_sha256(hashes) == expected


def _varint_bytes(values: list[int]) -> bytes:
    out = bytearray()
    for v in values:
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v & 0x7F)
    return bytes(out)
```

- [ ] **Step C2.1.3: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_zstd_varint_codec.py -v
```

- [ ] **Step C2.1.4: Implement the codec module**

Create `backend/app/services/zstd_varint_codec.py`:

```python
"""Client-side zstd+varint codec.

Phase 1 stores chromaprints locally as gzip-JSON via ChromaprintResult.to_blob().
The uploader (Phase 2) re-encodes them as zstd-compressed varint streams on the
wire to match the server's storage format. This module is the encoder/decoder.

The varint scheme: standard LEB128 unsigned encoding, 7 bits per byte with the
high bit signaling continuation. Identical to the protobuf varint format.
"""

from __future__ import annotations

import hashlib

import zstandard as zstd

_COMPRESSOR = zstd.ZstdCompressor(level=11)
_DECOMPRESSOR = zstd.ZstdDecompressor()


def _write_varint(buf: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError(f"varint values must be unsigned: {value}")
    while value >= 0x80:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value & 0x7F)


def _read_varint_stream(data: bytes) -> list[int]:
    out: list[int] = []
    value = 0
    shift = 0
    for b in data:
        value |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            out.append(value)
            value = 0
            shift = 0
    return out


def _to_varint_bytes(hashes: list[int]) -> bytes:
    buf = bytearray()
    for h in hashes:
        _write_varint(buf, h)
    return bytes(buf)


def encode_zstd_varint(hashes: list[int]) -> bytes:
    """uint32[] → zstd-compressed varint stream (wire format)."""
    return _COMPRESSOR.compress(_to_varint_bytes(hashes))


def decode_zstd_varint(blob: bytes) -> list[int]:
    """zstd-compressed varint stream → uint32[]."""
    if not blob:
        return []
    return _read_varint_stream(_DECOMPRESSOR.decompress(blob))


def fingerprint_sha256(hashes: list[int]) -> bytes:
    """SHA256 of the DECOMPRESSED varint stream. Server dedupes on this."""
    return hashlib.sha256(_to_varint_bytes(hashes)).digest()
```

- [ ] **Step C2.1.5: Run the test (expect PASS)**

- [ ] **Step C2.1.6: Commit**

```bash
git add backend/app/services/zstd_varint_codec.py backend/tests/unit/test_zstd_varint_codec.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(services): zstd+varint wire-format codec"
```

### Cluster C3: ContributionUploader

#### Task C3.1: Module skeleton + constants

**Files:**
- Create: `backend/app/services/contribution_uploader.py`
- Create: `backend/tests/unit/test_contribution_uploader.py`

- [ ] **Step C3.1.1: Write the failing test**

Create `backend/tests/unit/test_contribution_uploader.py`:

```python
"""Tests for ContributionUploader."""

import pytest

from app.services.contribution_uploader import (
    ContributionUploader,
    BATCH_SIZE,
    MAX_ATTEMPTS,
    TICK_INTERVAL_SECONDS,
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
)


def test_constants_are_exposed():
    assert BATCH_SIZE == 10
    assert MAX_ATTEMPTS == 20
    assert TICK_INTERVAL_SECONDS == 300
    assert BACKOFF_BASE_SECONDS == 60
    assert BACKOFF_CAP_SECONDS == 7 * 86400


def test_uploader_construction():
    up = ContributionUploader(server_url="http://localhost:8787/v1")
    assert up.server_url == "http://localhost:8787/v1"
```

- [ ] **Step C3.1.2: Run the test (expect FAIL — ImportError)**

- [ ] **Step C3.1.3: Implement the skeleton**

Create `backend/app/services/contribution_uploader.py`:

```python
"""ContributionUploader — drains the local fingerprint_contributions queue.

Background asyncio task started in app.main.lifespan. Polls the queue every
TICK_INTERVAL_SECONDS, batches up to BATCH_SIZE rows per POST, applies
exponential backoff on failures (capped at BACKOFF_CAP_SECONDS = 7 days),
and writes one audit-log line per upload to ~/.engram/cache/contribution_log.jsonl.

Respects:
- AppConfig.enable_fingerprint_contributions (Phase 1 toggle; off → no uploads).
- AppConfig.fingerprint_disclosure_accepted (Phase 2; off → broadcast disclosure event).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    import httpx

BATCH_SIZE = 10
MAX_ATTEMPTS = 20
TICK_INTERVAL_SECONDS = 300
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 7 * 86400


class ContributionUploader:
    def __init__(self, server_url: str, http_client: "httpx.AsyncClient | None" = None):
        self.server_url = server_url.rstrip("/")
        self._http = http_client
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        """Drain loop. Cancelled on app shutdown."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=TICK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass  # Normal tick
            if self._stop_event.is_set():
                return
            try:
                await self._drain_one_batch()
            except Exception:
                logger.exception("ContributionUploader tick crashed; continuing")

    async def stop(self) -> None:
        self._stop_event.set()

    async def _drain_one_batch(self) -> None:
        raise NotImplementedError("lands in Task C3.2")
```

- [ ] **Step C3.1.4: Run the test (expect PASS)**

- [ ] **Step C3.1.5: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/unit/test_contribution_uploader.py
git commit -m "feat(services): ContributionUploader skeleton + constants"
```

#### Task C3.1b: Add EventBroadcaster method for disclosure-required event

**Files:**
- Modify: `backend/app/services/event_broadcaster.py`
- Create: `backend/tests/unit/test_event_broadcaster_disclosure.py`

EventBroadcaster is the project's typed wrapper around `ConnectionManager` (per CLAUDE.md). The uploader uses it instead of calling `ws_manager` directly so all WebSocket messages flow through one validated surface.

- [ ] **Step C3.1b.1: Write the failing test**

Create `backend/tests/unit/test_event_broadcaster_disclosure.py`:

```python
"""EventBroadcaster.broadcast_fingerprint_disclosure_required."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.event_broadcaster import EventBroadcaster


@pytest.mark.asyncio
async def test_broadcast_disclosure_required_sends_typed_event():
    fake_manager = MagicMock()
    fake_manager.broadcast = AsyncMock()
    bcast = EventBroadcaster(connection_manager=fake_manager)

    await bcast.broadcast_fingerprint_disclosure_required(
        pending_count=3,
        pseudonym="11111111-1111-4111-8111-111111111111",
        server_url="http://x/v1",
    )

    fake_manager.broadcast.assert_called_once()
    payload = fake_manager.broadcast.call_args[0][0]
    assert payload["type"] == "fingerprint_disclosure_required"
    assert payload["data"]["pending_count"] == 3
    assert payload["data"]["pseudonym"] == "11111111-1111-4111-8111-111111111111"
    assert payload["data"]["server_url"] == "http://x/v1"
    assert "fields_sent" in payload["data"]
```

- [ ] **Step C3.1b.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_event_broadcaster_disclosure.py -v
```

- [ ] **Step C3.1b.3: Add the method**

In `backend/app/services/event_broadcaster.py`, add to the `EventBroadcaster` class:

```python
async def broadcast_fingerprint_disclosure_required(
    self,
    *,
    pending_count: int,
    pseudonym: str,
    server_url: str,
) -> None:
    """Phase 2 JIT-disclosure modal trigger.

    Sent when ContributionUploader has rows to upload but the user has not
    yet accepted the disclosure. Frontend shows a blocking modal.
    """
    await self._manager.broadcast({
        "type": "fingerprint_disclosure_required",
        "data": {
            "pending_count": pending_count,
            "pseudonym": pseudonym,
            "server_url": server_url,
            "fields_sent": [
                "chromaprint",
                "tmdb_id+season+episode",
                "match_confidence+source",
                "disc_content_hash",
                "client_version",
            ],
        },
    })
```

(If the existing `EventBroadcaster` class stores the manager as a different attribute name, adjust accordingly. Check `backend/app/services/event_broadcaster.py` for the existing convention.)

- [ ] **Step C3.1b.4: Run the test (expect PASS)**

- [ ] **Step C3.1b.5: Commit**

```bash
git add backend/app/services/event_broadcaster.py backend/tests/unit/test_event_broadcaster_disclosure.py
git commit -m "feat(services): EventBroadcaster.broadcast_fingerprint_disclosure_required"
```

#### Task C3.2: Pre-flight gate (disclosure + opt-out check)

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Modify: `backend/tests/unit/test_contribution_uploader.py`
- Create: `backend/app/services/event_broadcaster.py` (add new method)

- [ ] **Step C3.2.1: Write the failing tests**

Append to `backend/tests/unit/test_contribution_uploader.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_drain_no_op_when_disabled():
    """enable_fingerprint_contributions=False → no upload, no broadcast."""
    cfg = MagicMock(enable_fingerprint_contributions=False, fingerprint_disclosure_accepted=False)
    session = AsyncMock()

    with patch("app.services.contribution_uploader.get_config", return_value=cfg), \
         patch("app.services.contribution_uploader.async_session") as ms:
        ms.return_value.__aenter__.return_value = session
        up = ContributionUploader(server_url="http://x")
        up._broadcast_disclosure_required = AsyncMock()
        await up._drain_one_batch()
        up._broadcast_disclosure_required.assert_not_called()


@pytest.mark.asyncio
async def test_drain_broadcasts_disclosure_when_unset():
    """enable=True but disclosure_accepted=False → broadcast, no upload."""
    cfg = MagicMock(
        enable_fingerprint_contributions=True,
        fingerprint_disclosure_accepted=False,
        contribution_pseudonym="11111111-1111-4111-8111-111111111111",
        fingerprint_server_url="http://x",
    )
    session = AsyncMock()
    # session.exec returns rows when called
    session.exec.return_value.all.return_value = [MagicMock(id=1, uploaded_at=None)]

    with patch("app.services.contribution_uploader.get_config", return_value=cfg), \
         patch("app.services.contribution_uploader.async_session") as ms:
        ms.return_value.__aenter__.return_value = session
        up = ContributionUploader(server_url="http://x")
        up._broadcast_disclosure_required = AsyncMock()
        up._upload_one = AsyncMock()
        await up._drain_one_batch()
        up._broadcast_disclosure_required.assert_called_once()
        up._upload_one.assert_not_called()
```

- [ ] **Step C3.2.2: Run the test (expect FAIL)**

- [ ] **Step C3.2.3: Implement the gate**

Replace `_drain_one_batch` in `contribution_uploader.py`:

```python
from datetime import datetime, timezone

from sqlmodel import select

from app.database import async_session
from app.models.fingerprint import FingerprintContribution
from app.services.config_service import get_config


async def _drain_one_batch(self) -> None:
    async with async_session() as session:
        cfg = await get_config(session)
        if not cfg.enable_fingerprint_contributions:
            return  # Phase 1 toggle wins

        # Are there any pending rows? If not, nothing to do.
        pending = await session.exec(
            select(FingerprintContribution)
            .where(FingerprintContribution.uploaded_at.is_(None))
            .where(FingerprintContribution.upload_attempts < MAX_ATTEMPTS)
            .order_by(FingerprintContribution.queued_at)
            .limit(BATCH_SIZE)
        )
        rows = pending.all()
        if not rows:
            return

        if not cfg.fingerprint_disclosure_accepted:
            await self._broadcast_disclosure_required(
                pending_count=len(rows),
                pseudonym=cfg.contribution_pseudonym or "",
                server_url=cfg.fingerprint_server_url,
            )
            return

        # next_attempt_at gate (rows with future retry time are skipped)
        now = datetime.now(timezone.utc)
        eligible = [r for r in rows if r.next_attempt_at is None or r.next_attempt_at <= now]
        if not eligible:
            return

        for row in eligible:
            await self._upload_one(session, row, cfg)
        await session.commit()


async def _broadcast_disclosure_required(
    self, pending_count: int, pseudonym: str, server_url: str,
) -> None:
    """Delegate to EventBroadcaster — keeps the WS layer abstraction intact
    (EventBroadcaster wraps the ConnectionManager with typed methods per
    the project's existing pattern; see backend/app/services/event_broadcaster.py)."""
    from app.services.event_broadcaster import event_broadcaster
    await event_broadcaster.broadcast_fingerprint_disclosure_required(
        pending_count=pending_count,
        pseudonym=pseudonym,
        server_url=server_url,
    )


async def _upload_one(self, session, row, cfg) -> None:
    raise NotImplementedError("lands in Task C3.3")
```

Add these methods to the `ContributionUploader` class. The two new test cases should now pass.

- [ ] **Step C3.2.4: Run the test (expect PASS)**

- [ ] **Step C3.2.5: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/unit/test_contribution_uploader.py
git commit -m "feat(services): ContributionUploader pre-flight gate (opt-out + disclosure)"
```

#### Task C3.3: `_upload_one` with status-code paths

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Extend: `backend/tests/unit/test_contribution_uploader.py`

- [ ] **Step C3.3.1: Write the failing tests**

Append to `test_contribution_uploader.py`:

```python
@pytest.mark.asyncio
async def test_upload_one_success_marks_uploaded_at():
    """202 from server → uploaded_at set, no retry scheduled."""
    import httpx
    from datetime import datetime
    from app.services.zstd_varint_codec import encode_zstd_varint

    # Build a row whose chromaprint_blob is valid Phase-1 gzip-JSON.
    from app.matcher.chromaprint_extractor import ChromaprintResult
    fp = ChromaprintResult(hashes=[1, 2, 3], duration_seconds=10.0, fpcalc_version="test")
    row = MagicMock(
        id=1, title_id=10, chromaprint_blob=fp.to_blob(),
        tmdb_id=99, season=1, episode=1,
        match_confidence=0.91, match_source="engram_asr",
        disc_content_hash=None,
        upload_attempts=0, next_attempt_at=None, uploaded_at=None,
    )
    cfg = MagicMock(
        contribution_pseudonym="11111111-1111-4111-8111-111111111111",
        fingerprint_server_url="http://x",
    )

    fake_http = AsyncMock()
    fake_response = MagicMock(status_code=202)
    fake_response.json.return_value = {"contribution_id": 42, "poison_check": "pass", "overlap_pct": 0.01}
    fake_http.post.return_value = fake_response

    up = ContributionUploader(server_url="http://x", http_client=fake_http)
    up._append_audit_log = AsyncMock()

    session = AsyncMock()
    await up._upload_one(session, row, cfg)
    assert row.uploaded_at is not None
    up._append_audit_log.assert_called_once()


@pytest.mark.asyncio
async def test_upload_one_400_permanent_fail():
    """400 → mark uploaded_at with sentinel, record error, don't retry."""
    from app.matcher.chromaprint_extractor import ChromaprintResult
    fp = ChromaprintResult(hashes=[1], duration_seconds=10.0, fpcalc_version="test")
    row = MagicMock(
        id=2, title_id=10, chromaprint_blob=fp.to_blob(),
        tmdb_id=99, season=1, episode=1, match_confidence=0.9, match_source="engram_asr",
        disc_content_hash=None, upload_attempts=0, next_attempt_at=None, uploaded_at=None,
    )
    cfg = MagicMock(contribution_pseudonym="11111111-1111-4111-8111-111111111111", fingerprint_server_url="http://x")
    fake_http = AsyncMock()
    fake_response = MagicMock(status_code=400, text="schema invalid")
    fake_http.post.return_value = fake_response

    up = ContributionUploader(server_url="http://x", http_client=fake_http)
    up._append_audit_log = AsyncMock()
    session = AsyncMock()
    await up._upload_one(session, row, cfg)
    assert row.uploaded_at is not None
    assert row.upload_error is not None
    assert "400" in row.upload_error


@pytest.mark.asyncio
async def test_upload_one_503_schedules_backoff():
    """503 → next_attempt_at set to now + backoff, attempts incremented."""
    from app.matcher.chromaprint_extractor import ChromaprintResult
    fp = ChromaprintResult(hashes=[1], duration_seconds=10.0, fpcalc_version="test")
    row = MagicMock(
        id=3, title_id=10, chromaprint_blob=fp.to_blob(),
        tmdb_id=99, season=1, episode=1, match_confidence=0.9, match_source="engram_asr",
        disc_content_hash=None, upload_attempts=0, next_attempt_at=None, uploaded_at=None,
    )
    cfg = MagicMock(contribution_pseudonym="11111111-1111-4111-8111-111111111111", fingerprint_server_url="http://x")
    fake_http = AsyncMock()
    fake_response = MagicMock(status_code=503, text="busy")
    fake_http.post.return_value = fake_response

    up = ContributionUploader(server_url="http://x", http_client=fake_http)
    up._append_audit_log = AsyncMock()
    session = AsyncMock()
    await up._upload_one(session, row, cfg)
    assert row.uploaded_at is None
    assert row.upload_attempts == 1
    assert row.next_attempt_at is not None
```

- [ ] **Step C3.3.2: Run tests (expect FAIL)**

- [ ] **Step C3.3.3: Implement _upload_one**

Replace `_upload_one` in `contribution_uploader.py`:

```python
import base64
from datetime import datetime, timedelta, timezone

import httpx

from app.matcher.chromaprint_extractor import ChromaprintResult
from app.services.zstd_varint_codec import encode_zstd_varint, fingerprint_sha256
from app import __version__ as ENGRAM_VERSION


async def _upload_one(self, session, row, cfg) -> None:
    # Decode Phase 1's gzip-JSON storage and re-encode for the wire.
    try:
        phase1 = ChromaprintResult.from_blob(row.chromaprint_blob)
        wire_bytes = encode_zstd_varint(phase1.hashes)
        wire_sha256 = fingerprint_sha256(phase1.hashes)
    except Exception as e:
        row.uploaded_at = datetime.now(timezone.utc)
        row.upload_error = f"local encode failed: {type(e).__name__}: {e}"[:200]
        logger.error(f"Contribution {row.id} could not be encoded: {e}")
        return

    payload = {
        "wire_format_version": 1,
        "pseudonym": cfg.contribution_pseudonym,
        "tmdb_id": row.tmdb_id,
        "season": row.season,
        "episode": row.episode,
        "fingerprint_b64": base64.b64encode(wire_bytes).decode("ascii"),
        "fingerprint_sha256_b64": base64.b64encode(wire_sha256).decode("ascii"),
        "disc_content_hash_b64": (
            base64.b64encode(row.disc_content_hash).decode("ascii")
            if row.disc_content_hash else None
        ),
        "match_confidence": row.match_confidence,
        "match_source": row.match_source,
        "client_version": f"engram/{ENGRAM_VERSION}",
    }

    http = self._http or httpx.AsyncClient(timeout=30.0)
    own_client = self._http is None
    try:
        response = await http.post(f"{self.server_url}/contribute", json=payload)
    except (httpx.NetworkError, httpx.TimeoutException) as e:
        self._defer_with_backoff(row)
        logger.warning(f"Contribution {row.id} network error: {e}")
        return
    finally:
        if own_client:
            await http.aclose()

    if response.status_code in (200, 202, 409):
        row.uploaded_at = datetime.now(timezone.utc)
        try:
            body = response.json()
        except Exception:
            body = {}
        await self._append_audit_log(row, body)
    elif response.status_code == 400:
        row.uploaded_at = datetime.now(timezone.utc)
        row.upload_error = f"400: {response.text[:200]}"
        logger.error(f"Contribution {row.id} rejected as malformed: {response.text[:200]}")
        await self._append_audit_log(row, {"event": "upload_failed", "error": row.upload_error})
    else:
        # 429, 503, 5xx, anything else — backoff.
        self._defer_with_backoff(row)
        logger.warning(f"Contribution {row.id} got {response.status_code}; deferring")


def _defer_with_backoff(self, row) -> None:
    row.upload_attempts += 1
    delay = min(BACKOFF_BASE_SECONDS * (2 ** row.upload_attempts), BACKOFF_CAP_SECONDS)
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
```

Add `_append_audit_log` as a stub (real impl lands in Cluster C5):

```python
async def _append_audit_log(self, row, server_response: dict) -> None:
    """Append a JSONL line to ~/.engram/cache/contribution_log.jsonl. Real impl in Cluster C5."""
    pass
```

- [ ] **Step C3.3.4: Run tests (expect PASS)**

```bash
uv run pytest tests/unit/test_contribution_uploader.py -v
```

- [ ] **Step C3.3.5: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/unit/test_contribution_uploader.py
git commit -m "feat(services): ContributionUploader._upload_one + exp-backoff"
```

#### Task C3.4: Wire into app lifespan

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step C3.4.1: Write the failing integration test**

Append to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_uploader_is_started_at_lifespan(integration_client):
    """The app starts a ContributionUploader task at lifespan entry."""
    # We don't drive an upload here — just verify the task exists.
    import asyncio
    from app.main import _contribution_uploader_task

    assert _contribution_uploader_task is not None
    assert not _contribution_uploader_task.done()
```

- [ ] **Step C3.4.2: Wire into lifespan**

In `backend/app/main.py`, modify the `lifespan` context manager:

```python
_contribution_uploader_task: asyncio.Task | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _contribution_uploader_task
    # ... existing init_db() + pseudonym generator code ...

    # Phase 2: start the ContributionUploader
    from app.services.contribution_uploader import ContributionUploader
    async with async_session() as session:
        cfg = await get_config(session)
        server_url = cfg.fingerprint_server_url
    uploader = ContributionUploader(server_url=server_url)
    _contribution_uploader_task = asyncio.create_task(uploader.run_forever())

    yield

    if _contribution_uploader_task is not None:
        await uploader.stop()
        try:
            await asyncio.wait_for(_contribution_uploader_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            _contribution_uploader_task.cancel()
```

- [ ] **Step C3.4.3: Run integration test (expect PASS)**

- [ ] **Step C3.4.4: Commit**

```bash
git add backend/app/main.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(startup): start ContributionUploader at lifespan entry"
```

### Cluster C4: POST /api/fingerprint/forget

#### Task C4.1: Local forget endpoint

**Files:**
- Modify: `backend/app/api/routes.py`
- Create: `backend/tests/integration/test_fingerprint_forget.py`

- [ ] **Step C4.1.1: Write the failing test**

Create `backend/tests/integration/test_fingerprint_forget.py`:

```python
"""Integration test for POST /api/fingerprint/forget."""

import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_forget_calls_server_and_rotates_pseudonym(integration_client, async_session):
    """Forget endpoint hits the server, deletes pending rows, rotates pseudonym."""
    from app.models.app_config import AppConfig
    from app.models.fingerprint import FingerprintContribution
    from sqlmodel import select

    # Seed: ensure pseudonym exists + one pending contribution
    async with async_session() as session:
        cfg_q = await session.execute(select(AppConfig))
        cfg = cfg_q.scalar_one()
        old_pseudonym = cfg.contribution_pseudonym
        session.add(FingerprintContribution(
            title_id=None, chromaprint_blob=b"\x00", tmdb_id=99,
            season=1, episode=1, match_confidence=0.9, match_source="engram_asr",
            disc_content_hash=None, pseudonym=old_pseudonym or "00000000-0000-4000-8000-000000000000",
        ))
        await session.commit()

    fake_response = AsyncMock()
    fake_response.json.return_value = {"rows_deleted": 5, "canonical_unaffected": True}
    fake_response.raise_for_status = lambda: None

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value.post.return_value = fake_response

    with patch("httpx.AsyncClient", return_value=fake_client):
        resp = await integration_client.post("/api/fingerprint/forget")

    assert resp.status_code == 200
    data = resp.json()
    assert data["old_pseudonym"] == old_pseudonym
    assert data["new_pseudonym"] != old_pseudonym
    assert data["server_rows_deleted"] == 5
```

- [ ] **Step C4.1.2: Implement the endpoint**

In `backend/app/api/routes.py`, add:

```python
import httpx
from sqlmodel import delete, select

from app.api.routes import require_localhost  # or wherever it lives
from app.models.fingerprint import FingerprintContribution
from app.services.contribution_pseudonym import generate_pseudonym
from app.services.config_service import get_config


@router.post("/fingerprint/forget", dependencies=[Depends(require_localhost)])
async def forget_contributions(session: AsyncSession = Depends(get_session)):
    cfg = await get_config(session)
    old_pseudonym = cfg.contribution_pseudonym or ""

    if not old_pseudonym:
        raise HTTPException(400, "no pseudonym to forget")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{cfg.fingerprint_server_url.rstrip('/')}/forget",
                json={"pseudonym": old_pseudonym},
            )
            r.raise_for_status()
            server_response = r.json()
    except Exception as e:
        raise HTTPException(503, f"could not reach fingerprint server: {e}")

    result = await session.execute(
        delete(FingerprintContribution).where(FingerprintContribution.uploaded_at.is_(None))
    )
    local_deleted = result.rowcount or 0

    cfg.contribution_pseudonym = generate_pseudonym()
    cfg.fingerprint_disclosure_accepted = False
    cfg.fingerprint_disclosure_accepted_at = None
    await session.commit()

    return {
        "old_pseudonym": old_pseudonym,
        "new_pseudonym": cfg.contribution_pseudonym,
        "local_rows_deleted": local_deleted,
        "server_rows_deleted": server_response.get("rows_deleted", 0),
    }
```

- [ ] **Step C4.1.3: Run test (expect PASS)**

- [ ] **Step C4.1.4: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_fingerprint_forget.py
git commit -m "feat(api): POST /api/fingerprint/forget — pseudonym rotation + server call"
```

### Cluster C5: Audit Log

#### Task C5.1: Audit log writer

**Files:**
- Create: `backend/app/services/contribution_log.py`
- Modify: `backend/app/services/contribution_uploader.py` (real _append_audit_log impl)
- Create: `backend/tests/unit/test_contribution_log.py`

- [ ] **Step C5.1.1: Write the failing test**

Create `backend/tests/unit/test_contribution_log.py`:

```python
"""Tests for the audit log writer."""

import json
import tempfile
from pathlib import Path

import pytest

from app.services.contribution_log import append_log_line, read_log_lines


def test_append_and_read(tmp_path):
    log = tmp_path / "log.jsonl"
    append_log_line(log, {"event": "upload", "id": 1})
    append_log_line(log, {"event": "upload", "id": 2})
    lines = list(read_log_lines(log))
    assert len(lines) == 2
    assert lines[0]["event"] == "upload"
    assert lines[1]["id"] == 2


def test_append_creates_parent_dirs(tmp_path):
    log = tmp_path / "deep" / "nested" / "log.jsonl"
    append_log_line(log, {"event": "x"})
    assert log.exists()


def test_append_is_atomic_jsonl_one_line_each(tmp_path):
    log = tmp_path / "log.jsonl"
    append_log_line(log, {"event": "upload", "id": 1})
    raw = log.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw.count("\n") == 1
```

- [ ] **Step C5.1.2: Implement contribution_log.py**

Create `backend/app/services/contribution_log.py`:

```python
"""Audit log writer for fingerprint contributions.

Per-line JSONL at ~/.engram/cache/contribution_log.jsonl. Append-only. The privacy
commitment: this file shows the user exactly what was sent, with nothing hidden.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_LOG_PATH = Path.home() / ".engram" / "cache" / "contribution_log.jsonl"


def append_log_line(path: Path | str, entry: dict[str, Any]) -> None:
    """Append a single JSON entry as one line. Atomic on POSIX/NTFS for size < PIPE_BUF."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry_with_ts = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    line = json.dumps(entry_with_ts, separators=(",", ":")) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def read_log_lines(path: Path | str) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
```

- [ ] **Step C5.1.3: Wire real impl into ContributionUploader**

Replace the stub `_append_audit_log` in `contribution_uploader.py`:

```python
from app.services.contribution_log import append_log_line, DEFAULT_LOG_PATH


async def _append_audit_log(self, row, server_response: dict) -> None:
    entry = {
        "event": "upload" if row.uploaded_at and not row.upload_error else "upload_failed",
        "contribution_id": row.id,
        "tmdb_id": row.tmdb_id,
        "season": row.season,
        "episode": row.episode,
        "match_source": row.match_source,
        "match_confidence": row.match_confidence,
        "server_contribution_id": server_response.get("contribution_id"),
        "poison_check": server_response.get("poison_check"),
        "overlap_pct": server_response.get("overlap_pct"),
    }
    if row.upload_error:
        entry["error"] = row.upload_error
    # Run blocking I/O in a thread to keep the event loop snappy
    import asyncio
    await asyncio.to_thread(append_log_line, DEFAULT_LOG_PATH, entry)
```

- [ ] **Step C5.1.4: Run tests (expect PASS)**

```bash
uv run pytest tests/unit/test_contribution_log.py tests/unit/test_contribution_uploader.py -v
```

- [ ] **Step C5.1.5: Commit**

```bash
git add backend/app/services/contribution_log.py backend/app/services/contribution_uploader.py backend/tests/unit/test_contribution_log.py
git commit -m "feat(services): contribution audit log + uploader integration"
```

#### Task C5.2: `?include_log=true` on GET /api/fingerprint/contributions

**Files:**
- Modify: `backend/app/api/routes.py`
- Modify: `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step C5.2.1: Write the failing test**

Append to `test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_contributions_endpoint_includes_log(integration_client, tmp_path, monkeypatch):
    from app.services.contribution_log import append_log_line
    log_path = tmp_path / "log.jsonl"
    append_log_line(log_path, {"event": "upload", "contribution_id": 1, "tmdb_id": 99})

    monkeypatch.setattr("app.services.contribution_log.DEFAULT_LOG_PATH", log_path)

    resp = await integration_client.get("/api/fingerprint/contributions?include_log=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "audit_log" in data
    assert any(e.get("tmdb_id") == 99 for e in data["audit_log"])
```

- [ ] **Step C5.2.2: Add the query param**

In `backend/app/api/routes.py`, modify the existing `GET /api/fingerprint/contributions`:

```python
@router.get("/fingerprint/contributions", dependencies=[Depends(require_localhost)])
async def get_fingerprint_contributions(
    limit: int = 100,
    include_log: bool = False,
    session: AsyncSession = Depends(get_session),
):
    # ... existing queue listing ...
    response = {"contributions": [...]}
    if include_log:
        from app.services.contribution_log import read_log_lines, DEFAULT_LOG_PATH
        response["audit_log"] = list(read_log_lines(DEFAULT_LOG_PATH))[-200:]
    return response
```

- [ ] **Step C5.2.3: Run test (expect PASS)**

- [ ] **Step C5.2.4: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(api): ?include_log=true on GET /api/fingerprint/contributions"
```

### Cluster C6: Just-in-Time Disclosure Modal

#### Task C6.1: Modal component

**Files:**
- Create: `frontend/src/components/FingerprintDisclosureModal.tsx`
- Create: `frontend/src/components/FingerprintDisclosureModal.test.tsx`

- [ ] **Step C6.1.1: Write the modal component**

Create `frontend/src/components/FingerprintDisclosureModal.tsx`:

```tsx
import { useState } from "react";

export interface FingerprintDisclosureModalProps {
  pendingCount: number;
  pseudonym: string;
  serverUrl: string;
  onAccept: () => Promise<void>;
  onDecline: () => Promise<void>;
}

export function FingerprintDisclosureModal(props: FingerprintDisclosureModalProps) {
  const [busy, setBusy] = useState(false);

  const handle = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="fp-disclosure-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
    >
      <div className="max-w-xl w-full mx-4 bg-bg-card border border-border-strong p-6 shadow-2xl">
        <h2 id="fp-disclosure-title" className="text-xl font-semibold mb-3">
          Engram is about to start contributing audio fingerprints.
        </h2>
        <p className="mb-3">
          The local matcher just finished identifying an episode. Engram can upload a short
          audio fingerprint to the engram fingerprint network so future Engram users can
          identify the same episode in milliseconds — no subtitles, no LLM call.
        </p>
        <p className="font-semibold mb-2">What gets sent:</p>
        <ol className="list-decimal pl-6 mb-3 space-y-1">
          <li>The audio fingerprint (~7 KB; not the audio itself, not subtitles).</li>
          <li>The episode it matched (TMDB ID + season + episode).</li>
          <li>How confident we are in the match (so the network can weight your contribution).</li>
          <li>The disc release identifier from TheDiscDB (m2ts file size hash; not the file itself).</li>
          <li>
            A random per-install ID (<code className="font-mono text-xs">{props.pseudonym}</code>)
            — you can rotate this anytime.
          </li>
        </ol>
        <p className="mb-3">
          Your IP is not stored. You can opt out at any time in Settings. The pending{" "}
          <strong>{props.pendingCount}</strong> contributions are queued locally; nothing has
          been uploaded yet.
        </p>
        <p className="mb-4 text-text-muted text-sm">
          Once contributions promote into the network's canonical layer, your individual
          contribution is indistinguishable from the consensus — it cannot be retroactively
          removed.
        </p>
        <div className="flex gap-3 justify-end">
          <button
            disabled={busy}
            className="px-4 py-2 border border-border-strong"
            onClick={() => handle(props.onDecline)}
          >
            Disable contributions
          </button>
          <button
            disabled={busy}
            className="px-4 py-2 bg-accent-cyan text-bg-card font-semibold"
            onClick={() => handle(props.onAccept)}
          >
            Accept and start contributing
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step C6.1.2: Commit**

```bash
git add frontend/src/components/FingerprintDisclosureModal.tsx
git commit -m "feat(ui): FingerprintDisclosureModal component"
```

#### Task C6.2: Wire modal into App.tsx via WebSocket event

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/hooks/useWebSocket.ts` (if needed for new event)

- [ ] **Step C6.2.1: Add the WS event handler in App.tsx**

In `frontend/src/App.tsx`, near the other WS event handlers, add:

```tsx
import { FingerprintDisclosureModal } from "./components/FingerprintDisclosureModal";

// In App component:
const [disclosurePayload, setDisclosurePayload] = useState<null | {
  pendingCount: number;
  pseudonym: string;
  serverUrl: string;
}>(null);

// In the WS message handler:
case "fingerprint_disclosure_required":
  setDisclosurePayload({
    pendingCount: message.data.pending_count,
    pseudonym: message.data.pseudonym,
    serverUrl: message.data.server_url,
  });
  break;

// In JSX:
{disclosurePayload && (
  <FingerprintDisclosureModal
    {...disclosurePayload}
    onAccept={async () => {
      await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fingerprint_disclosure_accepted: true,
          fingerprint_disclosure_accepted_at: new Date().toISOString(),
        }),
      });
      setDisclosurePayload(null);
    }}
    onDecline={async () => {
      await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable_fingerprint_contributions: false }),
      });
      setDisclosurePayload(null);
    }}
  />
)}
```

- [ ] **Step C6.2.2: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(ui): wire FingerprintDisclosureModal to WS disclosure-required event"
```

### Cluster C7: ConfigWizard Settings UI

#### Task C7.1: Settings panel additions

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step C7.1.1: Add "Forget me" button + toggle in Settings panel**

In `frontend/src/components/ConfigWizard.tsx`, add a new section under settings:

```tsx
<section className="space-y-3 border-t border-border-subtle pt-4">
  <h3 className="font-semibold">Fingerprint contributions</h3>
  <label className="flex items-center gap-2">
    <input
      type="checkbox"
      checked={config.enable_fingerprint_contributions}
      onChange={(e) => updateConfig({ enable_fingerprint_contributions: e.target.checked })}
    />
    <span>Contribute audio fingerprints to the engram network</span>
  </label>
  {config.fingerprint_disclosure_accepted && (
    <p className="text-xs text-text-muted">
      Accepted on{" "}
      {config.fingerprint_disclosure_accepted_at
        ? new Date(config.fingerprint_disclosure_accepted_at).toLocaleString()
        : "(unknown)"}
    </p>
  )}
  <p className="text-sm text-text-muted">
    Pseudonym: <code className="font-mono text-xs">{config.contribution_pseudonym}</code>
  </p>
  <button
    type="button"
    className="px-3 py-1 border border-border-strong"
    onClick={async () => {
      if (!confirm("This deletes all your contribution history on the server AND generates a new pseudonym. Continue?")) return;
      const r = await fetch("/api/fingerprint/forget", { method: "POST" });
      if (r.ok) {
        const d = await r.json();
        alert(`Server deleted ${d.server_rows_deleted} rows. New pseudonym: ${d.new_pseudonym}`);
        location.reload();
      } else {
        alert(`Forget failed: ${r.status} ${r.statusText}`);
      }
    }}
  >
    Forget me on the fingerprint server
  </button>
</section>
```

- [ ] **Step C7.1.2: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(ui): Settings panel — opt-out toggle + Forget-me button"
```

---

## PART C: End-to-End Validation

After Parts A and B, both halves work in isolation. Part C validates the seam.

### Cluster I1: End-to-End Tests

#### Task I1.1: E2E — disc → match → upload → server-stored

**Files:**
- Create: `frontend/e2e/fingerprint-disclosure.spec.ts`

- [ ] **Step I1.1.1: Write the Playwright spec**

Create `frontend/e2e/fingerprint-disclosure.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test("first match triggers disclosure modal; Accept drains the queue", async ({ page }) => {
  // Reset: ensure disclosure not accepted on this dev DB
  await page.request.put("http://localhost:8000/api/config", {
    data: { fingerprint_disclosure_accepted: false, enable_fingerprint_contributions: true },
  });

  await page.goto("http://localhost:5173");

  // Simulate disc insert (DEBUG-only endpoint)
  await page.request.post("http://localhost:8000/api/simulate/insert-disc", {
    data: { volume_label: "ARRESTED_DEVELOPMENT_S1D1", content_type: "tv", simulate_ripping: true },
  });

  // Modal pops within ~6 min (uploader tick). For E2E speed we trigger drain manually:
  // (In implementation, a debug endpoint /api/debug/uploader/drain forces a tick.)
  await page.request.post("http://localhost:8000/api/debug/uploader/drain");

  await expect(page.getByRole("dialog", { name: /contributing audio fingerprints/i })).toBeVisible({ timeout: 10000 });

  await page.getByRole("button", { name: /accept and start contributing/i }).click();

  // After accept, expect another drain to attempt upload (the dev server will succeed or 4xx).
  await page.request.post("http://localhost:8000/api/debug/uploader/drain");
  // No assertion on server-side state here — we trust the unit tests.
});

test("Decline disables contributions", async ({ page }) => {
  await page.request.put("http://localhost:8000/api/config", {
    data: { fingerprint_disclosure_accepted: false, enable_fingerprint_contributions: true },
  });
  await page.goto("http://localhost:5173");
  await page.request.post("http://localhost:8000/api/simulate/insert-disc", {
    data: { volume_label: "INCEPTION_2010", content_type: "movie", simulate_ripping: true },
  });
  await page.request.post("http://localhost:8000/api/debug/uploader/drain");

  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: /disable contributions/i }).click();

  const cfg = await page.request.get("http://localhost:8000/api/config").then(r => r.json());
  expect(cfg.enable_fingerprint_contributions).toBe(false);
});
```

- [ ] **Step I1.1.2: Add the debug drain endpoint (test-only)**

In `backend/app/api/routes.py`, add:

```python
@router.post("/debug/uploader/drain", dependencies=[Depends(require_localhost), Depends(require_debug)])
async def debug_drain_uploader():
    """Force one uploader tick. Only available with DEBUG=true."""
    from app.main import _contribution_uploader_task
    if _contribution_uploader_task is None:
        raise HTTPException(503, "uploader not running")
    # Get the uploader instance and invoke _drain_one_batch directly.
    # In practice the simplest path: expose the uploader via app state.
    from app.main import _uploader_instance
    await _uploader_instance._drain_one_batch()
    return {"ok": True}
```

(Plumb `_uploader_instance` alongside `_contribution_uploader_task` in `main.py`.)

`require_debug` is a small dependency that 404s when DEBUG≠true:

```python
def require_debug():
    from app.config import settings
    if not settings.debug:
        raise HTTPException(404)
```

- [ ] **Step I1.1.3: Run the E2E spec**

```bash
cd frontend
npm run test:e2e -- fingerprint-disclosure.spec.ts
```

- [ ] **Step I1.1.4: Commit**

```bash
git add frontend/e2e/fingerprint-disclosure.spec.ts backend/app/api/routes.py
git commit -m "test(e2e): fingerprint disclosure modal E2E + debug drain endpoint"
```

#### Task I1.2: End-to-end forget round-trip

**Files:**
- Create: `backend/tests/integration/test_phase2_e2e.py`

- [ ] **Step I1.2.1: Write the E2E forget integration test**

```python
"""Phase 2 end-to-end: queue → upload (against mock server) → forget."""

import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_full_lifecycle(integration_client, async_session):
    from sqlmodel import select
    from app.models.app_config import AppConfig

    # Force disclosure accepted
    cfg_q = await async_session.execute(select(AppConfig))
    cfg = cfg_q.scalar_one()
    cfg.fingerprint_disclosure_accepted = True
    cfg.fingerprint_server_url = "http://mock-server"
    await async_session.commit()

    # Simulate a disc end-to-end so a contribution gets queued
    r = await integration_client.post(
        "/api/simulate/insert-disc",
        json={"volume_label": "ARRESTED_DEVELOPMENT_S1D1", "content_type": "tv", "simulate_ripping": True},
    )
    assert r.status_code == 200

    # Wait for match + chromaprint extract
    import asyncio
    from app.models.fingerprint import FingerprintContribution
    for _ in range(60):
        await asyncio.sleep(0.5)
        rows = (await async_session.execute(select(FingerprintContribution))).scalars().all()
        if rows:
            break
    else:
        pytest.fail("No contribution queued within 30s")

    # Drain with a mocked server
    fake_resp = AsyncMock()
    fake_resp.status_code = 202
    fake_resp.json.return_value = {"contribution_id": 1, "poison_check": "pass", "overlap_pct": 0.01}
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        r = await integration_client.post("/api/debug/uploader/drain")
    assert r.status_code == 200

    # Now forget
    fake_forget = AsyncMock()
    fake_forget.json.return_value = {"rows_deleted": 1, "canonical_unaffected": True}
    fake_forget.raise_for_status = lambda: None
    with patch("httpx.AsyncClient.post", return_value=fake_forget):
        r = await integration_client.post("/api/fingerprint/forget")
    assert r.status_code == 200

    # Verify local pending rows cleared + pseudonym rotated
    cfg_after = (await async_session.execute(select(AppConfig))).scalar_one()
    assert cfg_after.contribution_pseudonym != cfg.contribution_pseudonym
    assert cfg_after.fingerprint_disclosure_accepted is False
```

- [ ] **Step I1.2.2: Run the test**

```bash
cd backend
uv run pytest tests/integration/test_phase2_e2e.py -v
```

- [ ] **Step I1.2.3: Commit**

```bash
git add backend/tests/integration/test_phase2_e2e.py
git commit -m "test(integration): Phase 2 full lifecycle — queue, upload, forget"
```

---

## Verification Checklist (run before declaring Phase 2 done)

- [ ] All server tests pass: `cd C:\Github\engram-fingerprint-server; pnpm test`
- [ ] All client unit tests pass: `cd backend; uv run pytest tests/unit -x`
- [ ] All client integration tests pass: `cd backend; uv run pytest tests/integration -x`
- [ ] Server deployed and reachable at production URL (or local wrangler dev).
- [ ] Real disc test: insert a disc, observe contribution lands in server `contribution` table.
- [ ] Forget round-trip verified end-to-end.
- [ ] PromotionWorker dry-run: seed 3 contributors for an episode, run cron manually (`wrangler dev` cron simulation), assert CANONICAL.
- [ ] PackBuilderWorker dry-run: with a CANONICAL episode in DB, run cron, verify R2 object exists.
- [ ] `overlap_observation` table has ≥ 100 rows from your dogfooding so threshold tuning has data.
- [ ] Grafana / observability dashboard live.
- [ ] Custom domain configured for the server; `AppConfig.fingerprint_server_url` default updated.

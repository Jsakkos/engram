# Post-Completion Track Reassignment with Fingerprint Correction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users reassign a track on an already-completed job (to a different episode, an extra, or
discard), moving the organized library file, retracting the erroneous fingerprint from the shared network,
and re-contributing the corrected one.

**Architecture:** "In-place amendment" — a completed job is corrected without re-entering the job state
machine. Three shared units: a new server `POST /v1/retract` endpoint that deletes one exact fingerprint and
re-derives canonical; a client `ContributionCorrectionService` (retract old, re-contribute new); and a thin
`JobManager.amend_title_assignment()` that reuses the existing `organize_tv_episode`/`organize_tv_extras`
organizer functions to move the file. Retractions ride a new local queue table drained by the existing
`ContributionUploader` loop.

**Tech Stack:** Backend — Python/FastAPI, SQLModel + aiosqlite, httpx, pytest. Server —
Cloudflare Workers + TypeScript, D1 (SQLite), zod, vitest. Frontend — React 18 + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-06-21-track-reassignment-fingerprint-correction-design.md`

**Refinement vs spec:** The spec proposed adding `uploaded_fingerprint_sha256` + status columns to
`FingerprintContribution`. We instead model retractions as a dedicated `FingerprintRetraction` queue table
(a clean mirror of the existing two-phase contribution pattern) and recompute the sha256 from the stored blob
at correction time (`fingerprint_sha256` is deterministic from the blob). This avoids migrating the existing
table and reuses the uploader's generic `_sweep_queue` machinery.

---

## Phase 1 — Server: `POST /v1/retract` (repo: `C:\Github\engram-fingerprint-server`)

> All Phase 1 work is in the **separate** server repo. `main` auto-deploys to prod — branch first.
> Run tests with `pnpm test` (vitest). Run `pnpm run lint` (biome) before committing.

### Task 1: Retract request/response schemas

**Files:**
- Modify: `src/schemas.ts` (append after `ForgetResponseSchema`, ~line 91)
- Test: `test/schemas.test.ts` (create if absent; otherwise append)

- [ ] **Step 1: Write the failing test**

```ts
// test/schemas.test.ts
import { describe, expect, it } from "vitest";
import { RetractRequestSchema } from "../src/schemas";

describe("RetractRequestSchema", () => {
  const base = {
    wire_format_version: 1 as const,
    pseudonym: "00000000-0000-4000-8000-000000000000",
    tmdb_id: 1396,
    season: 3,
    episode: 10,
    fingerprint_sha256_b64: "AAAA",
  };

  it("accepts a valid retract body", () => {
    expect(RetractRequestSchema.safeParse(base).success).toBe(true);
  });

  it("accepts null season/episode (movie fingerprint)", () => {
    expect(RetractRequestSchema.safeParse({ ...base, season: null, episode: null }).success).toBe(true);
  });

  it("rejects a non-UUID pseudonym", () => {
    expect(RetractRequestSchema.safeParse({ ...base, pseudonym: "nope" }).success).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test -- schemas`
Expected: FAIL — `RetractRequestSchema` is not exported.

- [ ] **Step 3: Add the schemas**

```ts
// src/schemas.ts — append after ForgetResponseSchema
export const RetractRequestSchema = z.object({
  wire_format_version: z.literal(1),
  pseudonym: UUIDv4,
  tmdb_id: z.number().int().positive(),
  season: z.number().int().min(0).nullable(),
  episode: z.number().int().min(0).nullable(),
  // SHA256 of the decompressed varint stream — the server's per-fingerprint dedup key.
  fingerprint_sha256_b64: Base64,
});

export const RetractResponseSchema = z.object({
  deleted: z.number().int().min(0),
  canonical: z.enum(["requeued", "removed", "untouched"]),
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test -- schemas`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/schemas.ts test/schemas.test.ts
git commit -m "feat(retract): add retract request/response schemas"
```

### Task 2: Export `promoteOne` for synchronous re-derivation

**Files:**
- Modify: `src/workers/promotion.ts:54` (add `export`)

- [ ] **Step 1: Add the export keyword**

Change the signature at `src/workers/promotion.ts:54` from `async function promoteOne(` to
`export async function promoteOne(`.

No behavior change — `promoteOne` already aggregates ALL remaining contributions for the group (cumulative)
and upserts `episode_canonical`, so calling it after a delete re-derives the consensus without the bad vote.
Note: it `return`s early when zero contributions remain (it does NOT delete canonical) — the retract handler
deletes canonical explicitly in that case (Task 3).

- [ ] **Step 2: Verify the build still passes**

Run: `pnpm test`
Expected: PASS (no test references promoteOne yet; this just confirms nothing broke).

- [ ] **Step 3: Commit**

```bash
git add src/workers/promotion.ts
git commit -m "refactor(promotion): export promoteOne for synchronous re-derivation"
```

### Task 3: `handleRetract` route + wiring

**Files:**
- Create: `src/routes/retract.ts`
- Modify: `src/index.ts` (import + route)
- Test: `test/retract.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// test/retract.test.ts
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { handleRetract } from "../src/routes/retract";

const PSEUDO = "00000000-0000-4000-8000-000000000001";
const OTHER = "00000000-0000-4000-8000-000000000002";

async function seedContribution(opts: {
  pseudonym: string;
  episode: number;
  sha: Uint8Array;
  promoted?: boolean;
}) {
  await env.DB.prepare(
    `INSERT INTO contribution
       (pseudonym, tmdb_id, season, episode, fingerprint, fingerprint_sha256,
        match_confidence, match_source, client_version, poison_check, promoted_at)
     VALUES (?, 1396, 3, ?, ?, ?, 0.9, 'engram_asr', 'test', 'pass', ?)`,
  )
    .bind(opts.pseudonym, opts.episode, new Uint8Array([1, 2, 3]), opts.sha,
          opts.promoted ? 1 : null)
    .run();
}

function retractReq(body: object): Request {
  return new Request("https://x/v1/retract", { method: "POST", body: JSON.stringify(body) });
}

const b64 = (u: Uint8Array) => btoa(String.fromCharCode(...u));

describe("handleRetract", () => {
  beforeEach(async () => {
    await env.DB.exec("DELETE FROM contribution");
    await env.DB.exec("DELETE FROM episode_canonical");
    await env.DB.exec("DELETE FROM canonical_sketch");
  });

  it("deletes only the targeted fingerprint, leaving same-episode siblings", async () => {
    const badSha = new Uint8Array(32).fill(7);
    const goodSha = new Uint8Array(32).fill(9);
    await seedContribution({ pseudonym: PSEUDO, episode: 10, sha: badSha });
    await seedContribution({ pseudonym: OTHER, episode: 10, sha: goodSha });

    const resp = await handleRetract(
      retractReq({ wire_format_version: 1, pseudonym: PSEUDO, tmdb_id: 1396,
                   season: 3, episode: 10, fingerprint_sha256_b64: b64(badSha) }),
      env,
    );
    expect(resp.status).toBe(200);
    const json = await resp.json();
    expect(json.deleted).toBe(1);
    expect(json.canonical).toBe("requeued");

    const remaining = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM contribution WHERE tmdb_id=1396 AND season=3 AND episode=10",
    ).first<{ n: number }>();
    expect(remaining?.n).toBe(1);
  });

  it("removes canonical when no votes remain", async () => {
    const sha = new Uint8Array(32).fill(7);
    await seedContribution({ pseudonym: PSEUDO, episode: 10, sha });
    await env.DB.prepare(
      `INSERT INTO episode_canonical (tmdb_id, season, episode, tier, fingerprint,
         unique_contributors, mean_confidence, promoted_at)
       VALUES (1396, 3, 10, 'candidate', ?, 1, 0.9, unixepoch())`,
    ).bind(new Uint8Array([1, 2, 3])).run();

    const resp = await handleRetract(
      retractReq({ wire_format_version: 1, pseudonym: PSEUDO, tmdb_id: 1396,
                   season: 3, episode: 10, fingerprint_sha256_b64: b64(sha) }),
      env,
    );
    const json = await resp.json();
    expect(json.deleted).toBe(1);
    expect(json.canonical).toBe("removed");
    const canon = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM episode_canonical WHERE tmdb_id=1396 AND season=3 AND episode=10",
    ).first<{ n: number }>();
    expect(canon?.n).toBe(0);
  });

  it("is idempotent — a missing row returns deleted:0 / untouched", async () => {
    const sha = new Uint8Array(32).fill(7);
    const resp = await handleRetract(
      retractReq({ wire_format_version: 1, pseudonym: PSEUDO, tmdb_id: 1396,
                   season: 3, episode: 10, fingerprint_sha256_b64: b64(sha) }),
      env,
    );
    const json = await resp.json();
    expect(json.deleted).toBe(0);
    expect(json.canonical).toBe("untouched");
  });

  it("cannot delete another pseudonym's contribution", async () => {
    const sha = new Uint8Array(32).fill(7);
    await seedContribution({ pseudonym: OTHER, episode: 10, sha });
    const resp = await handleRetract(
      retractReq({ wire_format_version: 1, pseudonym: PSEUDO, tmdb_id: 1396,
                   season: 3, episode: 10, fingerprint_sha256_b64: b64(sha) }),
      env,
    );
    const json = await resp.json();
    expect(json.deleted).toBe(0);
    const remaining = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM contribution",
    ).first<{ n: number }>();
    expect(remaining?.n).toBe(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm test -- retract`
Expected: FAIL — `../src/routes/retract` does not exist.

- [ ] **Step 3: Implement the handler**

```ts
// src/routes/retract.ts
import { RetractRequestSchema } from "../schemas";
import type { Env } from "./contribute";
import { promoteOne } from "../workers/promotion";

export async function handleRetract(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }

  const parsed = RetractRequestSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { error: "schema validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }
  const { pseudonym, tmdb_id, season, episode, fingerprint_sha256_b64 } = parsed.data;

  let sha: Uint8Array;
  try {
    sha = Uint8Array.from(atob(fingerprint_sha256_b64), (c) => c.charCodeAt(0));
  } catch {
    return new Response("invalid base64", { status: 400 });
  }

  // Delete only THIS pseudonym's exact fingerprint for this identity (cascades
  // overlap_observation). The pseudonym predicate enforces caller isolation —
  // same trust model as /forget. `season IS ?` / `episode IS ?` handle NULLs.
  const del = await env.DB.prepare(
    `DELETE FROM contribution
       WHERE pseudonym = ? AND tmdb_id = ? AND season IS ? AND episode IS ?
         AND fingerprint_sha256 = ?`,
  )
    .bind(pseudonym, tmdb_id, season, episode, sha)
    .run();
  const deleted = del.meta.changes ?? 0;

  if (deleted === 0) {
    return Response.json({ deleted: 0, canonical: "untouched" as const }, { status: 200 });
  }

  // Re-derive canonical from whatever votes remain.
  const remaining = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM contribution
       WHERE tmdb_id = ? AND season IS ? AND episode IS ?`,
  )
    .bind(tmdb_id, season, episode)
    .first<{ n: number }>();

  let canonical: "requeued" | "removed";
  if ((remaining?.n ?? 0) > 0) {
    // promoteOne aggregates all remaining contributions and upserts the consensus
    // fingerprint/tier without the retracted vote (immediate heal — no waiting for cron).
    await promoteOne(env, tmdb_id, season, episode);
    canonical = "requeued";
  } else {
    // No evidence left: promoteOne would no-op, so drop canonical + sketch explicitly.
    // (canonical_sketch is otherwise rebuilt hourly; removing it now prevents a ghost
    // result from identify/anti-poison in the gap.)
    await env.DB.batch([
      env.DB.prepare(
        `DELETE FROM episode_canonical WHERE tmdb_id = ? AND season IS ? AND episode IS ?`,
      ).bind(tmdb_id, season, episode),
      env.DB.prepare(
        `DELETE FROM canonical_sketch WHERE tmdb_id = ? AND season IS ? AND episode IS ?`,
      ).bind(tmdb_id, season, episode),
    ]);
    canonical = "removed";
  }

  return Response.json({ deleted, canonical }, { status: 200 });
}
```

- [ ] **Step 4: Wire the route into the router**

In `src/index.ts`, add the import after the `handleForget` import (line 5):

```ts
import { handleRetract } from "./routes/retract";
```

And add the route block after the `/v1/forget` block (after line 52):

```ts
  if (url.pathname === "/v1/retract") {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }
    return handleRetract(request, env);
  }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pnpm test -- retract`
Expected: PASS (4 tests)

- [ ] **Step 6: Lint + commit**

```bash
pnpm run lint
git add src/routes/retract.ts src/index.ts
git commit -m "feat(retract): add POST /v1/retract per-fingerprint retraction + re-derivation"
```

---

## Phase 2 — Client: retraction queue + correction service (repo: `engram`)

> Backend work from `backend/`. Tests: `uv run pytest`. Lint/format: `uv run ruff check .` / `uv run ruff format .`.
> If `backend/engram.db` is a 0-byte stub in this worktree, run
> `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` once so tables exist.

### Task 4: `FingerprintRetraction` queue model

**Files:**
- Modify: `backend/app/models/fingerprint.py` (append a model)
- Test: `backend/tests/unit/test_fingerprint_retraction_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_fingerprint_retraction_model.py
import pytest
from sqlmodel import select

from app.database import async_session, init_db
from app.models.fingerprint import FingerprintRetraction


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_retraction_row_roundtrips():
    async with async_session() as session:
        row = FingerprintRetraction(
            pseudonym="00000000-0000-4000-8000-000000000000",
            tmdb_id=1396,
            season=3,
            episode=10,
            fingerprint_sha256=b"\x07" * 32,
        )
        session.add(row)
        await session.commit()

        fetched = (await session.execute(select(FingerprintRetraction))).scalars().all()
        assert len(fetched) == 1
        assert fetched[0].upload_status is None
        assert fetched[0].fingerprint_sha256 == b"\x07" * 32
        await session.delete(fetched[0])
        await session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fingerprint_retraction_model.py -v`
Expected: FAIL — `cannot import name 'FingerprintRetraction'`.

- [ ] **Step 3: Add the model**

Append to `backend/app/models/fingerprint.py`:

```python
class FingerprintRetraction(SQLModel, table=True):
    """Local-only queue row requesting deletion of one already-uploaded fingerprint.

    Created when a user reassigns a track whose fingerprint was already uploaded.
    The ContributionUploader drains this table by POSTing /v1/retract, mirroring
    the two-phase contribution pattern. The original FingerprintContribution row is
    deleted at correction time; this row carries only what the server needs to find
    and delete the contribution: pseudonym + identity + fingerprint_sha256.
    """

    __tablename__ = "fingerprint_retractions"

    id: int | None = Field(default=None, primary_key=True)
    queued_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("(datetime('now'))")},
    )

    pseudonym: str
    tmdb_id: int
    season: int | None = None
    episode: int | None = None
    # SHA256 of the decompressed varint stream — the server's per-fingerprint dedup key.
    fingerprint_sha256: bytes

    # Uploader state (mirrors FingerprintContribution; same transient/permanent semantics).
    uploaded_at: datetime | None = None
    upload_attempts: int = Field(default=0)
    upload_status: str | None = Field(default=None)  # None=pending; "success"; "failed"
    upload_error_msg: str | None = Field(default=None)
```

The table is created by `create_all` over the metadata (so frozen builds, which skip Alembic, get it too —
same reasoning as `DiscContribution`'s model-level index comment).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fingerprint_retraction_model.py -v`
Expected: PASS

- [ ] **Step 5: Add an Alembic migration for dev parity**

```bash
uv run alembic revision --autogenerate -m "add fingerprint_retractions queue"
```

Verify the generated migration's `upgrade()` creates `fingerprint_retractions` and `downgrade()` drops it.
If autogenerate missed it, hand-write `op.create_table(...)` mirroring the model columns.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/fingerprint.py backend/tests/unit/test_fingerprint_retraction_model.py backend/alembic/versions/
git commit -m "feat(contrib): add FingerprintRetraction local queue model"
```

### Task 5: `ContributionCorrectionService`

**Files:**
- Create: `backend/app/services/contribution_correction.py`
- Test: `backend/tests/unit/test_contribution_correction.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_contribution_correction.py
import pytest
from sqlmodel import select

from app.database import async_session, init_db
from app.matcher.chromaprint_extractor import ChromaprintResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState
from app.models.fingerprint import FingerprintContribution, FingerprintRetraction
from app.services.contribution_correction import ContributionCorrectionService, NewTarget


def _blob() -> bytes:
    return ChromaprintResult(hashes=[1, 2, 3, 4], duration_seconds=10.0, fpcalc_version="t").to_blob()


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import text as _t
        await session.execute(_t("DELETE FROM fingerprint_contributions"))
        await session.execute(_t("DELETE FROM fingerprint_retractions"))
        await session.commit()


async def _make_title(session, *, uploaded: bool):
    job = DiscJob(volume_label="BB_S3", content_type=ContentType.TV, state=JobState.COMPLETED,
                  tmdb_id=1396, tmdb_name="Breaking Bad", tmdb_year=2008, detected_season=3)
    session.add(job)
    await session.commit()
    title = DiscTitle(job_id=job.id, title_index=24, duration_seconds=3382,
                      matched_episode="S03E10", chromaprint_blob=_blob())
    session.add(title)
    await session.commit()
    contrib = FingerprintContribution(
        title_id=title.id, chromaprint_blob=_blob(), tmdb_id=1396, season=3, episode=10,
        match_confidence=0.8, match_source="engram_asr",
        pseudonym="00000000-0000-4000-8000-000000000000",
        upload_status="success" if uploaded else None,
    )
    session.add(contrib)
    await session.commit()
    return job, title


async def test_uploaded_row_enqueues_retraction_and_deletes_contribution():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=True)
        await ContributionCorrectionService().correct_title_contribution(
            session, title, NewTarget(kind="extra"), job=job,
            enable_contributions=True, pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        contribs = (await session.execute(select(FingerprintContribution))).scalars().all()
        retractions = (await session.execute(select(FingerprintRetraction))).scalars().all()
        assert contribs == []
        assert len(retractions) == 1
        assert retractions[0].season == 3 and retractions[0].episode == 10


async def test_pending_row_deletes_without_retraction():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=False)
        await ContributionCorrectionService().correct_title_contribution(
            session, title, NewTarget(kind="extra"), job=job,
            enable_contributions=True, pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        assert (await session.execute(select(FingerprintContribution))).scalars().all() == []
        assert (await session.execute(select(FingerprintRetraction))).scalars().all() == []


async def test_episode_target_recontributes_as_user_review():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=True)
        await ContributionCorrectionService().correct_title_contribution(
            session, title, NewTarget(kind="episode", episode_code="S03E11"), job=job,
            enable_contributions=True, pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        contribs = (await session.execute(select(FingerprintContribution))).scalars().all()
        assert len(contribs) == 1
        assert contribs[0].episode == 11
        assert contribs[0].match_source == "user_review"
        assert contribs[0].match_confidence == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_contribution_correction.py -v`
Expected: FAIL — module `app.services.contribution_correction` does not exist.

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/contribution_correction.py
"""Reconcile fingerprint contributions when a user reassigns a track after the fact.

Retract the erroneous fingerprint (delete it locally if it never uploaded; otherwise
queue a /v1/retract via FingerprintRetraction) and re-contribute the corrected episode
as the highest-trust source (user_review).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.matcher.chromaprint_extractor import ChromaprintResult
from app.models.disc_job import DiscJob, DiscTitle
from app.models.fingerprint import FingerprintContribution, FingerprintRetraction
from app.services.contribution_queue import ContributionQueue
from app.services.zstd_varint_codec import fingerprint_sha256


@dataclass(frozen=True)
class NewTarget:
    """Where a track is being reassigned to."""

    kind: Literal["episode", "extra", "discard"]
    episode_code: str | None = None  # required when kind == "episode"


class ContributionCorrectionService:
    """Retract a track's old fingerprint and (for episodes) re-contribute the new one."""

    async def correct_title_contribution(
        self,
        session: AsyncSession,
        title: DiscTitle,
        new_target: NewTarget,
        *,
        job: DiscJob,
        enable_contributions: bool,
        pseudonym: str | None,
    ) -> None:
        """Reconcile contributions for ``title`` against ``new_target``.

        Operates within the caller's session/transaction — the caller commits.
        Best-effort: never raises on contribution bookkeeping (a network/queue hiccup
        must not block the user-visible file + DB correction).
        """
        try:
            rows = (
                await session.execute(
                    select(FingerprintContribution).where(
                        FingerprintContribution.title_id == title.id
                    )
                )
            ).scalars().all()

            for row in rows:
                if row.upload_status == "success":
                    # Already on the network — queue a retraction, then drop the local row.
                    try:
                        sha = fingerprint_sha256(
                            ChromaprintResult.from_blob(row.chromaprint_blob).hashes
                        )
                        session.add(
                            FingerprintRetraction(
                                pseudonym=row.pseudonym,
                                tmdb_id=row.tmdb_id,
                                season=row.season,
                                episode=row.episode,
                                fingerprint_sha256=sha,
                            )
                        )
                    except Exception:
                        logger.warning(
                            f"Could not derive sha256 for contrib {row.id}; "
                            "deleting local row without queuing retraction",
                            exc_info=True,
                        )
                await session.delete(row)

            # Re-contribute only when the new target is a real episode.
            if new_target.kind == "episode" and new_target.episode_code:
                await self._recontribute(
                    session,
                    title,
                    job=job,
                    episode_code=new_target.episode_code,
                    enable_contributions=enable_contributions,
                    pseudonym=pseudonym,
                )
        except Exception:
            logger.warning(
                f"Contribution correction failed for title {title.id}", exc_info=True
            )

    async def _recontribute(
        self,
        session: AsyncSession,
        title: DiscTitle,
        *,
        job: DiscJob,
        episode_code: str,
        enable_contributions: bool,
        pseudonym: str | None,
    ) -> None:
        if not (title.chromaprint_blob and pseudonym and job.tmdb_id):
            return
        m = re.match(r"S(\d{1,2})E(\d{1,3})", episode_code)
        if not m:
            return
        try:
            tmdb_id_val = int(job.tmdb_id)
        except (TypeError, ValueError):
            return
        disc_hash = None
        if getattr(job, "content_hash", None):
            try:
                disc_hash = bytes.fromhex(job.content_hash)
            except (TypeError, ValueError):
                disc_hash = None
        await ContributionQueue().enqueue(
            session=session,
            title_id=title.id,
            chromaprint_blob=title.chromaprint_blob,
            tmdb_id=tmdb_id_val,
            season=int(m.group(1)),
            episode=int(m.group(2)),
            match_confidence=1.0,
            match_source="user_review",
            disc_content_hash=disc_hash,
            pseudonym=pseudonym,
            show_title=getattr(job, "tmdb_name", None) or getattr(job, "detected_title", None),
            contributions_enabled=enable_contributions,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_contribution_correction.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/contribution_correction.py backend/tests/unit/test_contribution_correction.py
git commit -m "feat(contrib): add ContributionCorrectionService (retract + re-contribute)"
```

### Task 6: Uploader drains the retraction queue

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Test: `backend/tests/unit/test_contribution_uploader_retract.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_contribution_uploader_retract.py
import asyncio

import httpx
import pytest

from app.database import async_session, init_db
from app.models.fingerprint import FingerprintRetraction
from app.services.contribution_uploader import ContributionUploader


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import text as _t
        await session.execute(_t("DELETE FROM fingerprint_retractions"))
        await session.commit()


async def test_retraction_row_posts_to_v1_retract_and_marks_success(monkeypatch):
    async with async_session() as session:
        session.add(FingerprintRetraction(
            pseudonym="00000000-0000-4000-8000-000000000000",
            tmdb_id=1396, season=3, episode=10, fingerprint_sha256=b"\x07" * 32,
        ))
        await session.commit()

    seen = {}

    async def fake_post(self, url, json=None, **kw):
        seen["url"] = url
        seen["json"] = json
        return httpx.Response(200, json={"deleted": 1, "canonical": "requeued"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    uploader = ContributionUploader()
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(1)
        drained, _ = await uploader._sweep_queue(
            FingerprintRetraction, uploader._upload_retraction_row, client, sem
        )
    assert drained == 1
    assert seen["url"].endswith("/v1/retract")
    assert seen["json"]["episode"] == 10
    assert "fingerprint_sha256_b64" in seen["json"]
```

> Note: `_sweep_queue` re-reads config and gates on `enable_fingerprint_contributions` /
> `fingerprint_disclosure_accepted`. Ensure the test DB's `app_config` has both enabled (set them in the
> fixture if `init_db()`'s defaults don't) so the sweep proceeds.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_contribution_uploader_retract.py -v`
Expected: FAIL — `ContributionUploader` has no attribute `_upload_retraction_row`.

- [ ] **Step 3: Add the import**

In `backend/app/services/contribution_uploader.py`, line 24:

```python
from app.models.fingerprint import DiscContribution, FingerprintContribution, FingerprintRetraction
```

- [ ] **Step 4: Add the retraction sweep to `_drain`**

Replace the post-episode disc-sweep block in `_drain` (the existing `if not stop:` block, ~lines 124-128)
with:

```python
            if not stop:
                disc_drained, disc_stop = await self._sweep_queue(
                    DiscContribution, self._upload_disc_row, client, semaphore
                )
                drained += disc_drained
                if not disc_stop:
                    retract_drained, _ = await self._sweep_queue(
                        FingerprintRetraction, self._upload_retraction_row, client, semaphore
                    )
                    drained += retract_drained
```

- [ ] **Step 5: Add the per-row coroutine**

After `_upload_disc_row` (~line 281):

```python
    async def _upload_retraction_row(
        self,
        row_id: int,
        client: httpx.AsyncClient,
        server_url: str,
        semaphore: asyncio.Semaphore,
    ) -> bool:
        """Upload one queued retraction under the concurrency semaphore."""
        async with semaphore:
            async with async_session() as session:
                row = await session.get(FingerprintRetraction, row_id)
                if row is None:
                    return False  # deleted between the ID query and now
                await self._upload_one_retraction(
                    row, session, client=client, server_url=server_url
                )
                return row.upload_status == "success"
```

- [ ] **Step 6: Add the upload body**

After `_upload_one_disc` (~line 530):

```python
    async def _upload_one_retraction(
        self,
        row: FingerprintRetraction,
        session,
        client: httpx.AsyncClient,
        server_url: str,
    ) -> None:
        """POST one retraction to /v1/retract.

        Same per-drain retry budget + transient/permanent classification as
        ``_upload_one``: 4xx -> permanent "failed"; 5xx/429/network -> leave pending
        for a later drain. A 200 with deleted:0 is still success (idempotent).
        """
        import base64

        payload = {
            "wire_format_version": 1,
            "pseudonym": row.pseudonym,
            "tmdb_id": row.tmdb_id,
            "season": row.season,
            "episode": row.episode,
            "fingerprint_sha256_b64": base64.b64encode(row.fingerprint_sha256).decode("ascii"),
        }

        for attempt in range(_MAX_ATTEMPTS):
            backoff: float = 2**attempt
            try:
                resp = await client.post(f"{server_url.rstrip('/')}/v1/retract", json=payload)
                resp.raise_for_status()
                row.upload_status = "success"
                row.uploaded_at = datetime.now(UTC)
                row.upload_error_msg = None
                await session.commit()
                logger.info(
                    f"Retracted fingerprint (tmdb={row.tmdb_id} s{row.season}e{row.episode})"
                )
                return
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    retry_after = _retry_after_seconds(e.response.headers.get("Retry-After"))
                    if retry_after is not None:
                        backoff = min(retry_after, _MAX_RETRY_AFTER)
                    row.upload_attempts += 1
                    await session.commit()
                elif 400 <= status < 500:
                    row.upload_status = "failed"
                    row.upload_error_msg = f"HTTP {status} (permanent)"
                    row.upload_attempts += 1
                    await session.commit()
                    logger.warning(f"Retraction {row.id}: permanent HTTP {status}; marking failed")
                    return
                else:
                    row.upload_attempts += 1
                    await session.commit()
            except httpx.HTTPError as e:
                row.upload_attempts += 1
                await session.commit()
                logger.warning(f"Retraction {row.id}: network error, attempt {attempt + 1}: {e}")

            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(backoff)

        row.upload_error_msg = (
            f"Transient errors after {row.upload_attempts} attempt(s); will retry on a later drain"
        )
        await session.commit()
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_contribution_uploader_retract.py -v`
Expected: PASS

- [ ] **Step 8: Run the broader uploader suite to confirm no regressions**

Run: `uv run pytest tests/unit/ -k uploader -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/unit/test_contribution_uploader_retract.py
git commit -m "feat(contrib): drain FingerprintRetraction queue to /v1/retract"
```

---

## Phase 3 — Client: amend orchestration + endpoint

### Task 7: `JobManager.amend_title_assignment`

**Files:**
- Modify: `backend/app/services/job_manager.py` (add method after `reassign_episode`, ~line 1559)
- Test: `backend/tests/integration/test_amend_completed_title.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_amend_completed_title.py
from pathlib import Path

import pytest

from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.contribution_correction import NewTarget
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def _seed_completed_tv(tmp_path: Path):
    lib = tmp_path / "lib"
    season_dir = lib / "Breaking Bad (2008) [tmdbid-1396]" / "Season 3"
    season_dir.mkdir(parents=True)
    organized = season_dir / "Breaking Bad - S03E10.mkv"
    organized.write_text("fake video")

    async with async_session() as session:
        job = DiscJob(volume_label="BREAKING_BAD_S3_D2", content_type=ContentType.TV,
                      state=JobState.COMPLETED, tmdb_id=1396, tmdb_name="Breaking Bad",
                      tmdb_year=2008, detected_title="Breaking Bad", detected_season=3,
                      disc_number=2)
        session.add(job)
        await session.commit()
        title = DiscTitle(job_id=job.id, title_index=24, duration_seconds=3382,
                          matched_episode="S03E10", state=TitleState.COMPLETED,
                          organized_to=str(organized), is_extra=False)
        session.add(title)
        await session.commit()
        return job.id, title.id, lib


async def test_amend_to_extra_moves_file_and_clears_episode(tmp_path, monkeypatch):
    job_id, title_id, lib = await _seed_completed_tv(tmp_path)
    from app.services import config_service
    cfg = await config_service.get_config()
    monkeypatch.setattr(cfg, "library_tv_path", str(lib))

    await job_manager.amend_title_assignment(job_id, title_id, NewTarget(kind="extra"))

    async with async_session() as session:
        title = await session.get(DiscTitle, title_id)
        assert title.is_extra is True
        assert title.matched_episode is None
        assert title.organized_to is not None and "Extras" in title.organized_to
        assert Path(title.organized_to).exists()
        assert not (lib / "Breaking Bad (2008) [tmdbid-1396]" / "Season 3"
                    / "Breaking Bad - S03E10.mkv").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_amend_completed_title.py -v`
Expected: FAIL — `job_manager` has no attribute `amend_title_assignment`.

- [ ] **Step 3: Implement the method**

Add to `backend/app/services/job_manager.py` after `reassign_episode` (~line 1559):

```python
    async def amend_title_assignment(self, job_id: int, title_id: int, target) -> None:
        """Correct a track on a COMPLETED job in place (reassign / extra / discard).

        Moves the organized library file to its new home, updates the DiscTitle, and
        reconciles the fingerprint network (retract old, re-contribute new). The job
        stays COMPLETED — we never re-enter the state machine.

        ``target`` is a contribution_correction.NewTarget.
        """
        import re as _re

        from app.core.organizer import organize_tv_episode, organize_tv_extras
        from app.services.config_service import get_config
        from app.services.contribution_correction import ContributionCorrectionService

        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError(f"Title {title_id} not found for job {job_id}")
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            if not title.organized_to:
                raise ValueError("Title has no organized file to amend")

            current = Path(title.organized_to)
            if not current.exists():
                raise ValueError(f"Organized file is missing: {current}")

            show = job.tmdb_name or job.detected_title or job.volume_label
            tmdb_id = str(job.tmdb_id) if job.tmdb_id else None

            if target.kind == "episode":
                if not target.episode_code:
                    raise ValueError("episode_code required for episode reassignment")
                result = organize_tv_episode(
                    current,
                    show,
                    target.episode_code,
                    conflict_resolution="ask",  # abort on a real collision; never overwrite
                    year=job.tmdb_year,
                    tmdb_id=tmdb_id,
                    ordering=title.episode_ordering or "aired",
                    episode_group_id=title.episode_group_id,
                )
                if not result.get("success"):
                    raise ValueError(result.get("error") or "Organize failed")
                title.matched_episode = target.episode_code
                title.is_extra = False
                title.organized_to = str(result["final_path"])
            else:  # extra or discard — both land in Extras (discard differs only as intent)
                season = job.detected_season
                if season is None and title.matched_episode:
                    m = _re.match(r"S(\d{1,2})E", title.matched_episode)
                    season = int(m.group(1)) if m else 1
                result = organize_tv_extras(
                    current,
                    show,
                    season or 1,
                    disc_number=job.disc_number or 1,
                    title_index=title.title_index,
                    year=job.tmdb_year,
                    tmdb_id=tmdb_id,
                )
                if not result.get("success"):
                    raise ValueError(result.get("error") or "Organize failed")
                title.matched_episode = None
                title.is_extra = True
                title.organized_to = str(result["final_path"])

            title.match_source = "user"
            title.match_confidence = 1.0
            title.match_details = _strip_review_flags(title.match_details)
            title.organized_from = current.name

            cfg = await get_config()
            await ContributionCorrectionService().correct_title_contribution(
                session,
                title,
                target,
                job=job,
                enable_contributions=cfg.enable_fingerprint_contributions,
                pseudonym=cfg.contribution_pseudonym,
            )

            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                matched_episode=title.matched_episode,
                match_confidence=1.0,
                match_source="user",
            )
            await ws_manager.broadcast_job_update(job_id, job.state.value)

        logger.info(
            f"Job {sanitize_log_value(job_id)}: amended title "
            f"{sanitize_log_value(title_id)} -> {sanitize_log_value(target.kind)}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_amend_completed_title.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/integration/test_amend_completed_title.py
git commit -m "feat(amend): JobManager.amend_title_assignment for completed jobs"
```

### Task 8: REST endpoint

**Files:**
- Modify: `backend/app/api/routes.py` (add request model + route after `reassign_episode`, ~line 3513)
- Test: `backend/tests/integration/test_amend_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_amend_endpoint.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_amend_rejects_non_completed_job(client):
    async with async_session() as session:
        job = DiscJob(volume_label="X", content_type=ContentType.TV, state=JobState.REVIEW_NEEDED)
        session.add(job)
        await session.commit()
        title = DiscTitle(job_id=job.id, title_index=0, duration_seconds=1, state=TitleState.REVIEW)
        session.add(title)
        await session.commit()
        job_id, title_id = job.id, title.id

    resp = await client.post(
        f"/api/jobs/{job_id}/titles/{title_id}/amend",
        json={"target": {"kind": "extra"}},
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_amend_endpoint.py -v`
Expected: FAIL — 404 (route not found) instead of 409.

- [ ] **Step 3: Implement the request model + route**

Add to `backend/app/api/routes.py` after the `reassign_episode` route (~line 3513):

```python
class AmendTarget(BaseModel):
    """Where a completed-job track is being reassigned."""

    kind: Literal["episode", "extra", "discard"]
    episode_code: str | None = None


class AmendRequest(BaseModel):
    target: AmendTarget


@router.post("/jobs/{job_id}/titles/{title_id}/amend")
async def amend_title(
    title_id: int,
    request: AmendRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Reassign a track on a COMPLETED job (episode / extra / discard).

    Moves the organized library file, updates the title, and reconciles the
    fingerprint network. Only valid for completed jobs — jobs still in review use
    the existing review/reassign flow.
    """
    if job.state != JobState.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Amend is only available for completed jobs (state: {job.state.value})",
        )

    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    if request.target.kind == "episode" and not request.target.episode_code:
        raise HTTPException(status_code=400, detail="episode_code required for episode reassignment")

    from app.services.contribution_correction import NewTarget
    from app.services.job_manager import job_manager

    try:
        await job_manager.amend_title_assignment(
            job.id,
            title_id,
            NewTarget(kind=request.target.kind, episode_code=request.target.episode_code),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "amended", "title_id": title_id, "kind": request.target.kind}
```

> Confirm `Literal` is imported at the top of `routes.py` (`from typing import Literal`). It is already used
> by `RematchRequest`'s `source_preference`, so the import exists — if not, add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_amend_endpoint.py -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/api/routes.py backend/tests/integration/test_amend_endpoint.py
git commit -m "feat(amend): POST /api/jobs/{id}/titles/{id}/amend endpoint"
```

---

## Phase 4 — Frontend: History detail panel reassign action

> Frontend work from `frontend/`. If `node_modules` is absent, run `npm install` (then
> `git checkout package-lock.json` before committing — the worktree lock is stale). Build check: `npm run build`.

### Task 9: API client method + types

**Files:**
- Modify: `frontend/src/api/client.ts` (or wherever `reassignEpisode` lives — grep first)

- [ ] **Step 1: Locate the existing reassign API call**

Run: `grep -rn "reassign" frontend/src/api frontend/src/hooks`
Expected: find the function that POSTs `/api/jobs/{id}/titles/{id}/reassign` — mirror its style.

- [ ] **Step 2: Add the amend method**

In the same module, add (matching the file's existing fetch/error conventions):

```ts
export type AmendKind = "episode" | "extra" | "discard";

export async function amendTitle(
  jobId: number,
  titleId: number,
  target: { kind: AmendKind; episode_code?: string },
): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/titles/${titleId}/amend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Amend failed (${res.status})`);
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(amend): frontend amendTitle API client method"
```

### Task 10: Reassign action in HistoryPage detail panel

**Files:**
- Create: `frontend/src/components/HistoryPage/AmendTitleModal.tsx`
- Modify: `frontend/src/components/HistoryPage.tsx` (per-track row action; grep for the track breakdown render)
- Test: `frontend/src/components/HistoryPage/AmendTitleModal.test.tsx`

- [ ] **Step 1: Write the failing component test**

```tsx
// frontend/src/components/HistoryPage/AmendTitleModal.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AmendTitleModal } from "./AmendTitleModal";

describe("AmendTitleModal", () => {
  it("submits an extra amendment", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AmendTitleModal
        open
        title={{ id: 2265, matchedEpisode: "S03E10", titleIndex: 24 }}
        seasonEpisodes={[10, 11, 12, 13]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /mark as extra/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onSubmit).toHaveBeenCalledWith({ kind: "extra" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- AmendTitleModal`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the modal**

```tsx
// frontend/src/components/HistoryPage/AmendTitleModal.tsx
import { useState } from "react";

export interface AmendTitleModalProps {
  open: boolean;
  title: { id: number; matchedEpisode: string | null; titleIndex: number };
  seasonEpisodes: number[];
  hasUploadedFingerprint?: boolean;
  onSubmit: (target: { kind: "episode" | "extra" | "discard"; episode_code?: string }) => Promise<void>;
  onClose: () => void;
}

export function AmendTitleModal(props: AmendTitleModalProps) {
  const { open, title, seasonEpisodes, hasUploadedFingerprint, onSubmit, onClose } = props;
  const [kind, setKind] = useState<"episode" | "extra" | "discard">("episode");
  const [episode, setEpisode] = useState<number | null>(seasonEpisodes[0] ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const season = title.matchedEpisode?.match(/^S(\d{2})E/)?.[1] ?? "01";

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      if (kind === "episode") {
        if (episode == null) throw new Error("Pick an episode");
        const code = `S${season}E${String(episode).padStart(2, "0")}`;
        await onSubmit({ kind: "episode", episode_code: code });
      } else {
        await onSubmit({ kind });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Amend failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="dialog" aria-label="Reassign track" className="amend-modal">
      <h3>Reassign track t{String(title.titleIndex).padStart(2, "0")}</h3>
      <div className="amend-kind">
        <button type="button" aria-pressed={kind === "episode"} onClick={() => setKind("episode")}>
          Episode
        </button>
        <button type="button" aria-pressed={kind === "extra"} onClick={() => setKind("extra")}>
          Mark as Extra
        </button>
        <button type="button" aria-pressed={kind === "discard"} onClick={() => setKind("discard")}>
          Discard
        </button>
      </div>

      {kind === "episode" && (
        <select
          aria-label="Episode"
          value={episode ?? ""}
          onChange={(e) => setEpisode(Number(e.target.value))}
        >
          {seasonEpisodes.map((ep) => (
            <option key={ep} value={ep}>
              Episode {ep}
            </option>
          ))}
        </select>
      )}

      {hasUploadedFingerprint && (
        <p className="amend-note">
          This will retract the previous fingerprint from the shared network and submit your correction.
        </p>
      )}
      {error && <p className="amend-error">{error}</p>}

      <div className="amend-actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" onClick={apply} disabled={busy}>
          {busy ? "Applying…" : "Apply"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test:unit -- AmendTitleModal`
Expected: PASS

- [ ] **Step 5: Wire the modal into HistoryPage**

In `frontend/src/components/HistoryPage.tsx`, find the per-track breakdown render (grep for `organized_to`
or the track list). Add a "Reassign" button per track that opens `AmendTitleModal`, wiring `onSubmit` to
`amendTitle(jobId, title.id, target)` from Task 9 and refreshing the panel on success (the WS
`title_update`/`job_update` will also arrive). Derive `seasonEpisodes` from the job's season, and pass
`hasUploadedFingerprint` from track provenance if available (default true is acceptable — the note is
informational).

- [ ] **Step 6: Build + commit**

```bash
cd frontend && npm run build
git checkout package-lock.json   # if npm install rewrote it
git add frontend/src/components/HistoryPage.tsx frontend/src/components/HistoryPage/AmendTitleModal.tsx frontend/src/components/HistoryPage/AmendTitleModal.test.tsx
git commit -m "feat(amend): reassign action in History detail panel"
```

---

## Phase 5 — Changelog + live cleanup

### Task 11: Changelog entry

**Files:**
- Modify: `CHANGELOG.md` (under `## [Unreleased]` -> `### Added`)

- [ ] **Step 1: Add the entry**

```markdown
- Reassign a track on a completed job from the History detail panel (to a different episode, an extra, or
  discard). Moves the organized library file, retracts the erroneous fingerprint from the shared network, and
  re-contributes the corrected one as a user-verified match. (#NNN)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for track reassignment + fingerprint correction"
```

### Task 12: Live cleanup of jobs 214 / 221

> Run against the user's live backend (`http://127.0.0.1:8000`) only AFTER the feature is merged/running there.
> These steps mutate the real library and DB — confirm each result before the next.

- [ ] **Step 1: Amend job 214 / title 2265 -> extra**

```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/214/titles/2265/amend" \
  -H "Content-Type: application/json" -d '{"target":{"kind":"extra"}}'
```

Expected: `{"status":"amended","title_id":2265,"kind":"extra"}`. Verify
`X:\...\Season 3\Breaking Bad - S03E10.mkv` is gone and the featurette now sits under `Season 3\Extras\`.
(No network retraction occurs — title 2265 was never contributed.)

- [ ] **Step 2: Resolve job 221 / title 2440 (real E10) via the existing review flow**

```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/221/titles/2440/reassign" \
  -H "Content-Type: application/json" -d '{"episode_code":"S03E10"}'
```

Then complete the job's review as usual — the path is free, so organization succeeds.

- [ ] **Step 3: Verify the final library state**

Confirm `X:\...\Breaking Bad (2008) [tmdbid-1396]\Season 3\` holds `S03E06`-`S03E13` correctly, the disc-2
featurette is in `Extras\`, and the network's E10 vote (contrib 1813, the disc-3 fingerprint) is untouched.

---

## Self-Review Notes

- **Spec coverage:** server retract (Tasks 1-3); client correction service + retraction queue (Tasks 4-6);
  file amendment via reused organizer (Task 7); endpoint (Task 8); History UI (Tasks 9-10); changelog
  (Task 11); live cleanup (Task 12); testing woven per task.
- **Type consistency:** `NewTarget(kind, episode_code)` defined in Task 5, used in Tasks 7-8;
  `FingerprintRetraction` defined in Task 4, consumed in Tasks 5-6; `amendTitle` (Task 9) matches the
  endpoint shape (Task 8); `AmendTarget.kind` enum matches `NewTarget.kind`.
- **Out of scope (v1), per spec:** disc-layout correction, cross-show (wrong tmdb_id) reassignment,
  cross-job auto-resolution, movie amendment.

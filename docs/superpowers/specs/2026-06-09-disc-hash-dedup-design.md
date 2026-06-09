# Disc ContentHash Tracking + Hash-Based Dedup — Design

**Status:** Approved design; real-disc assumptions validated 2026-06-09. Ready for implementation planning.

**Scope:** Parts **A** (compute + persist a per-disc content hash for every job) and **B** (use it to fix the same-label dedup bug). Part **C** (single-track re-rip after clean & reinsert) is a *separate* follow-up spec that depends on A.

---

## 1. Motivation

Engram dedups disc-insert events in `JobManager._create_job_for_disc` (`backend/app/services/job_manager.py:439`). For a job in a **post-eject** state (`MATCHING`/`ORGANIZING`) the disc has already been ejected, so a fresh insert on that drive is usually a genuinely new disc — but the code conservatively **blocks** a new disc whose `volume_label` matches the in-flight job's, to avoid spawning a duplicate job from a glitchy eject/reinsert sensor reading.

**The bug:** every disc in a season set shares one volume label (e.g. all of Breaking Bad S2 are labelled `BREAKINGBADS2`). So inserting **Disc 2** while **Disc 1** is still `MATCHING` collides on the label and Disc 2 is rejected as "the same disc lingering." The user must wait for Disc 1 to finish, or eject/reinsert. This was observed live: job #99 was BB S2 D1, and D1/D2 share the label.

**The fix:** a disc's TheDiscDB **ContentHash** (MD5 of the little-endian Int64 `BDMV/STREAM/*.m2ts` file sizes, sorted by name; `VIDEO_TS/*` for DVD) is a true per-disc fingerprint. It already exists as the `DiscJob.content_hash` column and the `compute_content_hash()` function (`backend/app/core/extractor.py:147`), but is computed **only** inside the DiscDB-gated identification block (`identification_coordinator.py:1089`, `if DISCDB_ENABLED and config.discdb_enabled`). DiscDB is gated off, so the hash is **never populated today** (job #99's `content_hash` was null). We compute it for *every* disc job, at insert time, and use it to discriminate discs during dedup.

**Secondary motivation:** the hash is the disc-identity primitive the future single-track re-rip feature (Part C) needs to verify "the same disc was reinserted" before re-ripping one title.

---

## 2. Validated assumptions (real-disc test, 2026-06-09)

A read-only probe (`artifacts/hash_probe.py`, a verbatim mirror of `compute_content_hash`'s Windows branch — pure stdlib, no writes) was run against two physical discs while the frozen build was stopped. Results:

| Insert | Label | Files | Hash | Hash ready after mount |
|--------|-------|-------|------|------------------------|
| Disc 1 #1 | `BREAKINGBADS2` | 111 | `E3A6C429FB3521F720A97FF5A68E4D71` | +0.00s |
| Disc 1 #2 | `BREAKINGBADS2` | 111 | `E3A6C429FB3521F720A97FF5A68E4D71` | +0.00s |
| Disc 2 | `BREAKINGBADS2` | 110 | `8FACC865AFFB5B97D69F3132DAF5439A` | +0.00s |
| Disc 1 #3 (after Disc 2) | `BREAKINGBADS2` | 111 | `E3A6C429FB3521F720A97FF5A68E4D71` | +0.00s |

Conclusions that shape the design:

- **Timing — retry is insurance, not the hot path.** The hash was computable `+0.00s` after mount on all 4 inserts. The **volume label** (which the drive sentinel keys off to fire at all) resolves *slightly later* than `BDMV/STREAM` enumerability — observed as a transient `label=None` while the hash was already computable. So by the time `_create_job_for_disc` runs (after the label resolves), the hash is reliably ready. A small bounded retry covers only cold/marginal discs and will essentially never be exercised.
- **Discrimination — premise confirmed.** Two physically different discs with an **identical** label produced **different** hashes (and even different file counts, 111 vs 110). The hash separates discs the label cannot.
- **Stability — content-based, position-independent.** Disc 1's hash reproduced exactly across three inserts, including after Disc 2 had been in the drive in between. (This is also the property Part C depends on. File sizes come from directory metadata, so the hash is stable even on a disc with read errors — which is why a damaged disc still re-identifies for a re-rip.)

The probe is retained in gitignored `artifacts/` for re-running.

---

## 3. Design

### Approach (chosen)

Compute the hash once, at disc insert, store it on the job, and reuse it downstream (vs. a second computation in identification). This puts the hash exactly where the dedup needs it synchronously, and the timing test confirms the added latency is negligible.

### Components

**3.1 Hash-at-insert** — `JobManager._create_job_for_disc`
- Add a helper `_compute_disc_hash(drive) -> str | None` that wraps `compute_content_hash` with a **bounded retry** (≈3 attempts, ~0.5s apart) and runs it via `asyncio.to_thread` (matching how identification already offloads it).
- Call it inside the existing `self._drive_locks[drive_letter]` lock, **before** the blocking decision.
- Set `content_hash` on the newly created `DiscJob`.

**3.2 Dedup discrimination** — `JobManager._create_job_for_disc`
- Add a pure helper `_same_disc(job, volume_label, new_hash) -> bool`:
  - **both** hashes known → `job.content_hash == new_hash` (a *different* hash ⇒ definitely a different disc ⇒ not a duplicate);
  - either hash absent → `job.volume_label == volume_label` (the conservative fallback — chosen behavior: block when we can't fingerprint).
- Use `_same_disc` **only** in the `post_eject_states` (`MATCHING`/`ORGANIZING`) arm of the blocking predicate. The `disc_required_states` arm (`IDLE`/`IDENTIFYING`/`RIPPING`) stays an unconditional block — the disc is physically in the drive there.

**3.3 Decouple hash from DiscDB** — `IdentificationCoordinator`
- Reuse `job.content_hash` (set at insert) instead of computing it only inside the `DISCDB_ENABLED` block. The DiscDB classifier still consumes it when ungated; the non-DiscDB path now also has a populated hash.
- Keep a best-effort recompute when `job.content_hash` is None (e.g. a job created before this change, or an insert-time miss) — cheap and harmless.

### Data flow

```
disc inserted
  → sentinel fires (volume label readable)
    → _create_job_for_disc (under drive lock)
        → _compute_disc_hash(drive)        # ready ~instantly; retry only if cold
        → blocking predicate uses _same_disc() for post-eject jobs
        → create DiscJob(content_hash=…)
    → identification reuses job.content_hash
      → DiscDB classifier (when ungated) consumes it
```

### Error handling & edge cases

- **Hash None at insert** (cold disc, unreadable, or a non-BD/DVD structure): `_same_disc` falls back to `volume_label` (conservative block). The job is still created with `content_hash=None`.
- **Import / staging jobs** (`create_job_from_staging`, no physical disc): `content_hash` stays None; the existing staging-path dedup is unchanged.
- **Pre-existing in-flight jobs** with null `content_hash`: `_same_disc` falls back to the label comparison. Conservative; no regression.
- **`disc_required_states`** branch: unchanged.
- **15s per-drive creation cooldown** (`_last_job_created_at`): unchanged; multi-minute disc swaps clear it comfortably.

### Testing

- **Unit — `_same_disc` truth table:** same hash ⇒ True; different hash ⇒ False; null hash + same label ⇒ True; null hash + different label ⇒ False.
- **Unit — `_create_job_for_disc` dedup** with `compute_content_hash` patched and a seeded `MATCHING` job: same label + **different** hash ⇒ new job created; same label + **same** hash ⇒ blocked; **null** new hash + same label ⇒ blocked (conservative).
- **Note:** `/api/simulate/insert-disc` bypasses `_create_job_for_disc` (it injects jobs directly), so the dedup path is **unit-tested with mocked hashes**, not via the E2E simulation harness.
- **Real-disc validation:** already performed (§2) and recorded; re-runnable via `artifacts/hash_probe.py`.

---

## 4. Out of scope (follow-ups)

- **Part C — single-track re-rip:** detect a truncated/failed track → prompt "clean & reinsert to re-rip this track" → on reinsert, verify the disc by `content_hash` → re-rip just that title index → re-match → re-check completion. Its own spec; builds on the hash foundation here.
- **DiscDB ungating:** independent decision (`FEATURES.DISCDB` / `DISCDB_ENABLED`).
- The truncated-rip fast-fail fix (`_wait_for_file_ready`) is a separate plan already written: `docs/superpowers/plans/2026-06-09-truncated-rip-fast-fail.md`.

# Disc-hash identification rollout — design

**Date:** 2026-06-22
**Branch:** `feat/fp-network-tier-trust`
**Status:** Approved (design); pending spec review

## Summary

Turn on the existing fingerprint-network **disc-hash** identification path
(`GET /v1/identify-disc`) by default for all engram installs. The integration
and its tier-based trust logic already exist; this change is purely
enablement — flip the `enable_fingerprint_identification` default to on and
migrate existing installs to match.

This is the first of two sub-projects. The second — consuming the per-title
**audio** `/v1/identify` response as an identification signal — is a separate
spec and is **out of scope here**.

## Background

The engram ripper already calls the fingerprint network for whole-disc
identification:

- Read path: `app/core/fingerprint_disc_classifier.py` →
  `GET /v1/identify-disc?hash=<base64url md5>`.
- Call site / gate: `app/services/identification_coordinator.py:1485`
  (`if getattr(config, "enable_fingerprint_identification", False) and job.content_hash:`).
- Second gate: `app/core/curator.py:525`.
- Tier-based trust is already implemented: a `network_confident` hit
  (tier `canonical` or `confirmed`) sets identity directly and pre-assigns
  episodes for `canonical`; `candidate` is not applied as an override.

The flag `enable_fingerprint_identification` defaults to **off**
(`app/models/app_config.py:214`, `default=False`,
`server_default text("0")`), originally "OFF until the catalog is seeded."

The catalog is now seeded enough to be worth using, and we are comfortable
with the existing correctness and per-tier behavior. The only thing standing
between "code exists" and "enabled in production" is the flag/config.

## Goals

- Disc-hash identification runs by default for every install (new and
  existing), with no user-facing toggle.
- Retain a DB-level kill switch / test override (the column stays).
- No change to the tier-trust behavior itself.

## Non-goals

- No audio `/v1/identify` integration (separate spec).
- No settings UI / toggle.
- No change to canonical/confirmed/candidate handling.
- No change to the contributions opt-in or its JIT disclosure.

## Design

### 1. Model default flip

`app/models/app_config.py:214` — `enable_fingerprint_identification`:

- `default=False` → `default=True`
- `sa_column_kwargs={"server_default": text("0")}` → `text("1")`
- Update the comment (no longer "OFF until catalog seeded").

This governs **row creation** only — new installs. It does not touch existing
rows, which is why step 2 is required.

### 2. Alembic data migration

New revision under `backend/migrations/`:

- `upgrade()`: `UPDATE app_config SET enable_fingerprint_identification = 1`
- `downgrade()`: `UPDATE app_config SET enable_fingerprint_identification = 0`

The update is unconditional. With no toggle, every persisted `0` is the stale
old default rather than a deliberate user choice, so promoting all rows to `1`
is correct.

### 3. Tests (TDD)

- Update any test asserting the old default to expect `True` (e.g.
  `_default_config()` helpers in `tests/unit/test_analyst.py`,
  `tests/unit/test_identification_coordinator.py`).
- Add a test confirming the identify-disc path fires under a default
  (unmodified) config — i.e. the gate at
  `identification_coordinator.py:1485` passes when `content_hash` is set and
  config is left at its defaults.
- Follow red-green: write/adjust the failing assertion first, watch it fail,
  then make the model/migration change.

## Why the column stays (not removed)

`getattr(config, "enable_fingerprint_identification", False)` at both call
sites currently doubles as feature gate and as a safety net for pre-column
rows. After the flip, the column remains the single clean "off" state —
usable as a kill switch, a per-environment test override, and a reuse point
for the future audio-identify phase. Deleting the flag would churn both call
sites and remove that control for no benefit.

## Risks / considerations

- **Catalog dependency (real-world gate):** the change only delivers value if
  `/v1/identify-disc` actually has `canonical`/`confirmed` discs to return.
  Verify the server-side disc catalog is non-trivially seeded before/at
  rollout. The code change is safe regardless (a miss returns `None` and
  falls back to TMDB/AI/heuristics), but it is only *useful* with a seeded
  catalog.
- **Privacy:** identification sends only the disc content hash (MD5) to the
  network — a read, distinct from the contributions upload path that carries
  the JIT disclosure. We accept default-on without a separate disclosure for
  this read.
- **Silent behavior change:** existing users gain network identification with
  no UI signal. Acceptable per the "default-on, no toggle" decision; the
  fallback-on-miss semantics keep it safe.

## Rollout

Straight default-on for 100% (no staging) — correctness and per-tier behavior
are already accepted. The retained column is the escape hatch if a problem
surfaces.

## Out-of-scope follow-up (next spec)

Audio per-title identification: consume the `/v1/identify` response (which now
exposes `temporal_coherence` alongside `hash_overlap_pct`,
`rarity_weighted_score`, `combined_score`, and `tier`) as an identification
signal in the analyst/coordinator. Separate brainstorm → spec → plan cycle.

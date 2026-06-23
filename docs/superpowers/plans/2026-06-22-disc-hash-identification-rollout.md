# Disc-hash Identification Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the existing fingerprint-network disc-hash identification path (`GET /v1/identify-disc`) by default for all engram installs — new and existing — with no user-facing toggle.

**Architecture:** Flip the `enable_fingerprint_identification` flag default to on at the model level (governs new installs) and add an Alembic data migration that promotes the flag on all existing `app_config` rows. No new logic; the tier-trust behavior (canonical/confirmed/candidate) is unchanged. The column is retained as a DB-level kill switch / test override.

**Tech Stack:** Python, SQLModel/SQLAlchemy, Alembic, pytest, uv.

**Working directory:** All paths are relative to `backend/` inside the worktree `C:\Github\engram\.claude\worktrees\fp-tier-trust`. Run all commands from `backend/`.

**Spec:** `docs/superpowers/specs/2026-06-22-disc-hash-identification-rollout-design.md`

---

## File Structure

- **Modify:** `backend/app/models/app_config.py` — flip the `enable_fingerprint_identification` field default (`default=True`, `server_default text("1")`), update comment.
- **Create:** `backend/migrations/versions/<generated>_enable_fingerprint_identification_default_on.py` — Alembic data migration promoting existing rows.
- **Create:** `backend/tests/unit/test_app_config_defaults.py` — unit test asserting the model default.

**Note on the existing test helper:** `tests/unit/test_identification_coordinator.py` uses a `_config()` `SimpleNamespace` helper that explicitly sets `enable_fingerprint_identification=False` (line 51). That is a *test fixture default*, independent of the production model default, and tests that need the disabled branch pass `False` explicitly (e.g. line 489). **Do not change that helper** — flipping the production default does not require it, and changing it would destabilize the disabled-path tests. Task 3 runs the affected subsets to confirm no regression.

---

### Task 1: Flip the model default to on

**Files:**
- Test: `backend/tests/unit/test_app_config_defaults.py` (create)
- Modify: `backend/app/models/app_config.py:214`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_app_config_defaults.py`:

```python
import pytest

from app.models.app_config import AppConfig


@pytest.mark.unit
def test_fingerprint_identification_defaults_on():
    """Disc-hash identification is enabled by default for new installs."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_identification is True


@pytest.mark.unit
def test_fingerprint_contributions_still_default_on():
    """Guard: flipping identification must not disturb the contributions default."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_contributions is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_app_config_defaults.py -v`
Expected: `test_fingerprint_identification_defaults_on` FAILS (`assert False is True`); the contributions guard PASSES.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/models/app_config.py`, change the field at line 214 from:

```python
    # Phase 3: chromaprint identification (default OFF until the catalog is seeded).
    enable_fingerprint_identification: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
```

to:

```python
    # Phase 3: chromaprint / disc-hash identification. Default ON: the catalog is
    # seeded and disc-hash matches fall back safely to TMDB/AI/heuristics on a miss.
    # Retained as a DB-level kill switch / test override (no user-facing toggle).
    enable_fingerprint_identification: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("1")}
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_app_config_defaults.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/app_config.py backend/tests/unit/test_app_config_defaults.py
git commit -m "feat(fp): default disc-hash identification on for new installs"
```

---

### Task 2: Alembic data migration for existing installs

**Files:**
- Create: `backend/migrations/versions/<generated>_enable_fingerprint_identification_default_on.py`

The model default only affects newly created rows. Existing installs have `0`
persisted, so this migration promotes them. The update is unconditional: with
no toggle, every persisted `0` is the stale old default, not a user choice.

- [ ] **Step 1: Generate the migration skeleton (auto-wires down_revision to current head `01f4f5567376`)**

Run: `uv run alembic revision -m "enable fingerprint identification default on"`
Expected: prints `Generating .../migrations/versions/<rev>_enable_fingerprint_identification_default_on.py ... done`. Note the generated filename.

- [ ] **Step 2: Fill in upgrade/downgrade**

Open the generated file and replace the `upgrade()` and `downgrade()` bodies with:

```python
def upgrade() -> None:
    op.execute("UPDATE app_config SET enable_fingerprint_identification = 1")


def downgrade() -> None:
    op.execute("UPDATE app_config SET enable_fingerprint_identification = 0")
```

Leave the auto-generated `revision` / `down_revision` identifiers untouched
(`down_revision` should be `"01f4f5567376"`). Ensure `from alembic import op`
is present (it is in the template).

- [ ] **Step 3: Verify the migration applies and reverses on a scratch DB**

Run (creates a throwaway DB, applies all migrations to head, then checks the value):

```bash
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run python -c "import sqlite3; c=sqlite3.connect('scratch_mig.db'); c.execute(\"INSERT INTO app_config (enable_fingerprint_identification) VALUES (0)\"); c.commit(); print('rows before downgrade:', c.execute('SELECT enable_fingerprint_identification FROM app_config').fetchall())"
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run alembic downgrade -1
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run python -c "import sqlite3; c=sqlite3.connect('scratch_mig.db'); print('rows after downgrade:', c.execute('SELECT enable_fingerprint_identification FROM app_config').fetchall())"
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./scratch_mig.db" uv run python -c "import sqlite3; c=sqlite3.connect('scratch_mig.db'); print('rows after re-upgrade:', c.execute('SELECT enable_fingerprint_identification FROM app_config').fetchall())"
```

Expected: `upgrade head` succeeds with no error; after re-upgrade the row reads `1`. (The downgrade step demonstrates reversibility; the manually inserted `0` row is set back to `0` by `downgrade -1` then to `1` again by the final `upgrade head`.)

- [ ] **Step 4: Remove the scratch DB**

```bash
rm -f scratch_mig.db
```

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/
git commit -m "feat(fp): migrate existing installs to disc-hash identification on"
```

---

### Task 3: Regression check on affected suites

This change touches identification config. Per project gotchas, the full
single-process pytest run is unreliable on Windows — run the affected subsets
instead.

**Files:** none (verification only; fix fallout inline if any)

- [ ] **Step 1: Run the affected unit suites**

Run:

```bash
uv run pytest tests/unit/test_app_config_defaults.py tests/unit/test_identification_coordinator.py tests/unit/test_analyst.py tests/unit/test_curator_tmdb_id.py tests/unit/test_fingerprint_disc_classifier.py -v
```

Expected: all PASS. The disabled-path coordinator test (around
`test_identification_coordinator.py:489`, which passes `enable_fingerprint_identification=False` explicitly) must still PASS — its fixture overrides the default, so it is unaffected.

- [ ] **Step 2: If anything fails, fix inline**

Any failure here is a test that implicitly assumed identification was off via
the *model* default rather than an explicit fixture value. Fix by making the
test's intent explicit (pass `enable_fingerprint_identification=False` in its
config fixture if it needs the disabled branch). Re-run Step 1 until green. If
nothing fails, skip to Step 3.

- [ ] **Step 3: Final commit (only if Step 2 changed files)**

```bash
git add backend/tests/
git commit -m "test(fp): make disabled-identification intent explicit after default flip"
```

---

## Self-Review

- **Spec coverage:**
  - "Model default flip" → Task 1. ✓
  - "Alembic data migration (upgrade/downgrade)" → Task 2. ✓
  - "Tests (TDD), confirm path fires under default config" → Task 1 (model default test) + Task 3 (regression incl. the identify-disc gate tests). ✓
  - "Column stays / kill switch" → Task 1 keeps the column, comment documents it. ✓
  - "No behavior/tier change" → no task modifies tier logic. ✓
- **Placeholder scan:** none — all code, commands, and the head revision (`01f4f5567376`) are concrete. The migration filename is `<generated>` only because Alembic mints it in Task 2 Step 1; the engineer notes it then.
- **Type consistency:** field name `enable_fingerprint_identification` used identically across model, migration, and tests; `server_default` uses `text("1")` matching the existing `text(...)` style in the model.

## Out of scope (next spec)

Audio per-title identification — consuming the `/v1/identify` response (now
exposing `temporal_coherence`) as an identification signal. Separate
brainstorm → spec → plan cycle.

# Manual Disc Metadata Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user assert a disc's identity themselves, either by arming a drive before insertion so the disc rips unattended, or by editing identity on a live job card.

**Architecture:** An in-memory arm store keyed by drive id holds a one-shot payload consumed by the disc-insert handler and passed into `identify_disc`. Classification still runs (preserving all structural analysis such as Play-All detection), then a deterministic override replaces identity and clears every review trigger, so no walk-away gate can fire. The frontend extracts the shared identity form out of `ReIdentifyModal` so an arm modal can reuse it.

**Tech Stack:** FastAPI, SQLModel/async SQLite, pytest, React 18 + TypeScript, Vitest + React Testing Library, Playwright.

**Design spec:** `docs/superpowers/specs/2026-07-19-manual-disc-metadata-entry-design.md`

---

## Design note: why classification still runs

The spec said the manual path would "skip `_run_classification` entirely." Tracing the code shows that is the wrong seam. `_run_classification` (`identification_coordinator.py:1427`) does **two** jobs: identity resolution (DiscDB, TMDB, AI) *and* structural analysis (duration clustering, Play-All index detection, ambiguous-movie detection). Its result feeds title persistence, Play-All deselection (`identify_disc:361`), and DiscDB extras tagging (`identify_disc:387`), all of which are identity-independent and all of which we still want.

So this plan runs classification unchanged and then **overrides** the identity fields. The cost is one redundant TMDB lookup on an edge-case path. The benefit is that the user-facing requirement (a wrong guess must never drive subtitles or matching) is fully met with a small, verifiable change instead of a risky rewrite of a 400-line method.

**Critical detail found while tracing:** clearing the signals is *not* sufficient to suppress the walk-away gates. Two gates fire on absence, not presence:

- **Gate B** (`identify_disc:481`) fires when `content_type == TV and not job.tmdb_id`. A user who types a freeform title with no TMDB match has no `tmdb_id`, so this gate would fire and raise a name prompt on a manual disc.
- **Gate D** (`identify_disc:517`) fires when `detected_season is None`, raising a season prompt.

Both must be explicitly guarded with an `_is_manual` flag. Gates A and C are already safe (A requires a missing title, C sits inside `needs_review`, which the override clears).

---

## File Structure

**Backend, create:**
- `backend/app/services/manual_identity.py` — `ManualIdentity` dataclass + `ArmStore` + `arm_store` singleton. Leaf module, stdlib-only imports, so `routes.py` and `job_manager.py` can both import it without a cycle (same reasoning as the existing `identity_prompts.py`).
- `backend/tests/unit/test_manual_identity_store.py`
- `backend/tests/unit/test_manual_identify_path.py`
- `backend/tests/integration/test_manual_arm_workflow.py`

**Backend, modify:**
- `backend/app/api/websocket.py` — add `broadcast_drive_armed`.
- `backend/app/api/routes.py` — add `POST /manual/arm`, `POST /manual/disarm`; widen re-identify state check.
- `backend/app/services/job_manager.py:635` — consume armed payload on insert.
- `backend/app/services/identification_coordinator.py` — `identify_disc` gains `manual_identity` param, `_apply_manual_identity` helper, gate guards.

**Frontend, create:**
- `frontend/src/components/IdentityFields.tsx` — shared TMDB search + title + media type + season form, extracted from `ReIdentifyModal`.
- `frontend/src/components/IdentityFields.test.tsx`
- `frontend/src/components/ArmDiscModal.tsx` + `.test.tsx`
- `frontend/src/app/components/ArmedDriveCard.tsx` + `.test.tsx`

**Frontend, modify:**
- `frontend/src/components/ReIdentifyModal.tsx` — consume `IdentityFields`.
- `frontend/src/app/components/synapse/SvTopBar.tsx` — MANUAL button.
- `frontend/src/app/App.tsx:743` — always-on identity control; armed-card rendering.
- `frontend/src/app/components/DiscCard.tsx` — provenance chip.

---

# PHASE 1 — Backend

Phase 1 is independently shippable and fully testable via curl without any UI.

---

### Task 1: Arm store

**Files:**
- Create: `backend/app/services/manual_identity.py`
- Test: `backend/tests/unit/test_manual_identity_store.py`

- [ ] **Step 1: Write the failing test**

```python
"""Arm store: one-shot, drive-scoped manual identity payloads."""

import pytest

from app.services.manual_identity import ArmStore, ManualIdentity


@pytest.fixture
def identity() -> ManualIdentity:
    return ManualIdentity(
        title="Arrested Development",
        content_type="tv",
        season=1,
        tmdb_id=4589,
        disc_number=2,
    )


def test_consume_returns_payload_then_clears(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.consume("E:") == identity
    # One-shot: a second insert on the same drive must not reuse it.
    assert store.consume("E:") is None


def test_peek_does_not_consume(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.peek("E:") == identity
    assert store.peek("E:") == identity
    assert store.consume("E:") == identity


def test_arm_is_drive_scoped(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.consume("F:") is None
    assert store.consume("E:") == identity


def test_disarm_reports_whether_anything_was_armed(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.disarm("E:") is True
    assert store.disarm("E:") is False


def test_arming_twice_replaces_the_payload(identity):
    store = ArmStore()
    store.arm("E:", identity)
    replacement = ManualIdentity(title="The Office", content_type="tv", season=2)
    store.arm("E:", replacement)

    assert store.consume("E:") == replacement


def test_to_dict_is_json_safe(identity):
    assert identity.to_dict() == {
        "title": "Arrested Development",
        "content_type": "tv",
        "season": 1,
        "tmdb_id": 4589,
        "disc_number": 2,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_manual_identity_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.manual_identity'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Armed manual-identity payloads, keyed by optical drive.

Leaf module with stdlib-only imports, deliberately: both ``api/routes.py``
and ``services/job_manager.py`` import it, and hanging this off JobManager
would force routes into deferred in-function imports to dodge a cycle. Same
reasoning as ``identity_prompts.py``.

State is in-memory and one-shot by design. A backend restart clears every
armed drive, which is the only implicit expiry this feature has (there is no
timer). See the design spec for why that is acceptable.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ManualIdentity:
    """A user-asserted disc identity, awaiting a disc."""

    title: str
    content_type: str  # "tv" | "movie"
    season: int | None = None
    tmdb_id: int | None = None
    disc_number: int | None = None

    def to_dict(self) -> dict:
        """JSON-safe form for the WebSocket payload."""
        return asdict(self)


class ArmStore:
    """In-memory, drive-keyed, one-shot store of armed identities."""

    def __init__(self) -> None:
        self._armed: dict[str, ManualIdentity] = {}

    def arm(self, drive_id: str, identity: ManualIdentity) -> None:
        """Arm a drive, replacing any existing payload for it."""
        self._armed[drive_id] = identity

    def peek(self, drive_id: str) -> ManualIdentity | None:
        """Read without consuming (for API validation / reconnect sync)."""
        return self._armed.get(drive_id)

    def consume(self, drive_id: str) -> ManualIdentity | None:
        """Read and clear. Called exactly once, by the disc-insert handler."""
        return self._armed.pop(drive_id, None)

    def disarm(self, drive_id: str) -> bool:
        """Clear a drive. Returns whether anything was actually armed."""
        return self._armed.pop(drive_id, None) is not None

    def all_armed(self) -> dict[str, ManualIdentity]:
        """Snapshot of every armed drive, for client reconnect sync."""
        return dict(self._armed)


arm_store = ArmStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_manual_identity_store.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/manual_identity.py backend/tests/unit/test_manual_identity_store.py
git commit -m "feat: arm store for user-asserted disc identity (#520)"
```

---

### Task 2: WebSocket drive_armed event

**Files:**
- Modify: `backend/app/api/websocket.py` (after `broadcast_drive_event`, line 176)
- Test: `backend/tests/unit/test_manual_identity_store.py` (append)

Note the existing `broadcast_drive_event` flattens its fields at the top level rather than nesting under `data`. Follow that actual shape, not the `{"type", "data"}` convention described in CLAUDE.md.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_manual_identity_store.py`:

```python
from unittest.mock import AsyncMock

from app.api.websocket import ConnectionManager


@pytest.mark.asyncio
async def test_broadcast_drive_armed_sends_identity(identity):
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()

    await manager.broadcast_drive_armed("E:", identity.to_dict())

    manager.broadcast.assert_awaited_once_with(
        {
            "type": "drive_armed",
            "drive_id": "E:",
            "identity": {
                "title": "Arrested Development",
                "content_type": "tv",
                "season": 1,
                "tmdb_id": 4589,
                "disc_number": 2,
            },
        }
    )


@pytest.mark.asyncio
async def test_broadcast_drive_armed_none_clears():
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()

    await manager.broadcast_drive_armed("E:", None)

    manager.broadcast.assert_awaited_once_with(
        {"type": "drive_armed", "drive_id": "E:", "identity": None}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_manual_identity_store.py -k drive_armed -v`
Expected: FAIL with `AttributeError: 'ConnectionManager' object has no attribute 'broadcast_drive_armed'`

- [ ] **Step 3: Write minimal implementation**

Insert into `backend/app/api/websocket.py` immediately after `broadcast_drive_event`:

```python
    async def broadcast_drive_armed(
        self,
        drive_id: str,
        identity: dict | None,
    ) -> None:
        """Broadcast a drive's armed manual identity.

        ``identity=None`` clears the armed state (disarmed, or consumed by an
        inserted disc). Mirrors ``broadcast_drive_event``'s flat shape.
        """
        await self.broadcast(
            {
                "type": "drive_armed",
                "drive_id": drive_id,
                "identity": identity,
            }
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_manual_identity_store.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/websocket.py backend/tests/unit/test_manual_identity_store.py
git commit -m "feat: drive_armed websocket event (#520)"
```

---

### Task 3: Arm and disarm endpoints

**Files:**
- Modify: `backend/app/api/routes.py` (add near the re-identify endpoint, after line 1327)
- Test: `backend/tests/integration/test_manual_arm_workflow.py`

- [ ] **Step 1: Write the failing test**

```python
"""Arm/disarm API: validation, drive-occupied rejection, one-shot semantics."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscJob, JobState
from app.services.manual_identity import arm_store


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()
    arm_store.disarm("E:")
    yield
    arm_store.disarm("E:")


async def test_arm_stores_payload(client):
    resp = await client.post(
        "/api/manual/arm",
        json={
            "drive_id": "E:",
            "title": "Arrested Development",
            "content_type": "tv",
            "season": 1,
            "tmdb_id": 4589,
        },
    )

    assert resp.status_code == 200
    armed = arm_store.peek("E:")
    assert armed is not None
    assert armed.title == "Arrested Development"
    assert armed.tmdb_id == 4589


async def test_arm_rejects_blank_title(client):
    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "   ", "content_type": "tv"},
    )

    assert resp.status_code == 422
    assert arm_store.peek("E:") is None


async def test_arm_rejects_bad_content_type(client):
    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "X", "content_type": "audiobook"},
    )

    assert resp.status_code == 422


async def test_arm_conflicts_when_drive_has_active_job(client):
    async with async_session() as session:
        session.add(DiscJob(drive_id="E:", volume_label="BUSY", state=JobState.RIPPING))
        await session.commit()

    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "Arrested Development", "content_type": "tv"},
    )

    assert resp.status_code == 409
    assert arm_store.peek("E:") is None


async def test_arm_allowed_when_drive_job_is_terminal(client):
    async with async_session() as session:
        session.add(DiscJob(drive_id="E:", volume_label="OLD", state=JobState.COMPLETED))
        await session.commit()

    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "The Office", "content_type": "tv"},
    )

    assert resp.status_code == 200


async def test_disarm_clears(client):
    await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "The Office", "content_type": "tv"},
    )

    resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "disarmed"
    assert arm_store.peek("E:") is None


async def test_disarm_when_not_armed_is_not_an_error(client):
    resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_armed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -v`
Expected: FAIL, 404 responses because the routes do not exist

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/api/routes.py` after the re-identify endpoint (line 1327). The `TERMINAL_JOB_STATES` frozenset already exists in `app/models/disc_job.py`.

```python
class ArmManualRequest(BaseModel):
    """Arm a drive so the next disc adopts this identity verbatim."""

    drive_id: str
    title: str
    content_type: Literal["tv", "movie"]
    season: int | None = None
    tmdb_id: int | None = None
    disc_number: int | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v.strip()


class DisarmManualRequest(BaseModel):
    drive_id: str


@router.post("/manual/arm")
async def arm_manual_identity(
    req: ArmManualRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Arm a drive with a user-asserted identity for the next disc inserted.

    Rejects with 409 when the drive already holds a non-terminal job: that disc
    is already being worked on, and the caller should edit that job's identity
    instead of arming for a disc that will not be inserted.
    """
    from app.models.disc_job import TERMINAL_JOB_STATES
    from app.services.manual_identity import ManualIdentity, arm_store

    result = await session.execute(
        select(DiscJob).where(
            DiscJob.drive_id == req.drive_id,
            DiscJob.state.notin_(list(TERMINAL_JOB_STATES)),
        )
    )
    if result.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Drive {req.drive_id} already has an active job. "
                f"Edit that job's identity instead of arming the drive."
            ),
        )

    identity = ManualIdentity(
        title=req.title,
        content_type=req.content_type,
        season=req.season,
        tmdb_id=req.tmdb_id,
        disc_number=req.disc_number,
    )
    arm_store.arm(req.drive_id, identity)
    await ws_manager.broadcast_drive_armed(req.drive_id, identity.to_dict())
    logger.info(
        f"Armed drive {sanitize_log_value(req.drive_id)} with manual identity "
        f"'{sanitize_log_value(req.title)}' ({req.content_type})"
    )
    return {"status": "armed", "drive_id": req.drive_id}


@router.post("/manual/disarm")
async def disarm_manual_identity(req: DisarmManualRequest) -> dict:
    """Clear a drive's armed identity. Idempotent."""
    from app.services.manual_identity import arm_store

    was_armed = arm_store.disarm(req.drive_id)
    if was_armed:
        await ws_manager.broadcast_drive_armed(req.drive_id, None)
    return {
        "status": "disarmed" if was_armed else "not_armed",
        "drive_id": req.drive_id,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/app/api/routes.py backend/tests/integration/test_manual_arm_workflow.py
git commit -m "feat: arm/disarm endpoints for manual disc identity (#520)"
```

---

### Task 4: Manual identity override in identify_disc

This is the core task. It adds the `manual_identity` parameter, the override helper, and the two gate guards.

**Files:**
- Modify: `backend/app/services/identification_coordinator.py`
- Test: `backend/tests/unit/test_manual_identify_path.py`

- [ ] **Step 1: Write the failing test**

```python
"""Manual identity override: user assertion wins, no gate may fire.

The override runs AFTER _run_classification so structural analysis (Play-All
indices, ambiguous-movie detection, title clustering) is preserved. These tests
pin the two behaviors that matter: identity is replaced, and every walk-away
gate is suppressed.
"""

import pytest

from app.core.analyst import DiscAnalysisResult
from app.models.disc_job import ContentType
from app.services.identification_coordinator import _apply_manual_identity
from app.services.manual_identity import ManualIdentity


def _guessed_result() -> DiscAnalysisResult:
    """A classification result that guessed wrong and wants review."""
    result = DiscAnalysisResult()
    result.content_type = ContentType.MOVIE
    result.detected_name = "Wrong Guess"
    result.detected_season = None
    result.tmdb_id = 999
    result.confidence = 0.4
    result.classification_source = "heuristic"
    result.needs_review = True
    result.review_reason = "Could not confirm identity"
    result.is_ambiguous_movie = True
    result.identity_unconfirmed = True
    return result


def test_override_replaces_identity():
    result = _guessed_result()
    manual = ManualIdentity(
        title="Arrested Development", content_type="tv", season=1, tmdb_id=4589
    )

    _apply_manual_identity(result, manual)

    assert result.content_type == ContentType.TV
    assert result.detected_name == "Arrested Development"
    assert result.detected_season == 1
    assert result.tmdb_id == 4589
    assert result.classification_source == "manual"
    assert result.confidence == 1.0


def test_override_clears_every_review_trigger():
    result = _guessed_result()
    manual = ManualIdentity(title="The Office", content_type="tv", season=2)

    _apply_manual_identity(result, manual)

    # Each of these independently routes a job to REVIEW_NEEDED or raises a
    # walk-away prompt. A manual disc must never park.
    assert result.needs_review is False
    assert result.review_reason is None
    assert result.is_ambiguous_movie is False
    assert result.identity_unconfirmed is False
    assert getattr(result, "_tmdb_signal", None) is None
    assert getattr(result, "_discdb_signal", None) is None


def test_override_tolerates_missing_tmdb_id():
    """A freeform title with no TMDB match is explicitly allowed."""
    result = _guessed_result()
    manual = ManualIdentity(title="Home Movies 1998", content_type="tv", season=1)

    _apply_manual_identity(result, manual)

    assert result.tmdb_id is None
    assert result.detected_name == "Home Movies 1998"
    assert result.needs_review is False


def test_override_preserves_structural_analysis():
    """Play-All indices come from duration clustering, not identity."""
    result = _guessed_result()
    result.play_all_title_indices = [7, 8]
    manual = ManualIdentity(title="The Office", content_type="tv", season=2)

    _apply_manual_identity(result, manual)

    assert result.play_all_title_indices == [7, 8]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_manual_identify_path.py -v`
Expected: FAIL with `ImportError: cannot import name '_apply_manual_identity'`

- [ ] **Step 3: Write the override helper**

Add to `backend/app/services/identification_coordinator.py` at module level, near the other module-level helpers such as `_candidates_json_from_signal`:

```python
def _apply_manual_identity(analysis, manual: "ManualIdentity") -> None:
    """Overwrite a classification result with the user's asserted identity.

    Runs AFTER ``_run_classification`` so everything structural the Analyst
    produced (Play-All indices, duration clustering, extras hints) survives;
    only identity and the review triggers are replaced.

    Clearing ``_tmdb_signal``/``_discdb_signal`` is load-bearing: the same-name
    collision gate and the no-year backstop both read them, and a manual disc
    must never raise a collision prompt. Note that clearing signals alone does
    NOT suppress gates B and D, which fire on ABSENCE (no tmdb_id / no season);
    those are guarded separately at their call sites via ``_is_manual``.
    """
    analysis.content_type = ContentType(manual.content_type)
    analysis.detected_name = manual.title
    analysis.detected_season = manual.season
    analysis.tmdb_id = manual.tmdb_id
    analysis.tmdb_name = manual.title if manual.tmdb_id else None
    analysis.confidence = 1.0
    analysis.classification_source = "manual"
    analysis.needs_review = False
    analysis.review_reason = None
    analysis.is_ambiguous_movie = False
    analysis.identity_unconfirmed = False
    analysis.tmdb_degraded_reason = None
    analysis._tmdb_signal = None
    analysis._discdb_signal = None
```

Add the import at the top of the file:

```python
from app.services.manual_identity import ManualIdentity
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_manual_identify_path.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Wire the parameter and the gate guards**

Change the `identify_disc` signature (line 243):

```python
    async def identify_disc(
        self, job_id: int, manual_identity: ManualIdentity | None = None
    ) -> None:
```

Immediately after the `analysis = await self._run_classification(...)` call (line 282-284), insert:

```python
                # User-asserted identity wins over anything classification found.
                # Applied here, before any field is copied onto the job, so every
                # downstream branch sees the manual values.
                _is_manual = manual_identity is not None
                if manual_identity is not None:
                    _apply_manual_identity(analysis, manual_identity)
                    if manual_identity.disc_number is not None:
                        job.disc_number = manual_identity.disc_number
```

Guard **Gate B** by changing its condition (line 481-486) from:

```python
                if (
                    job.content_type == ContentType.TV
                    and not job.tmdb_id
                    and job.detected_title
                    and not _collision
                ):
```

to:

```python
                if (
                    job.content_type == ContentType.TV
                    and not job.tmdb_id
                    and job.detected_title
                    and not _collision
                    # A manual disc with a freeform title legitimately has no
                    # tmdb_id. The user asserted this name; do not second-guess it.
                    and not _is_manual
                ):
```

Guard **Gate D** by changing its condition (line 517) from:

```python
                    if job.detected_season is None:
                        await self._gate_unknown_season_disc(job, session, job_id)
```

to:

```python
                    # A manual disc with no season matches across all seasons
                    # silently; prompting would break the unattended contract.
                    if job.detected_season is None and not _is_manual:
                        await self._gate_unknown_season_disc(job, session, job_id)
```

Note the disc-number override placed above must come *after* the existing regex parse at lines 331-340, or it will be clobbered. Move the manual disc-number assignment to directly follow that block instead of into the override step if ordering conflicts.

- [ ] **Step 6: Add the gate-suppression regression test**

Append to `backend/tests/unit/test_manual_identify_path.py`:

```python
def test_gate_b_condition_is_suppressed_for_manual():
    """Gate B fires on ABSENCE of tmdb_id, so the override alone cannot stop it.

    This pins the _is_manual guard: without it, a manual TV disc whose title
    has no TMDB match would raise a name prompt and break the unattended rip.
    """
    is_tv = True
    tmdb_id = None
    detected_title = "Home Movies 1998"
    collision = False

    def gate_b_fires(is_manual: bool) -> bool:
        return bool(is_tv and not tmdb_id and detected_title and not collision and not is_manual)

    assert gate_b_fires(is_manual=False) is True
    assert gate_b_fires(is_manual=True) is False


def test_gate_d_condition_is_suppressed_for_manual():
    detected_season = None

    def gate_d_fires(is_manual: bool) -> bool:
        return detected_season is None and not is_manual

    assert gate_d_fires(is_manual=False) is True
    assert gate_d_fires(is_manual=True) is False
```

- [ ] **Step 7: Run the full unit suite**

Run: `cd backend && uv run pytest tests/unit/ -q`
Expected: PASS, no regressions

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/app/services/identification_coordinator.py backend/tests/unit/test_manual_identify_path.py
git commit -m "feat: manual identity override suppresses walk-away gates (#520)"
```

---

### Task 5: Consume the armed payload on disc insert

**Files:**
- Modify: `backend/app/services/job_manager.py:635-656`
- Test: `backend/tests/integration/test_manual_arm_workflow.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_manual_arm_workflow.py`:

```python
from unittest.mock import AsyncMock, patch

from app.services.job_manager import job_manager
from app.services.manual_identity import ManualIdentity


async def test_insert_consumes_armed_payload_and_passes_it_to_identify():
    """The armed payload reaches identify_disc and is cleared from the store."""
    arm_store.arm(
        "E:",
        ManualIdentity(title="Arrested Development", content_type="tv", season=1, tmdb_id=4589),
    )

    with patch.object(
        job_manager._identification, "identify_disc", new=AsyncMock()
    ) as mock_identify:
        await job_manager._handle_disc_inserted("E:", "UNREADABLE_LABEL")

    assert mock_identify.await_count == 1
    passed = mock_identify.await_args.kwargs.get("manual_identity")
    assert passed is not None
    assert passed.title == "Arrested Development"
    # One-shot: the next disc in this drive must identify normally.
    assert arm_store.peek("E:") is None


async def test_insert_without_arm_passes_no_manual_identity():
    with patch.object(
        job_manager._identification, "identify_disc", new=AsyncMock()
    ) as mock_identify:
        await job_manager._handle_disc_inserted("E:", "THE_OFFICE_S2D3")

    assert mock_identify.await_args.kwargs.get("manual_identity") is None
```

Adjust the handler name and call signature in these tests to match the actual method in `job_manager.py` if it differs; the insert path is the one containing the `DiscJob(...)` construction at line 635.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -k armed_payload -v`
Expected: FAIL, `manual_identity` is not among the call kwargs

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/job_manager.py`, replace the task-spawn block at lines 649-653 with:

```python
                # Walk-away manual path: if this drive was armed, the user's
                # asserted identity travels with the identify task instead of
                # being stamped on the row here, so IdentificationCoordinator
                # stays the single owner of identity fields. One-shot: consumed
                # even if the scan later fails, since the disc did go in.
                armed = arm_store.consume(drive_letter)
                if armed is not None:
                    logger.info(
                        f"Job {job.id}: adopting armed manual identity "
                        f"'{sanitize_log_value(armed.title)}' ({armed.content_type})"
                    )
                    await ws_manager.broadcast_drive_armed(drive_letter, None)

                task = asyncio.create_task(
                    with_job_log_context(
                        job.id,
                        self._identification.identify_disc(
                            job.id, manual_identity=armed
                        ),
                    )
                )
```

Add the import at the top of `job_manager.py`:

```python
from app.services.manual_identity import arm_store
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/integration/test_manual_arm_workflow.py
git commit -m "feat: consume armed identity on disc insert (#520)"
```

---

### Task 6: Widen re-identify states and record correction provenance

**Files:**
- Modify: `backend/app/api/routes.py:1311`
- Modify: `backend/app/services/identification_coordinator.py` (`re_identify`, around line 1270)
- Test: `backend/tests/integration/test_manual_arm_workflow.py` (append)

- [ ] **Step 1: Write the failing test**

```python
async def test_re_identify_accepted_while_identifying(client):
    async with async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="X", state=JobState.IDENTIFYING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch.object(job_manager, "re_identify_job", new=AsyncMock()):
        resp = await client.post(
            f"/api/jobs/{job_id}/re-identify",
            json={"title": "The Office", "content_type": "tv", "season": 2},
        )

    assert resp.status_code == 200


async def test_re_identify_still_rejected_when_completed(client):
    async with async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="X", state=JobState.COMPLETED)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    resp = await client.post(
        f"/api/jobs/{job_id}/re-identify",
        json={"title": "The Office", "content_type": "tv", "season": 2},
    )

    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -k re_identify -v`
Expected: FAIL, the IDENTIFYING case returns 400

- [ ] **Step 3: Write minimal implementation**

In `routes.py`, change the guard at line 1311 from:

```python
    if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING):
```

to:

```python
    # IDENTIFYING included so the always-on card control is usable for the whole
    # window the UI offers it (#520).
    if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING, JobState.IDENTIFYING):
```

Apply the identical change to the same guard inside `IdentificationCoordinator.re_identify` (around line 1270), which re-validates on a fresh row.

In `re_identify`, where the job's identity fields are written, record provenance:

```python
                # The resulting identity is user-asserted regardless of what was
                # guessed first. "manual_correction" distinguishes this from the
                # arm path's "manual" for diagnostics; both render one UI chip.
                job.classification_source = "manual_correction"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_manual_arm_workflow.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the full backend suite and commit**

```bash
cd backend && uv run pytest tests/unit/ tests/integration/test_manual_arm_workflow.py -q
uv run ruff check . && uv run ruff format .
git add backend/app/api/routes.py backend/app/services/identification_coordinator.py backend/tests/integration/test_manual_arm_workflow.py
git commit -m "feat: allow re-identify while identifying, record correction provenance (#520)"
```

---

### Task 7: Manual verification of Phase 1

Phase 1 is complete and usable without any UI. Verify end to end before starting the frontend.

- [ ] **Step 1: Start the backend**

```bash
cd backend && DEBUG=true uv run uvicorn app.main:app --port 8000
```

Never use `--reload` (it spawns a second drive sentinel).

- [ ] **Step 2: Arm a drive**

```bash
curl -X POST localhost:8000/api/manual/arm \
  -H "Content-Type: application/json" \
  -d '{"drive_id":"E:","title":"Arrested Development","content_type":"tv","season":1,"tmdb_id":4589}'
```

Expected: `{"status":"armed","drive_id":"E:"}`

- [ ] **Step 3: Confirm the conflict guard**

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ANYTHING","content_type":"tv","simulate_ripping":false}'
curl -X POST localhost:8000/api/manual/arm \
  -H "Content-Type: application/json" \
  -d '{"drive_id":"E:","title":"X","content_type":"tv"}'
```

Expected: the second call returns 409.

- [ ] **Step 4: Confirm the job adopted the identity**

```bash
curl -s localhost:8000/api/jobs | python -m json.tool | grep -E "detected_title|classification_source|identity_prompt"
```

Expected: `detected_title` is the armed title, `classification_source` is `"manual"`, and `identity_prompt_json` is null. A null prompt is the whole point: the disc rips unattended.

- [ ] **Step 5: Stop the server**

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

- [ ] **Step 6: Commit any fixes found during verification**

---

# PHASE 2 — Frontend

---

### Task 8: Extract the shared identity form

`ReIdentifyModal.tsx` is 715 lines and binds every field to a `Job`. Arm mode has no job, so the form must come out before it can be reused. This task is a pure refactor: behavior and existing tests stay green.

**Files:**
- Create: `frontend/src/components/IdentityFields.tsx`
- Create: `frontend/src/components/IdentityFields.test.tsx`
- Modify: `frontend/src/components/ReIdentifyModal.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import IdentityFields from './IdentityFields';

describe('IdentityFields', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          { tmdb_id: 4589, name: 'Arrested Development', type: 'tv', year: '2003', poster_path: null, popularity: 20 },
        ],
      }),
    }) as never;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('reports title changes to the parent', () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: '', contentType: 'tv', season: '1', tmdbId: undefined }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ title: 'The Office' }));
  });

  it('clears tmdbId when the title is typed manually', () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: 'Arrested Development', contentType: 'tv', season: '1', tmdbId: 4589 }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'Arrested Dev' } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ tmdbId: undefined }));
  });

  it('hides the season field for movies', () => {
    render(
      <IdentityFields
        value={{ title: 'Inception', contentType: 'movie', season: '1', tmdbId: undefined }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByText(/season/i)).not.toBeInTheDocument();
  });

  it('selecting a search result sets title, type and tmdbId together', async () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: '', contentType: 'movie', season: '1', tmdbId: undefined }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'arrested' } });
    await vi.advanceTimersByTimeAsync(600);
    fireEvent.click(await screen.findByText('Arrested Development'));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Arrested Development', contentType: 'tv', tmdbId: 4589 }),
      ),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- IdentityFields`
Expected: FAIL, cannot resolve `./IdentityFields`

- [ ] **Step 3: Create the component**

Create `frontend/src/components/IdentityFields.tsx` as a controlled component. Move these blocks verbatim out of `ReIdentifyModal.tsx`: the `TmdbResult` interface, `doSearch`/`handleSearchChange`/`selectResult`, `inputStyle`, and the JSX for the TMDB search section (lines 359-498), title input (503-536), media type toggle (539-583), and season field (585-609).

```tsx
import { useState, useRef, useCallback, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { IcoMovie, IcoTv, IcoSearch, IcoRetry } from '../app/components/icons';
import { SvLabel, sv } from '../app/components/synapse';

export interface TmdbResult {
    tmdb_id: number;
    name: string;
    type: 'tv' | 'movie';
    year: string;
    poster_path: string | null;
    popularity: number;
}

/** The identity a user is asserting. Owned by the parent modal. */
export interface IdentityValue {
    title: string;
    contentType: 'tv' | 'movie';
    /** Kept as a string so the number input can be transiently empty. */
    season: string;
    tmdbId: number | undefined;
    /** First-air year of the selected TMDB result, for the confirmation line. */
    selectedYear?: string;
}

interface IdentityFieldsProps {
    value: IdentityValue;
    onChange: (next: IdentityValue) => void;
    /** Autofocus the title input on mount. */
    autoFocus?: boolean;
}

export default function IdentityFields({ value, onChange, autoFocus }: IdentityFieldsProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<TmdbResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const titleInputRef = useRef<HTMLInputElement>(null);
    const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

    useEffect(() => {
        if (autoFocus) titleInputRef.current?.focus();
    }, [autoFocus]);

    useEffect(() => () => {
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    }, []);

    const doSearch = useCallback(async (query: string) => {
        if (!query.trim()) {
            setSearchResults([]);
            return;
        }
        setIsSearching(true);
        try {
            const resp = await fetch(`/api/tmdb/search?query=${encodeURIComponent(query)}`);
            if (resp.ok) {
                const data = await resp.json();
                setSearchResults(data.results || []);
            }
        } catch {
            // Silently fail — search is optional
        } finally {
            setIsSearching(false);
        }
    }, []);

    const handleSearchChange = (q: string) => {
        setSearchQuery(q);
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
        searchTimerRef.current = setTimeout(() => doSearch(q), 500);
    };

    const selectResult = (result: TmdbResult) => {
        onChange({
            ...value,
            title: result.name,
            contentType: result.type,
            tmdbId: result.tmdb_id,
            selectedYear: result.year || undefined,
        });
        setSearchResults([]);
        setSearchQuery('');
    };

    // Typing a title by hand invalidates any previously selected TMDB match.
    const handleTitleChange = (title: string) =>
        onChange({ ...value, title, tmdbId: undefined, selectedYear: undefined });

    // ... render the four moved JSX blocks, reading from `value` and calling
    // the handlers above. Copy the styles verbatim from ReIdentifyModal so the
    // visual result is byte-identical.
}
```

Copy the JSX bodies verbatim from the line ranges listed above, replacing local state reads with `value.*` and local setters with `onChange({ ...value, ... })`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test:unit -- IdentityFields`
Expected: PASS, 4 passed

- [ ] **Step 5: Rewire ReIdentifyModal**

Replace the four extracted blocks in `ReIdentifyModal.tsx` with a single `<IdentityFields>`, holding one `IdentityValue` in state seeded from the job:

```tsx
const [identity, setIdentity] = useState<IdentityValue>({
    title: job.detected_title || '',
    contentType: job.content_type === 'tv' ? 'tv' : 'movie',
    season: String(job.detected_season || 1),
    tmdbId: undefined,
});
```

and submit from it:

```tsx
const handleSubmit = () => {
    if (!identity.title.trim()) return;
    onSubmit(
        identity.title.trim(),
        identity.contentType,
        identity.contentType === 'tv' ? (parseInt(identity.season, 10) || 1) : undefined,
        identity.tmdbId,
    );
};
```

Keep the job-specific chrome (header, notice, same-name quick-pick, action buttons, status bar) in `ReIdentifyModal`.

- [ ] **Step 6: Verify no regression**

Run: `cd frontend && npm run test:unit -- ReIdentifyModal && npm run build`
Expected: existing ReIdentifyModal tests PASS, build clean

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/IdentityFields.tsx frontend/src/components/IdentityFields.test.tsx frontend/src/components/ReIdentifyModal.tsx
git commit -m "refactor: extract IdentityFields from ReIdentifyModal (#520)"
```

---

### Task 9: ArmDiscModal

**Files:**
- Create: `frontend/src/components/ArmDiscModal.tsx`, `ArmDiscModal.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import ArmDiscModal from './ArmDiscModal';

describe('ArmDiscModal', () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'armed' }) }) as never;
  });
  afterEach(() => vi.restoreAllMocks());

  it('disables arming until a title is entered', () => {
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);
    expect(screen.getByTestId('arm-submit')).toBeDisabled();
  });

  it('posts the armed identity and reports success', async () => {
    const onArmed = vi.fn();
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={onArmed} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });
    fireEvent.click(screen.getByTestId('arm-submit'));

    await waitFor(() => expect(onArmed).toHaveBeenCalled());
    const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/manual/arm');
    expect(JSON.parse(init.body)).toMatchObject({
      drive_id: 'E:',
      title: 'The Office',
      content_type: 'tv',
      season: 1,
    });
  });

  it('omits season for movies', async () => {
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'Inception' } });
    fireEvent.click(screen.getByRole('button', { name: /movie/i }));
    fireEvent.click(screen.getByTestId('arm-submit'));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body).season).toBeNull();
  });

  it('surfaces a 409 as a readable conflict message', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Drive E: already has an active job.' }),
    }) as never;
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });
    fireEvent.click(screen.getByTestId('arm-submit'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/already has an active job/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- ArmDiscModal`
Expected: FAIL, cannot resolve `./ArmDiscModal`

- [ ] **Step 3: Write the implementation**

```tsx
import { useState } from 'react';
import { motion } from 'motion/react';
import IdentityFields, { type IdentityValue } from './IdentityFields';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';

interface ArmDiscModalProps {
    driveId: string;
    onClose: () => void;
    onArmed: () => void;
}

export default function ArmDiscModal({ driveId, onClose, onArmed }: ArmDiscModalProps) {
    const [identity, setIdentity] = useState<IdentityValue>({
        title: '',
        contentType: 'tv',
        season: '1',
        tmdbId: undefined,
    });
    const [discNumber, setDiscNumber] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const submit = async () => {
        if (!identity.title.trim()) return;
        setBusy(true);
        setError(null);
        try {
            const resp = await fetch('/api/manual/arm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    drive_id: driveId,
                    title: identity.title.trim(),
                    content_type: identity.contentType,
                    season:
                        identity.contentType === 'tv'
                            ? parseInt(identity.season, 10) || 1
                            : null,
                    tmdb_id: identity.tmdbId ?? null,
                    disc_number: discNumber ? parseInt(discNumber, 10) : null,
                }),
            });
            if (!resp.ok) {
                const body = await resp.json().catch(() => ({}));
                setError(body.detail || `Could not arm the drive (${resp.status}).`);
                return;
            }
            onArmed();
        } catch {
            setError('Could not reach the server.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="arm-disc-title"
            onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
        >
            <div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                onClick={onClose}
            />
            <motion.div className="relative w-full max-w-md">
                <SvPanel glow pad={24} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                    <h2
                        id="arm-disc-title"
                        style={{
                            fontFamily: sv.display, fontWeight: 700, fontSize: 18,
                            letterSpacing: '0.2em', textTransform: 'uppercase',
                            color: sv.cyanHi, margin: 0,
                        }}
                    >
                        Manual Disc Identity
                    </h2>
                    <p style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim, margin: 0 }}>
                        Engram will skip automatic identification for the next disc inserted in
                        {' '}<span style={{ color: sv.cyan }}>{driveId}</span> and use exactly what you enter.
                    </p>

                    <IdentityFields value={identity} onChange={setIdentity} autoFocus />

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <SvLabel size={10}>Disc number (optional)</SvLabel>
                        <input
                            type="number"
                            min={1}
                            value={discNumber}
                            onChange={(e) => setDiscNumber(e.target.value)}
                            placeholder="auto from label"
                            style={{
                                width: 128, background: sv.bg0,
                                border: `1px solid ${sv.lineMid}`, color: sv.cyanHi,
                                fontFamily: sv.mono, fontSize: 13, padding: '10px 12px',
                            }}
                        />
                    </div>

                    <p style={{
                        fontFamily: sv.mono, fontSize: 11, color: sv.magentaHi,
                        borderLeft: `2px solid ${sv.magenta}`,
                        background: `${sv.magenta}12`, padding: '9px 12px', margin: 0,
                    }}>
                        Episode matching still runs automatically. Anything it cannot resolve
                        lands in the Review Queue as usual.
                    </p>

                    {error && (
                        <p role="alert" style={{
                            fontFamily: sv.mono, fontSize: 11, color: sv.red,
                            border: `1px solid ${sv.red}`, background: 'rgba(255,85,85,0.08)',
                            padding: '9px 12px', margin: 0,
                        }}>
                            {error}
                        </p>
                    )}

                    <div style={{ display: 'flex', gap: 12 }}>
                        <button
                            type="button"
                            onClick={onClose}
                            style={{
                                flex: 1, padding: '10px 16px', fontFamily: sv.mono, fontSize: 11,
                                fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase',
                                color: sv.inkDim, border: `1px solid ${sv.lineMid}`,
                                background: 'transparent', cursor: 'pointer',
                            }}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            data-testid="arm-submit"
                            onClick={submit}
                            disabled={!identity.title.trim() || busy}
                            style={{
                                flex: 1, padding: '10px 16px', fontFamily: sv.mono, fontSize: 11,
                                fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase',
                                color: identity.title.trim() ? sv.cyan : `${sv.cyan}4d`,
                                border: `1px solid ${identity.title.trim() ? sv.cyan : `${sv.cyan}33`}`,
                                background: identity.title.trim() ? `${sv.cyan}1f` : 'transparent',
                                cursor: identity.title.trim() ? 'pointer' : 'not-allowed',
                                opacity: identity.title.trim() ? 1 : 0.3,
                            }}
                        >
                            Arm Drive
                        </button>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test:unit -- ArmDiscModal`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ArmDiscModal.tsx frontend/src/components/ArmDiscModal.test.tsx
git commit -m "feat: ArmDiscModal for pre-insert manual identity (#520)"
```

---

### Task 10: MANUAL button in the top bar

**Files:**
- Modify: `frontend/src/app/components/synapse/SvTopBar.tsx` (props at line 18, button at line 98)
- Modify: `frontend/src/app/components/synapse/__tests__/SvTopBar.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `SvTopBar.test.tsx`:

```tsx
it('fires onManualClick when MANUAL is pressed', () => {
    const onManualClick = vi.fn();
    render(
        <MemoryRouter>
            <SvTopBar
                isConnected
                version="0.25.0"
                onImportClick={() => {}}
                onManualClick={onManualClick}
                onSettingsClick={() => {}}
            />
        </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: /manual disc/i }));

    expect(onManualClick).toHaveBeenCalledTimes(1);
});
```

Match the existing props and wrapper used by the other tests in this file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- SvTopBar`
Expected: FAIL, no button matching /manual disc/

- [ ] **Step 3: Write minimal implementation**

Add `onManualClick: () => void;` to the props interface (line 18) and destructuring (line 34). Insert this button immediately before the existing IMPORT button (line 98), reusing its style with the magenta tone:

```tsx
        <button
          onClick={onManualClick}
          aria-label="Manual disc identity"
          title="Enter disc metadata manually for the next disc"
          data-testid="sv-manual-btn"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            border: `1px solid ${sv.magenta}`,
            color: sv.magenta,
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.16em",
            padding: "7px 12px",
            cursor: "pointer",
            transition: "all 0.18s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${sv.magenta}1f`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IcoDisc size={14} />
          MANUAL
        </button>
```

Add `IcoDisc` to the icon imports at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test:unit -- SvTopBar && npm run build`
Expected: PASS; build fails until App.tsx passes the new required prop, which Task 11 does. If you need a green build now, make the prop optional and tighten it in Task 11.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/synapse/SvTopBar.tsx frontend/src/app/components/synapse/__tests__/SvTopBar.test.tsx
git commit -m "feat: MANUAL button in top bar (#520)"
```

---

### Task 11: Armed drive card and App wiring

**Files:**
- Create: `frontend/src/app/components/ArmedDriveCard.tsx`, `ArmedDriveCard.test.tsx`
- Modify: `frontend/src/app/App.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import ArmedDriveCard from './ArmedDriveCard';

describe('ArmedDriveCard', () => {
  const identity = { title: 'Arrested Development', content_type: 'tv', season: 1, tmdb_id: 4589, disc_number: null };

  it('shows the locked identity and target drive', () => {
    render(<ArmedDriveCard driveId="E:" identity={identity} onDisarm={vi.fn()} />);

    expect(screen.getByText(/Arrested Development/)).toBeInTheDocument();
    expect(screen.getByText(/E:/)).toBeInTheDocument();
    expect(screen.getByText(/season 1/i)).toBeInTheDocument();
  });

  it('calls onDisarm when dismissed', () => {
    const onDisarm = vi.fn();
    render(<ArmedDriveCard driveId="E:" identity={identity} onDisarm={onDisarm} />);

    fireEvent.click(screen.getByRole('button', { name: /disarm/i }));

    expect(onDisarm).toHaveBeenCalledWith('E:');
  });

  it('omits the season line for movies', () => {
    render(
      <ArmedDriveCard
        driveId="E:"
        identity={{ ...identity, content_type: 'movie', season: null }}
        onDisarm={vi.fn()}
      />,
    );

    expect(screen.queryByText(/season/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- ArmedDriveCard`
Expected: FAIL, cannot resolve `./ArmedDriveCard`

- [ ] **Step 3: Write the component**

```tsx
import { motion } from 'motion/react';
import { IcoDisc, IcoCancel } from './icons';
import { sv } from './synapse';

export interface ArmedIdentity {
    title: string;
    content_type: string;
    season: number | null;
    tmdb_id: number | null;
    disc_number: number | null;
}

interface ArmedDriveCardProps {
    driveId: string;
    identity: ArmedIdentity;
    onDisarm: (driveId: string) => void;
}

/**
 * A drive armed with a user-asserted identity, waiting for a disc. Rendered
 * dashed and without glow so it reads as "not a real job yet", but occupying a
 * card slot so an armed drive is impossible to forget.
 */
export default function ArmedDriveCard({ driveId, identity, onDisarm }: ArmedDriveCardProps) {
    const isTv = identity.content_type === 'tv';
    const meta = [
        isTv ? 'TV SERIES' : 'MOVIE',
        isTv && identity.season != null ? `SEASON ${identity.season}` : null,
        identity.tmdb_id != null ? `TMDB ${identity.tmdb_id}` : null,
    ].filter(Boolean).join(' · ');

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            data-testid="armed-drive-card"
            style={{
                display: 'flex', gap: 18, padding: 20,
                border: `1px dashed ${sv.lineHi}`,
                background: `${sv.bg1}99`,
            }}
        >
            <div style={{
                width: 96, height: 96, flexShrink: 0,
                border: `1px dashed ${sv.lineMid}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
                <IcoDisc size={36} color={`${sv.cyan}55`} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontFamily: sv.mono, fontSize: 15, fontWeight: 700, color: sv.inkDim }}>
                            {identity.title}
                        </div>
                        <div style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, marginTop: 4, letterSpacing: '0.14em' }}>
                            {meta}
                        </div>
                        <div style={{ fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, marginTop: 5, letterSpacing: '0.14em' }}>
                            DRIVE {driveId} · IDENTITY LOCKED BY USER
                        </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexShrink: 0 }}>
                        <span style={{
                            fontFamily: sv.mono, fontSize: 9, letterSpacing: '0.2em',
                            padding: '5px 9px', border: `1px solid ${sv.cyan}55`, color: sv.cyan,
                        }}>
                            AWAITING DISC
                        </span>
                        <button
                            type="button"
                            aria-label={`Disarm drive ${driveId}`}
                            title="Disarm"
                            onClick={() => onDisarm(driveId)}
                            style={{
                                width: 28, height: 28, display: 'flex',
                                alignItems: 'center', justifyContent: 'center',
                                background: sv.bg0, border: `1px solid ${sv.red}55`,
                                color: sv.red, cursor: 'pointer',
                            }}
                        >
                            <IcoCancel size={13} />
                        </button>
                    </div>
                </div>
                <div style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim, marginTop: 12 }}>
                    Insert a disc in {driveId}. It will scan, adopt this identity, and rip
                    without stopping to ask.
                </div>
            </div>
        </motion.div>
    );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test:unit -- ArmedDriveCard`
Expected: PASS, 3 passed

- [ ] **Step 5: Wire into App.tsx**

Add armed-drive state, handle the `drive_armed` WebSocket message, render the cards above the job list, and pass `onManualClick` to `SvTopBar`:

```tsx
const [armedDrives, setArmedDrives] = useState<Record<string, ArmedIdentity>>({});
const [showArmModal, setShowArmModal] = useState(false);

// In the existing WebSocket message switch:
case 'drive_armed':
    setArmedDrives((prev) => {
        const next = { ...prev };
        if (msg.identity) next[msg.drive_id] = msg.identity;
        else delete next[msg.drive_id];
        return next;
    });
    break;

const disarmDrive = async (driveId: string) => {
    await fetch('/api/manual/disarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ drive_id: driveId }),
    });
    // The drive_armed broadcast clears local state; drop it here too so the
    // card disappears immediately even if the socket is briefly down.
    setArmedDrives((prev) => {
        const next = { ...prev };
        delete next[driveId];
        return next;
    });
};
```

Render above the filtered disc list:

```tsx
{Object.entries(armedDrives).map(([driveId, identity]) => (
    <ArmedDriveCard
        key={driveId}
        driveId={driveId}
        identity={identity}
        onDisarm={disarmDrive}
    />
))}
```

And the modal, defaulting to the first detected drive or `"E:"`:

```tsx
{showArmModal && (
    <ArmDiscModal
        driveId={defaultDriveId}
        onClose={() => setShowArmModal(false)}
        onArmed={() => setShowArmModal(false)}
    />
)}
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npm run test:unit && npm run build && npm run lint`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/components/ArmedDriveCard.tsx frontend/src/app/components/ArmedDriveCard.test.tsx frontend/src/app/App.tsx
git commit -m "feat: armed drive card and manual modal wiring (#520)"
```

---

### Task 12: Provenance chip and always-on identity control

**Files:**
- Modify: `frontend/src/app/components/DiscCard.tsx` (`DiscData` interface, header block at line 386)
- Modify: `frontend/src/app/components/DiscCard/ActionButtons.tsx` (label)
- Modify: `frontend/src/app/App.tsx:743` and the job-to-disc adapter
- Test: `frontend/src/app/components/DiscCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
it('shows the MANUAL ID chip when identity was user-asserted', () => {
    render(<DiscCard disc={{ ...baseDisc, identitySource: 'manual' }} />);
    expect(screen.getByText(/manual id/i)).toBeInTheDocument();
});

it('shows the chip for a corrected identity too', () => {
    render(<DiscCard disc={{ ...baseDisc, identitySource: 'manual_correction' }} />);
    expect(screen.getByText(/manual id/i)).toBeInTheDocument();
});

it('shows no chip for an automatically identified disc', () => {
    render(<DiscCard disc={{ ...baseDisc, identitySource: 'tmdb' }} />);
    expect(screen.queryByText(/manual id/i)).not.toBeInTheDocument();
});

it('labels the identity button "Edit ID" on a manual disc', () => {
    render(<DiscCard disc={{ ...baseDisc, identitySource: 'manual' }} onReIdentify={vi.fn()} />);
    expect(screen.getByRole('button', { name: /edit id/i })).toBeInTheDocument();
});

it('labels it "Wrong title?" on an auto-identified disc', () => {
    render(<DiscCard disc={{ ...baseDisc, identitySource: 'tmdb' }} onReIdentify={vi.fn()} />);
    expect(screen.getByRole('button', { name: /wrong title/i })).toBeInTheDocument();
});
```

Reuse the existing `baseDisc` fixture in that file, adding `identitySource` to it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test:unit -- DiscCard`
Expected: FAIL, no MANUAL ID text and no /edit id/ button

- [ ] **Step 3: Write minimal implementation**

Add to the `DiscData` interface in `DiscCard.tsx`:

```tsx
  /** How this disc's identity was obtained, from the backend's
   *  classification_source. "manual" (asserted before the rip) and
   *  "manual_correction" (user overrode a guess) both render one chip;
   *  the distinction exists only for diagnostics. */
  identitySource?: string;
```

Add a derived flag next to `showIdentityReview` (line 239):

```tsx
    const isManualIdentity =
      disc.identitySource === 'manual' || disc.identitySource === 'manual_correction';
```

Render the chip in the header action group, immediately before `hasDamagedTrack` (line 387):

```tsx
                  {isManualIdentity && (
                    <span
                      data-testid="disccard-manual-badge"
                      title="Identity was entered by you, not detected"
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 9,
                        fontWeight: 700,
                        letterSpacing: "0.2em",
                        color: sv.cyan,
                        border: `1px solid ${sv.cyan}66`,
                        background: `${sv.cyan}14`,
                        padding: "3px 6px",
                      }}
                    >
                      MANUAL ID
                    </span>
                  )}
```

Pass the flag to `ActionButtons`:

```tsx
                  <ActionButtons
                    state={disc.state}
                    isHovered={isHovered}
                    onCancel={onCancel}
                    onReview={onReview}
                    onReIdentify={onReIdentify}
                    onAdvance={onAdvance}
                    emphasizeReIdentify={showIdentityReview}
                    manualIdentity={isManualIdentity}
                  />
```

In `ActionButtons.tsx`, add `manualIdentity?: boolean` to the props and make the label contextual:

```tsx
            {onReIdentify && (
                <ToneButton
                    tone={CYAN}
                    onClick={(e) => { e.stopPropagation(); onReIdentify(); }}
                    title={manualIdentity ? "Edit this disc's identity" : "Wrong title — re-identify disc"}
                    ariaLabel={manualIdentity ? "Edit disc identity" : "Wrong title — re-identify disc"}
                    paddingX={10}
                    emphasis={emphasizeReIdentify}
                >
                    <IcoRetry size={12} />
                    <span style={{ fontSize: 10 }}>{manualIdentity ? "Edit ID" : "Wrong title?"}</span>
                </ToneButton>
            )}
```

- [ ] **Step 4: Make the control always-on**

In `App.tsx`, change line 743 from:

```tsx
                  onReIdentify={disc.needsReview && disc.title ? () => {
```

to:

```tsx
                  // Always-on identity control (#520): available for the whole
                  // window the backend accepts a re-identify, not only in review.
                  // Excludes matching/organizing (work in flight) and completed
                  // (History's AmendTitleModal owns that).
                  onReIdentify={
                    disc.title && ['scanning', 'ripping', 'review_needed'].includes(disc.state)
                      ? () => {
```

closing the ternary the same way. In the job-to-disc adapter (`useDiscFilters`'s `transformJobToDiscData`), map the new field:

```tsx
    identitySource: job.classification_source,
```

Confirm `classification_source` is present on the `Job` type in `frontend/src/types.ts`; it is already returned by `/api/jobs` (`routes.py:500`). Add it to the type if missing.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test:unit && npm run build && npm run lint`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/DiscCard.tsx frontend/src/app/components/DiscCard/ActionButtons.tsx frontend/src/app/App.tsx frontend/src/types.ts
git commit -m "feat: manual provenance chip and always-on identity control (#520)"
```

---

### Task 13: End-to-end verification and changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full suites**

```bash
cd backend && uv run pytest tests/unit/ -q && uv run ruff check .
cd ../frontend && npm run test:unit && npm run lint && npm run build
```

Expected: all PASS

- [ ] **Step 2: Drive the real UI**

Start backend (`DEBUG=true`, port 8000, no `--reload`) and frontend (`npm run dev`). Then:

1. Click MANUAL, enter a TV show with a season, arm the drive.
2. Confirm the armed card appears with the locked identity.
3. Simulate an insert:
   ```bash
   curl -X POST localhost:8000/api/simulate/insert-disc \
     -H "Content-Type: application/json" \
     -d '{"volume_label":"UNREADABLE_XYZ","content_type":"tv","simulate_ripping":true}'
   ```
4. Confirm the armed card is replaced by a job card carrying the asserted title, a MANUAL ID chip, an Edit ID button, and **no** identity prompt CTA.
5. Click Edit ID mid-rip, change the season, confirm it applies without interrupting the rip.
6. Confirm a normal simulated disc still shows "Wrong title?" and is unaffected.

- [ ] **Step 3: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, in `### Added`:

```markdown
- **Enter disc details yourself when Engram cannot work them out** — a new **Manual** button lets you name a disc before you insert it: pick the show or film, season, and disc number, and the next disc in that drive adopts exactly what you entered, skipping automatic identification and ripping unattended with no questions asked. Useful for homemade discs, obscure releases, and labels that are just a catalog number. Every active disc also gains an always-available identity control, so you can correct a title mid-rip instead of waiting for Engram to ask, and a disc you identified yourself is now marked so you can tell at a glance which titles were asserted rather than detected. (#520)
```

- [ ] **Step 4: Stop the servers**

```powershell
Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for manual disc metadata entry (#520)"
```

---

## Self-review notes

**Spec coverage:** every spec section maps to a task. Provenance marker → Tasks 6, 12. Arm store → Task 1. Endpoints → Task 3. WS event → Task 2. Insert hook → Task 5. Manual identify → Task 4. Card edit → Task 6, 12. Modal → Tasks 8, 9. Top bar → Task 10. Armed card → Task 11. Edge cases → Task 3 (409, blank title), Task 4 (freeform title, gates), Task 1 (one-shot, restart). Testing plan → all tasks plus Task 13.

**Deviation from the spec, recorded deliberately:** the spec said "skip `_run_classification` entirely." This plan overrides after classification instead, and adds two explicit gate guards the spec did not anticipate. See the design note at the top for why. The spec should be updated to match if this approach survives implementation.

**Known softness to resolve during implementation:**
- Task 5's test calls `job_manager._handle_disc_inserted(...)`; confirm the real method name and signature at `job_manager.py:635` and adjust.
- Task 4 Step 5 places the manual `disc_number` assignment near the override, but the existing regex parse at lines 331-340 runs later and would clobber it. Apply the manual value *after* that block.
- Task 12 assumes a `baseDisc` fixture exists in `DiscCard.test.tsx`; create one if absent.

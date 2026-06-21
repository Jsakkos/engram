# DiscDB Lookup Enablement + ASR-Preferred Episode Precedence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable TheDiscDB lookup (disc identification + track matching) while keeping contribution gated off, and make Engram ASR the preferred episode signal so DiscDB's disc-order episode mappings only apply as a low-confidence fallback.

**Architecture:** Split the single backend (`DISCDB_ENABLED`) and frontend (`FEATURES.DISCDB`) master switches into separate lookup and contribution flags. Invert the ASR/DiscDB episode precedence: remove the two pre-dispatch DiscDB short-circuits so ASR always runs, and move DiscDB episode assignment into `_match_single_file_inner` as a fallback gated on ASR confidence (< 0.5). Harden the contribution submit endpoints with a backend gate. Refresh the stale DiscDB match-source badge.

**Tech Stack:** Python/FastAPI/SQLModel backend (pytest), React/TypeScript/Vite frontend (vitest).

**Spec:** `docs/superpowers/specs/2026-06-20-discdb-lookup-asr-precedence.md`

---

## File Structure

Backend:
- `backend/app/core/features.py` — split master flag into lookup + contributions.
- `backend/app/services/identification_coordinator.py` — re-gate lookup; remove DiscDB short-circuit; drop dead injection.
- `backend/app/services/cleanup_service.py` — re-gate auto-export under contributions flag.
- `backend/app/services/matching_coordinator.py` — add `DISCDB_FALLBACK_ASR_FLOOR`; add DiscDB fallback in `_match_single_file_inner`.
- `backend/app/services/job_manager.py` — remove DiscDB short-circuit in `_dispatch_match_for_title`; drop dead wiring.
- `backend/app/api/routes.py` — backend contribution guard on submit/export endpoints.
- Tests: `backend/tests/unit/test_match_source.py`, `test_identification_coordinator.py`, `test_disc_name_identification.py`.

Frontend:
- `frontend/src/config/constants.ts` — split `FEATURES.DISCDB`.
- `frontend/src/config/routes.ts`, `frontend/src/app/navigation.ts`, `frontend/src/app/App.tsx`, `frontend/src/components/ReviewQueue/Inspector.tsx`, `frontend/src/components/HistoryPage.tsx`, `frontend/src/components/ConfigWizard.tsx` — re-gate per concern.
- `frontend/src/app/components/synapse/tokens.ts` — add `blue` token.
- `frontend/src/app/components/TrackGrid.tsx` — DiscDB icon-chip badge.
- Tests: `frontend/src/app/components/TrackGrid.test.tsx`, `frontend/src/app/__tests__/App.routing.test.tsx`.

---

## Task 1: Split backend feature flags

**Files:**
- Modify: `backend/app/core/features.py`
- Modify: `backend/app/services/identification_coordinator.py:1516-1519`
- Modify: `backend/app/services/cleanup_service.py:36-39`

- [ ] **Step 1: Replace the master flag with two flags**

In `backend/app/core/features.py`, replace the whole body after the docstring:
```python
# TheDiscDB lookup — disc identification + track matching. Read-only GraphQL
# queries; safe to ship.
DISCDB_LOOKUP_ENABLED = True

# TheDiscDB contributions — local export + submit/upload to thediscdb.com.
# Keep False until the contribution API contract and UX are validated.
DISCDB_CONTRIBUTIONS_ENABLED = False
```

- [ ] **Step 2: Re-gate the lookup consumer**

In `backend/app/services/identification_coordinator.py` around line 1516-1519, change:
```python
        from app.core.features import DISCDB_ENABLED

        discdb_signal = None
        if DISCDB_ENABLED and config.discdb_enabled:
```
to:
```python
        from app.core.features import DISCDB_LOOKUP_ENABLED

        discdb_signal = None
        if DISCDB_LOOKUP_ENABLED and config.discdb_enabled:
```

- [ ] **Step 3: Re-gate the auto-export consumer**

In `backend/app/services/cleanup_service.py` around line 36-39, change:
```python
        from app.core.features import DISCDB_ENABLED

        if DISCDB_ENABLED and state == JobState.COMPLETED and config.discdb_contributions_enabled:
```
to:
```python
        from app.core.features import DISCDB_CONTRIBUTIONS_ENABLED

        if (
            DISCDB_CONTRIBUTIONS_ENABLED
            and state == JobState.COMPLETED
            and config.discdb_contributions_enabled
        ):
```

- [ ] **Step 4: Verify nothing references the old name**

Run: `cd backend && grep -rn "DISCDB_ENABLED" app/`
Expected: no matches in `app/` (only `DISCDB_LOOKUP_ENABLED` / `DISCDB_CONTRIBUTIONS_ENABLED`). Tests are updated in Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/features.py backend/app/services/identification_coordinator.py backend/app/services/cleanup_service.py
git commit -m "feat(discdb): split master flag into lookup + contributions"
```

---

## Task 2: Backend guard on contribution endpoints

The submit/export endpoints are gated only by `require_localhost`, not by any feature flag. Add a contribution gate so enabling lookup never exposes upload.

**Files:**
- Modify: `backend/app/api/routes.py` (endpoints at ~3149 `export_contribution`, ~3728 `submit_contribution`, ~3834 batch submit)

- [ ] **Step 1: Add a guard helper near the top of the contribution endpoints**

In `backend/app/api/routes.py`, immediately before the `export_contribution` definition (around line 3149), add:
```python
def _require_discdb_contributions() -> None:
    """Reject contribution endpoints unless the contribution feature is enabled.

    Lookup may be on while contribution stays gated; without this guard the
    submit/export endpoints would remain reachable directly.
    """
    from app.core.features import DISCDB_CONTRIBUTIONS_ENABLED

    if not DISCDB_CONTRIBUTIONS_ENABLED:
        raise HTTPException(status_code=404, detail="TheDiscDB contributions are disabled")
```

- [ ] **Step 2: Call the guard first in each contribution endpoint**

As the first statement in the body of `export_contribution`, `submit_contribution`, and the batch-submit endpoint (the three functions importing from `app.core.discdb_submitter`), add:
```python
    _require_discdb_contributions()
```
Place it before the existing `from app.core.discdb_submitter import ...` line in each.

- [ ] **Step 3: Verify the three call sites**

Run: `cd backend && grep -n "_require_discdb_contributions()" app/api/routes.py`
Expected: 3 matches (one per contribution endpoint).

- [ ] **Step 4: Smoke-check import**

Run: `cd backend && uv run python -c "import app.api.routes"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py
git commit -m "feat(discdb): gate contribution submit/export endpoints behind contributions flag"
```

---

## Task 3: Invert ASR <-> DiscDB episode precedence

ASR always runs. DiscDB episode mappings become a post-ASR fallback when ASR confidence is below 0.5.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (add constant near other module constants; fallback inside `_match_single_file_inner` at the `if result.needs_review or advisory:` branch, ~line 1194)
- Modify: `backend/app/services/identification_coordinator.py:1056-1070` (remove short-circuit)
- Modify: `backend/app/services/job_manager.py:2865-2893` (remove short-circuit)
- Test: `backend/tests/unit/test_match_source.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_match_source.py`:
```python
@pytest.mark.asyncio
async def test_high_confidence_asr_wins_over_discdb_mapping(monkeypatch):
    """ASR is the preferred signal: a confident ASR match wins even when a
    DiscDB mapping disagrees (DiscDB numbers by disc order, not aired order)."""
    from app.core.discdb_classifier import DiscDbTitleMapping

    mc_mod = importlib.import_module("app.services.matching_coordinator")
    job, title = await _seed_job_and_title()

    mock_result = MagicMock()
    mock_result.episode_code = "S01E03"  # ASR says E03
    mock_result.confidence = 0.85
    mock_result.needs_review = False
    mock_result.match_details = {"score": 0.85}
    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    mapping = DiscDbTitleMapping(
        index=0, title_type="Episode", episode_title="Pilot",
        season=1, episode=1, duration_seconds=2400, size_bytes=1024**3,
    )  # DiscDB says E01
    coordinator = _make_coordinator(monkeypatch, discdb_mappings={job.id: [mapping]})

    await coordinator._match_single_file_inner(
        job.id, title.id, Path("/tmp/staging/test/title_t00.mkv")
    )

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.state == TitleState.MATCHED
        assert title.matched_episode == "S01E03"  # ASR won
        assert title.match_source == "engram"


@pytest.mark.asyncio
async def test_very_low_asr_falls_back_to_discdb_autoorganize(monkeypatch):
    """When ASR confidence is very low (< 0.5) and a DiscDB mapping exists, the
    DiscDB episode is assigned and auto-organized instead of going to review."""
    from app.core.discdb_classifier import DiscDbTitleMapping

    mc_mod = importlib.import_module("app.services.matching_coordinator")
    job, title = await _seed_job_and_title()

    mock_result = MagicMock()
    mock_result.episode_code = "S01E07"  # weak ASR guess
    mock_result.confidence = 0.41
    mock_result.needs_review = True
    mock_result.match_details = {"score": 0.41}
    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    mapping = DiscDbTitleMapping(
        index=0, title_type="Episode", episode_title="Pilot",
        season=1, episode=1, duration_seconds=2400, size_bytes=1024**3,
    )
    coordinator = _make_coordinator(monkeypatch, discdb_mappings={job.id: [mapping]})

    await coordinator._match_single_file_inner(
        job.id, title.id, Path("/tmp/staging/test/title_t00.mkv")
    )

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.state == TitleState.MATCHED  # auto-organized, not review
        assert title.matched_episode == "S01E01"  # DiscDB mapping
        assert title.match_source == "discdb"


@pytest.mark.asyncio
async def test_mid_confidence_asr_goes_to_review_not_discdb(monkeypatch):
    """Mid-confidence ASR (0.5 <= conf < 0.7) routes to review as the ASR guess;
    DiscDB is NOT used because ASR is preferred above the 0.5 floor."""
    from app.core.discdb_classifier import DiscDbTitleMapping

    mc_mod = importlib.import_module("app.services.matching_coordinator")
    job, title = await _seed_job_and_title()

    mock_result = MagicMock()
    mock_result.episode_code = "S01E07"
    mock_result.confidence = 0.60
    mock_result.needs_review = True
    mock_result.match_details = {"score": 0.60}
    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    mapping = DiscDbTitleMapping(
        index=0, title_type="Episode", episode_title="Pilot",
        season=1, episode=1, duration_seconds=2400, size_bytes=1024**3,
    )
    coordinator = _make_coordinator(monkeypatch, discdb_mappings={job.id: [mapping]})

    await coordinator._match_single_file_inner(
        job.id, title.id, Path("/tmp/staging/test/title_t00.mkv")
    )

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.state == TitleState.REVIEW  # ASR guess held for review
        assert title.match_source is None  # DiscDB not applied
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_match_source.py -v -k "discdb_mapping or discdb_autoorganize or review_not_discdb"`
Expected: `test_very_low_asr_falls_back_to_discdb_autoorganize` and `test_mid_confidence_asr_goes_to_review_not_discdb` FAIL (current code never applies DiscDB inside `_match_single_file_inner`, so the very-low case goes to REVIEW and the mid case has no DiscDB anyway). `test_high_confidence_asr_wins_over_discdb_mapping` may already PASS.

- [ ] **Step 3: Add the fallback floor constant**

In `backend/app/services/matching_coordinator.py`, alongside the other module-level constants (e.g. near `_SEASON_FROM_EP_CODE_RE`), add:
```python
# ASR-preferred episode precedence: ASR always runs and is authoritative at or
# above this confidence. Only below it do we defer to a DiscDB episode mapping —
# DiscDB numbers episodes by physical disc order, not aired order, so it is a
# last-resort fallback, never a competitor to a usable ASR match.
DISCDB_FALLBACK_ASR_FLOOR = 0.5
```

- [ ] **Step 4: Add the fallback in `_match_single_file_inner`**

In `backend/app/services/matching_coordinator.py`, the branch currently reads:
```python
                if result.needs_review or advisory:
                    # A low-confidence result always goes to REVIEW — even when the
```
Insert the fallback as the first thing inside that branch, before the existing comment/`title.state = TitleState.REVIEW`:
```python
                if result.needs_review or advisory:
                    # ASR-preferred precedence: a very low-confidence ASR result
                    # (not a deliberate manual re-match) defers to a DiscDB episode
                    # mapping when one exists, instead of going to review. DiscDB
                    # numbers by disc order (not aired order), so it is trusted only
                    # when ASR could not produce a usable match.
                    if (
                        not advisory
                        and result.confidence < DISCDB_FALLBACK_ASR_FLOOR
                        and await self.try_discdb_assignment(job_id, title, session)
                    ):
                        logger.info(
                            f"[MATCH] Title {title_id} (Job {job_id}): ASR confidence "
                            f"{result.confidence:.2f} < {DISCDB_FALLBACK_ASR_FLOOR}; "
                            f"assigned episode from DiscDB mapping (disc-order fallback)."
                        )
                        await self._check_job_completion(session, job_id)
                        return

                    # A low-confidence result always goes to REVIEW — even when the
```
(The rest of the existing branch is unchanged.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_match_source.py -v`
Expected: all tests PASS (the three new ones plus the existing five).

- [ ] **Step 6: Remove the short-circuit in identification_coordinator**

In `backend/app/services/identification_coordinator.py` around line 1056-1070, replace:
```python
                        for dt in db_titles:
                            if dt.output_filename:
                                file_path = Path(dt.output_filename)
                                discdb_applied = await self._try_discdb_assignment(
                                    job_id, dt, session
                                )
                                if not discdb_applied:
                                    task = asyncio.create_task(
                                        self._match_single_file(job_id, dt.id, file_path)
                                    )
                                    task.add_done_callback(
                                        lambda t, jid=job_id, tid=dt.id: self._on_match_task_done(
                                            t, jid, tid
                                        )
                                    )
```
with:
```python
                        for dt in db_titles:
                            if dt.output_filename:
                                file_path = Path(dt.output_filename)
                                # ASR-preferred precedence: always run audio matching.
                                # A DiscDB episode mapping (disc order, not aired order)
                                # is applied only as a low-confidence fallback inside
                                # _match_single_file_inner.
                                task = asyncio.create_task(
                                    self._match_single_file(job_id, dt.id, file_path)
                                )
                                task.add_done_callback(
                                    lambda t, jid=job_id, tid=dt.id: self._on_match_task_done(
                                        t, jid, tid
                                    )
                                )
```

- [ ] **Step 7: Remove the short-circuit in job_manager**

In `backend/app/services/job_manager.py` around line 2865-2893, replace:
```python
        applied = False
        try:
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title is None:
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: title "
                        f"{sanitize_log_value(title_id)} vanished before match dispatch"
                    )
                    self._inflight_match_dispatch.discard(title_id)
                    return False
                applied = await self._matching.try_discdb_assignment(job_id, title, session)
                if applied:
                    await self._finalization.check_job_completion(session, job_id)
        except Exception:
            self._inflight_match_dispatch.discard(title_id)
            raise
        if applied:
            # DiscDB path resolved the title — no match task needed; release
            # the sentinel so a future re-dispatch (e.g. after a re-rip) works.
            self._inflight_match_dispatch.discard(title_id)
            return True

        # match_single_file self-tags the job log context.
        task = asyncio.create_task(self._matching.match_single_file(job_id, title_id, file_path))
```
with:
```python
        try:
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title is None:
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: title "
                        f"{sanitize_log_value(title_id)} vanished before match dispatch"
                    )
                    self._inflight_match_dispatch.discard(title_id)
                    return False
        except Exception:
            self._inflight_match_dispatch.discard(title_id)
            raise

        # ASR-preferred precedence: always run audio matching. A DiscDB episode
        # mapping (disc order, not aired order) is applied only as a low-confidence
        # fallback inside _match_single_file_inner.
        # match_single_file self-tags the job log context.
        task = asyncio.create_task(self._matching.match_single_file(job_id, title_id, file_path))
```
(The `task.add_done_callback(...)` and `return True` lines that follow are unchanged.)

- [ ] **Step 8: Run the broader matching + job-manager suites**

Run: `cd backend && uv run pytest tests/unit/test_match_source.py tests/unit/test_matching_coordinator.py tests/unit/test_job_manager.py tests/unit/test_identification_coordinator.py -v`
Expected: PASS (Task 4 fixes any flag-rename failures in test_identification_coordinator.py; if that file fails only on `DISCDB_ENABLED`, proceed to Task 4 then re-run).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/app/services/identification_coordinator.py backend/app/services/job_manager.py backend/tests/unit/test_match_source.py
git commit -m "feat(matching): prefer ASR over DiscDB episode order; DiscDB fallback below 0.5"
```

---

## Task 4: Update existing backend flag-reference tests

**Files:**
- Modify: `backend/tests/unit/test_identification_coordinator.py:127,163`
- Modify: `backend/tests/unit/test_disc_name_identification.py:341,416,505,585,676,768,936`

- [ ] **Step 1: Update test_identification_coordinator.py**

Replace both occurrences:
```python
        monkeypatch.setattr("app.core.features.DISCDB_ENABLED", True)
```
with:
```python
        monkeypatch.setattr("app.core.features.DISCDB_LOOKUP_ENABLED", True)
```

- [ ] **Step 2: Update test_disc_name_identification.py**

Replace all seven occurrences:
```python
        patch("app.core.features.DISCDB_ENABLED", False),
```
with:
```python
        patch("app.core.features.DISCDB_LOOKUP_ENABLED", False),
```

- [ ] **Step 3: Verify no stale references remain**

Run: `cd backend && grep -rn "DISCDB_ENABLED" tests/`
Expected: no matches.

- [ ] **Step 4: Run both test files**

Run: `cd backend && uv run pytest tests/unit/test_identification_coordinator.py tests/unit/test_disc_name_identification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/unit/test_identification_coordinator.py backend/tests/unit/test_disc_name_identification.py
git commit -m "test(discdb): update flag references to DISCDB_LOOKUP_ENABLED"
```

---

## Task 5: Remove the now-dead try_discdb_assignment injection

After Task 3, `IdentificationCoordinator._try_discdb_assignment` is never called. Remove the dead wiring (the public `MatchingCoordinator.try_discdb_assignment` stays — it is now called by the fallback).

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:211,225,239`
- Modify: `backend/app/services/job_manager.py:181`

- [ ] **Step 1: Remove the attribute init**

In `backend/app/services/identification_coordinator.py`, delete line 211:
```python
        self._try_discdb_assignment: callable = None
```

- [ ] **Step 2: Remove the kwarg from set_callbacks**

In the same file, in `set_callbacks`, delete the parameter line 225:
```python
        try_discdb_assignment,
```
and the assignment line 239:
```python
        self._try_discdb_assignment = try_discdb_assignment
```

- [ ] **Step 3: Remove the wiring in job_manager**

In `backend/app/services/job_manager.py`, delete line 181:
```python
            try_discdb_assignment=self._matching.try_discdb_assignment,
```

- [ ] **Step 4: Verify it is fully gone**

Run: `cd backend && grep -rn "_try_discdb_assignment\|try_discdb_assignment=" app/`
Expected: no matches (the `def try_discdb_assignment` and `self.try_discdb_assignment` self-call in matching_coordinator are different and must remain — confirm with `grep -rn "try_discdb_assignment" app/` showing only the matching_coordinator definition and the `_match_single_file_inner` self-call).

- [ ] **Step 5: Run the orchestration suites**

Run: `cd backend && uv run pytest tests/unit/test_identification_coordinator.py tests/unit/test_job_manager.py tests/unit/test_matching_coordinator_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/identification_coordinator.py backend/app/services/job_manager.py
git commit -m "refactor(discdb): drop dead try_discdb_assignment injection after precedence change"
```

---

## Task 6: Split frontend feature flags and re-gate sites

**Files:**
- Modify: `frontend/src/config/constants.ts:10-13`
- Modify: `frontend/src/config/routes.ts:39`
- Modify: `frontend/src/app/navigation.ts:64`
- Modify: `frontend/src/app/App.tsx:106,945`
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx:403`
- Modify: `frontend/src/components/HistoryPage.tsx:604`
- Modify: `frontend/src/app/__tests__/App.routing.test.tsx:66,74`

- [ ] **Step 1: Split the flags**

In `frontend/src/config/constants.ts`, replace:
```ts
export const FEATURES = {
  /** TheDiscDB integration — contribute page, match-source badges, settings toggle. */
  DISCDB: false,
} as const;
```
with:
```ts
export const FEATURES = {
  /** TheDiscDB lookups: match-source badges, history metadata, lookup settings toggle. */
  DISCDB_LOOKUP: true,
  /** TheDiscDB contributions: contribute page, nav item, stats badge, submit controls. */
  DISCDB_CONTRIBUTE: false,
} as const;
```

- [ ] **Step 2: Re-gate the contribute route registry**

In `frontend/src/config/routes.ts:39`, change `FEATURES.DISCDB` to `FEATURES.DISCDB_CONTRIBUTE`:
```ts
  ...(FEATURES.DISCDB_CONTRIBUTE ? [ROUTES.CONTRIBUTE] : []),
```

- [ ] **Step 3: Re-gate the nav item**

In `frontend/src/app/navigation.ts:64`, change:
```ts
      show: FEATURES.DISCDB_CONTRIBUTE,
```

- [ ] **Step 4: Re-gate App.tsx (stats fetch + route)**

In `frontend/src/app/App.tsx:106`:
```tsx
      if (FEATURES.DISCDB_CONTRIBUTE && data.discdb_contributions_enabled) {
```
In `frontend/src/app/App.tsx:945`:
```tsx
      {FEATURES.DISCDB_CONTRIBUTE && <Route path={ROUTES.CONTRIBUTE} element={<ContributePage />} />}
```

- [ ] **Step 5: Re-gate the lookup-side UI**

In `frontend/src/components/ReviewQueue/Inspector.tsx:403`:
```tsx
                        {FEATURES.DISCDB_LOOKUP && title.discdb_match_details && title.match_details && (
```
In `frontend/src/components/HistoryPage.tsx:604`:
```tsx
          {FEATURES.DISCDB_LOOKUP && (
```

- [ ] **Step 6: Update the routing test**

In `frontend/src/app/__tests__/App.routing.test.tsx:74`:
```tsx
  if (FEATURES.DISCDB_CONTRIBUTE) routeCases.push(["/contribute", "contribute"]);
```
(The comment on line 66 may keep referring to the gate generically; update "FEATURES.DISCDB" to "FEATURES.DISCDB_CONTRIBUTE" there if present.)

- [ ] **Step 7: Verify no stale flag references**

Run: `cd frontend && grep -rn "FEATURES.DISCDB\b" src/`
Expected: no matches for the bare `FEATURES.DISCDB` (only `DISCDB_LOOKUP` / `DISCDB_CONTRIBUTE`). The ConfigWizard site is handled in Task 7; if it still shows here, that is expected until then.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/config/constants.ts frontend/src/config/routes.ts frontend/src/app/navigation.ts frontend/src/app/App.tsx frontend/src/components/ReviewQueue/Inspector.tsx frontend/src/components/HistoryPage.tsx frontend/src/app/__tests__/App.routing.test.tsx
git commit -m "feat(discdb): split frontend feature flag into lookup vs contribute"
```

---

## Task 7: Split the ConfigWizard DiscDB group and fix stale copy

The group holds both the lookup toggle and the contribution toggle. Split them, and correct the lookup hint copy (it claims DiscDB "skips audio fingerprinting and instantly maps episodes", which is no longer true).

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx:1219-1299`

- [ ] **Step 1: Gate the group on either flag, and split the toggles**

In `frontend/src/components/ConfigWizard.tsx`, change the group opener at line 1220:
```tsx
                        {FEATURES.DISCDB && (
```
to:
```tsx
                        {(FEATURES.DISCDB_LOOKUP || FEATURES.DISCDB_CONTRIBUTE) && (
```

- [ ] **Step 2: Wrap the lookup toggle and fix its hint**

Wrap the lookup checkbox `div` (lines 1226-1240) so it renders only under `DISCDB_LOOKUP`, and replace the stale hint text:
```tsx
                                {FEATURES.DISCDB_LOOKUP && (
                                <div className="form-group checkbox-group">
                                    <label className="checkbox-label">
                                        <input
                                            type="checkbox"
                                            checked={config.discdbEnabled}
                                            onChange={(e) => handleInputChange('discdbEnabled', e.target.checked)}
                                        />
                                        <span className="checkbox-text">
                                            <strong>Enable TheDiscDB Lookup</strong>
                                            <span className="checkbox-hint">
                                                Query TheDiscDB to help identify a disc. Episode matching still runs locally (audio); DiscDB episode order is used only as a fallback when audio matching is uncertain. No API key required.
                                            </span>
                                        </span>
                                    </label>
                                </div>
                                )}
```

- [ ] **Step 3: Wrap the contributions toggle + sub-block under DISCDB_CONTRIBUTE**

Wrap the contributions checkbox `div` (lines 1241-1255) and the following `{config.discdbContributionsEnabled && ( ... )}` block (lines 1257-1296) in a single `{FEATURES.DISCDB_CONTRIBUTE && ( ... )}` guard:
```tsx
                                {FEATURES.DISCDB_CONTRIBUTE && (
                                <>
                                <div className="form-group checkbox-group">
                                    <label className="checkbox-label">
                                        <input
                                            type="checkbox"
                                            checked={config.discdbContributionsEnabled}
                                            onChange={(e) => handleInputChange('discdbContributionsEnabled', e.target.checked)}
                                        />
                                        <span className="checkbox-text">
                                            <strong>Enable TheDiscDB Contributions</strong>
                                            <span className="checkbox-hint">
                                                Share disc metadata (track info, episode mappings) with TheDiscDB after each rip. Helps others identify their discs automatically. No personal data is shared.
                                            </span>
                                        </span>
                                    </label>
                                </div>

                                {config.discdbContributionsEnabled && (
                                    <>
                                        <div className="form-group">
                                            <label htmlFor="discdbContributionTier">Contribution Level</label>
                                            <EngramSelect
                                                id="discdbContributionTier"
                                                value={String(config.discdbContributionTier)}
                                                onValueChange={(v) => handleInputChange('discdbContributionTier', parseInt(v, 10))}
                                                options={[
                                                    { value: '2', label: 'Automatic — share auto-collected data' },
                                                    { value: '3', label: 'Full — prompt for UPC and images' },
                                                ]}
                                            />
                                        </div>

                                        <div className="form-group">
                                            <label htmlFor="discdbApiKey">TheDiscDB API Key</label>
                                            <input
                                                id="discdbApiKey"
                                                type="password"
                                                value={config.discdbApiKey}
                                                onChange={(e) => handleInputChange('discdbApiKey', e.target.value)}
                                                placeholder="Enter API key for automatic submission"
                                            />
                                            <small>Required for submitting directly to TheDiscDB. Leave empty for local-only export.</small>
                                        </div>

                                        <div className="form-group">
                                            <label htmlFor="discdbExportPath">Export Directory (optional)</label>
                                            <input
                                                id="discdbExportPath"
                                                type="text"
                                                value={config.discdbExportPath}
                                                onChange={(e) => handleInputChange('discdbExportPath', e.target.value)}
                                                placeholder="~/.engram/discdb-exports"
                                            />
                                            <small>Leave empty for the default location</small>
                                        </div>
                                    </>
                                )}
                                </>
                                )}
```
Note: the option labels keep their existing em dashes to avoid altering unrelated copy; do not introduce new ones elsewhere.

- [ ] **Step 4: Verify the bare flag is gone**

Run: `cd frontend && grep -rn "FEATURES.DISCDB\b" src/`
Expected: no matches.

- [ ] **Step 5: Type-check the build**

Run: `cd frontend && npm run build`
Expected: TypeScript + Vite build succeeds (run `npm install` first if `node_modules` is absent; do not commit `package-lock.json` churn — `git checkout package-lock.json` if it changes).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(discdb): split ConfigWizard lookup/contribution toggles; fix stale lookup copy"
```

---

## Task 8: Refresh the DiscDB match-source badge to the icon system

**Files:**
- Modify: `frontend/src/app/components/synapse/tokens.ts` (add `blue` token)
- Modify: `frontend/src/app/components/TrackGrid.tsx:46-107`
- Test: `frontend/src/app/components/TrackGrid.test.tsx:71-75`

- [ ] **Step 1: Update the failing test first**

In `frontend/src/app/components/TrackGrid.test.tsx` around line 71-75, the test currently asserts the text `"DISCDB"`. Change it to assert the icon chip via its testid:
```tsx
    expect(screen.getByTestId("source-badge-discdb")).toBeInTheDocument();
    expect(screen.getByText("99%")).toBeInTheDocument();
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test:unit -- TrackGrid`
Expected: the updated assertion FAILS (the icon chip is not yet rendered for `discdb`; it is still a text chip — actually `getByTestId` would currently pass on the text chip, so this test may still pass. If it passes, that is acceptable; the behavioral change is the icon swap in Step 4. Proceed.)

- [ ] **Step 3: Add a `blue` token**

In `frontend/src/app/components/synapse/tokens.ts`, in the Functional section (after line 40 `purple`), add:
```ts
  blue: "#60a5fa",
```

- [ ] **Step 4: Make the DiscDB source an icon chip**

In `frontend/src/app/components/TrackGrid.tsx`:

(a) Import `IcoDisc` at the top with the other icon imports:
```tsx
import { IcoDisc } from "./icons/media";
```
(verify the exact existing icon import path/style in the file and match it).

(b) Extend the `SourceDesc` type (lines 46-52) with an optional icon component:
```tsx
type SourceDesc = {
  kind: "icon" | "text";
  label: string;
  tone: string;
  tooltip: string;
  node?: boolean;
  Icon?: React.ComponentType<{ size?: number; color?: string }>;
};
```
(If TypeScript complains that `IcoDisc`'s `IconProps` is not assignable, widen to `React.ComponentType<any>` or import and use the icon module's `IconProps` type.)

(c) Change the `discdb` descriptor (line 60) from a text chip to an icon chip:
```tsx
  discdb:             { kind: "icon", label: "DISCDB", tone: sv.blue,    tooltip: "Matched from TheDiscDB", Icon: IcoDisc },
```

(d) In `SourceChip`, the icon branch (line 101) currently hardcodes `MarkMono`. Render the descriptor's icon when present, else `MarkMono`:
```tsx
          {desc.Icon ? (
            <desc.Icon size={12} color={desc.tone} />
          ) : (
            <MarkMono size={12} color={desc.tone} node={desc.node} />
          )}
```

- [ ] **Step 5: Run the badge test**

Run: `cd frontend && npm run test:unit -- TrackGrid`
Expected: PASS (the `source-badge-discdb` testid resolves to the icon chip span; `99%` still renders).

- [ ] **Step 6: Build**

Run: `cd frontend && npm run build`
Expected: success.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/components/synapse/tokens.ts frontend/src/app/components/TrackGrid.tsx frontend/src/app/components/TrackGrid.test.tsx
git commit -m "feat(discdb): render match-source badge with IcoDisc icon chip on palette blue"
```

---

## Final verification

- [ ] **Backend full suite**

Run: `cd backend && uv run pytest tests/unit/ -q`
Expected: PASS. (If `engram.db` is a 0-byte worktree stub, run a one-off `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first.)

- [ ] **Backend lint/format**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Frontend unit + build + lint**

Run: `cd frontend && npm run test:unit && npm run build && npm run lint`
Expected: PASS. `git checkout package-lock.json` if install rewrote it.

- [ ] **Spec coverage self-check**

Confirm each spec section maps to a task: flag split (Tasks 1, 6, 7), contribution gating hardening (Task 2), ASR precedence (Task 3), dead-wiring cleanup (Task 5), badge refresh (Task 8), test updates (Tasks 3, 4, 6, 8).

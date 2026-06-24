# Overridable Auto-Detected Extras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the default `keep` extras policy from filing extras mid-rip; instead label them `MATCHED`/`"extra"` and let the existing end-of-disc finalizer handle them, so a disc that goes to review shows extras as ordinary, reassignable tracks.

**Architecture:** The app already defers organizing normal matched tracks (state `MATCHED`) to `check_job_completion` → `finalize_disc_job`. Extras are the sole exception that organizes early and jumps to `COMPLETED`, which freezes them in the review UI's read-only "Processed" list. We make extras ride the same deferred path. `finalize_disc_job` already files `matched_episode == "extra"` titles into `Extras/`, so no new organizing code is needed.

**Tech Stack:** Python 3.11 (FastAPI, SQLModel, async SQLite), pytest. Frontend React 18 + TypeScript + Vite, vitest.

**Reference spec:** `docs/superpowers/specs/2026-06-23-overridable-extras-design.md`

---

## File Structure

- `backend/app/services/matching_coordinator.py` — `_handle_extras` `keep` branch: defer instead of organize. (modify)
- `backend/app/services/finalization_coordinator.py` — `_apply_decision_fields`: clear `is_extra` when reassigning to a real episode. (modify)
- `backend/tests/unit/test_matching_coordinator.py` — replace the `keep`-policy tests with the deferred-behaviour test. (modify)
- `backend/tests/unit/test_finalization_coordinator.py` — add a review-hold guard test for a deferred extra and `_apply_decision_fields` tests. (modify)
- `frontend/src/components/ReviewQueue/utils.ts` — new pure `buildInitialSelections` helper + `TitleAction` type. (modify)
- `frontend/src/components/ReviewQueue/utils.test.ts` — vitest tests for the helper. (create)
- `frontend/src/components/ReviewQueue.tsx` — use the helper for pre-fill; import `TitleAction` from utils. (modify)

---

## Task 1: Defer the `keep` extras branch (backend)

**Files:**
- Modify: `backend/tests/unit/test_matching_coordinator.py:108-181` (the three `keep`-policy tests)
- Modify: `backend/app/services/matching_coordinator.py:1550-1623` (the `keep` default branch of `_handle_extras`)

- [ ] **Step 1: Replace the `keep` tests with the deferred-behaviour test**

In `backend/tests/unit/test_matching_coordinator.py`, delete the three existing tests
`test_keep_policy_organizes_to_extras`, `test_keep_policy_records_organize_error`, and
`test_keep_policy_threads_tmdb_id_and_year` (lines 108-181, inside `class TestHandleExtras`)
and replace them with this single test:

```python
    async def test_keep_policy_defers_as_matched_extra(self, monkeypatch, tmp_path):
        """keep no longer organizes mid-match. The extra rests as MATCHED with the
        synthetic "extra" code so it rides the normal end-of-disc finalize path
        (auto-files into Extras/ on a clean disc, or stays editable if the disc
        goes to review). Organizing here would set COMPLETED early and freeze the
        title in the review UI's read-only "Processed" list."""
        _patch_config(monkeypatch, "keep")
        import app.core.organizer as org

        org_spy = Mock()
        monkeypatch.setattr(org, "organize_tv_extras", org_spy)
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            handled = await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert handled is True
            assert title.state == TitleState.MATCHED
            assert title.is_extra is True
            assert title.matched_episode == "extra"
            assert title.organized_to is None
            assert json.loads(title.match_details)["action"] == "deferred"
        org_spy.assert_not_called()
        coord._check_job_completion.assert_awaited_once()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_matching_coordinator.py::TestHandleExtras::test_keep_policy_defers_as_matched_extra -v`
Expected: FAIL — the current `keep` branch sets `state == COMPLETED` and calls `organize_tv_extras`, so the `MATCHED`/`organized_to is None`/`org_spy.assert_not_called()` assertions fail.

- [ ] **Step 3: Rewrite the `keep` branch to defer**

In `backend/app/services/matching_coordinator.py`, replace the entire default `keep`
block (from the comment `# Default: "keep" — organize to extras folder` through the
final `return True` of `_handle_extras`, currently lines 1550-1623) with:

```python
        # Default: "keep" — defer organization to end-of-disc finalize.
        # The extra rides the normal MATCHED -> finalize path like any other
        # track: a cleanly-matched disc files it into Extras/ at finalize and
        # auto-completes, while a disc that goes to review shows it as an
        # ordinary, reassignable track (pre-labelled "extra"). Organizing here
        # would set COMPLETED early and freeze it in the review UI's read-only
        # "Processed" list — the bug this fixes.
        title.state = TitleState.MATCHED
        title.is_extra = True
        title.matched_episode = "extra"
        title.match_details = json.dumps(
            {
                "auto_sorted": "extras",
                "action": "deferred",
                "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
            }
        )
        session.add(title)
        await session.commit()
        await ws_manager.broadcast_title_update(
            job_id,
            title.id,
            title.state.value,
            matched_episode=title.matched_episode,
            is_extra=title.is_extra,
            match_details=title.match_details,
        )
        await self._check_job_completion(session, job_id)
        return True
```

This removes the local `from app.core.organizer import organize_tv_extras` import, the
`extras_count`/`extra_index` counting, the `organize_tv_extras` call, and both the
success and failure organize branches — all now handled at finalize.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_matching_coordinator.py::TestHandleExtras -v`
Expected: PASS (the new test plus the unchanged `skip`/`ask` tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "fix(extras): defer keep-policy extras to end-of-disc finalize

Stop organizing keep-policy extras mid-match. Mark them MATCHED with the
synthetic \"extra\" code so they ride the normal finalize path: a clean
disc files them into Extras/ at the end, while a disc that goes to review
shows them as ordinary, reassignable tracks instead of freezing them in
the read-only Processed list."
```

---

## Task 2: Guard that a deferred extra holds while another title needs review (backend)

This is a regression-guard test. `check_job_completion` already organizes nothing while
any title is in `REVIEW`; this locks in that a deferred extra is included in that hold.
(The auto-complete path is already covered by `test_all_matched_invokes_finalize` plus
`test_extra_title_routes_to_extras_folder` in the same file.)

**Files:**
- Modify: `backend/tests/unit/test_finalization_coordinator.py` (add to `class TestCheckJobCompletion`, after `test_review_title_transitions_to_review` ~line 513)

- [ ] **Step 1: Write the test**

Add this method inside `class TestCheckJobCompletion`:

```python
    async def test_deferred_extra_held_when_other_title_needs_review(self, tmp_path):
        """A deferred extra (MATCHED + "extra") must not finalize while another
        title still needs review — the whole disc holds in staging unorganized."""
        job_id = await _seed_job(
            [
                (0, "extra", None, TitleState.MATCHED),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, titles = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert titles[0].state == TitleState.MATCHED
        assert titles[0].matched_episode == "extra"
        assert titles[0].organized_to is None
        coord.finalize_disc_job.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_finalization_coordinator.py::TestCheckJobCompletion::test_deferred_extra_held_when_other_title_needs_review -v`
Expected: PASS (mirrors the existing `test_review_title_transitions_to_review` setup; the
`has_review` branch parks the job before any finalize).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_finalization_coordinator.py
git commit -m "test(extras): guard deferred extra holds while a title needs review"
```

---

## Task 3: Clear `is_extra` when a user reassigns an extra to a real episode (backend)

`_finalize_tv_if_resolved` and `finalize_disc_job` recompute `is_extra` from the final
`matched_episode` at organize time, so reassignment already files correctly. This makes
the in-DB flag consistent immediately on the review decision rather than transiently
stale until finalize.

**Files:**
- Modify: `backend/tests/unit/test_finalization_coordinator.py` (add a new test class)
- Modify: `backend/app/services/finalization_coordinator.py:1374-1377` (`_apply_decision_fields`)

- [ ] **Step 1: Write the failing tests**

Add this test class at the end of `backend/tests/unit/test_finalization_coordinator.py`:

```python
@pytest.mark.unit
class TestApplyDecisionFields:
    def test_clears_is_extra_on_real_episode(self):
        t = DiscTitle(
            job_id=1, title_index=0, is_extra=True, matched_episode="extra"
        )
        FinalizationCoordinator._apply_decision_fields(t, "S01E03", None)
        assert t.matched_episode == "S01E03"
        assert t.is_extra is False

    def test_keeps_is_extra_for_extra_code(self):
        t = DiscTitle(job_id=1, title_index=0, matched_episode=None)
        FinalizationCoordinator._apply_decision_fields(t, "extra", None)
        assert t.is_extra is True
```

- [ ] **Step 2: Run the tests to verify the first fails**

Run: `cd backend && uv run pytest tests/unit/test_finalization_coordinator.py::TestApplyDecisionFields -v`
Expected: `test_clears_is_extra_on_real_episode` FAILS (current code never clears
`is_extra`); `test_keeps_is_extra_for_extra_code` PASSES.

- [ ] **Step 3: Update `_apply_decision_fields`**

In `backend/app/services/finalization_coordinator.py`, replace the `if episode_code:`
block at the top of `_apply_decision_fields` (currently lines 1374-1377):

```python
        if episode_code:
            title.matched_episode = episode_code
            if episode_code == "extra":
                title.is_extra = True
```

with:

```python
        if episode_code:
            title.matched_episode = episode_code
            if episode_code == "extra":
                title.is_extra = True
            elif episode_code != "skip":
                # Reassigning a (possibly auto-detected) extra to a real episode
                # must clear the extra flag so finalize files it as an episode,
                # not back into Extras/.
                title.is_extra = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_finalization_coordinator.py::TestApplyDecisionFields -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/finalization_coordinator.py backend/tests/unit/test_finalization_coordinator.py
git commit -m "fix(extras): clear is_extra when a review reassigns an extra to an episode"
```

---

## Task 4: Frontend review pre-fill for deferred extras

The review UI already treats `MATCHED` titles as editable and renders the "extra" badge
and "Extra" action button. The gap is the pre-fill: a title with
`matched_episode === "extra"` currently pre-fills as an `episode` action. Extract the
pre-fill into a pure, tested helper and fix the `extra` case.

**Files:**
- Modify: `frontend/src/components/ReviewQueue/utils.ts`
- Create: `frontend/src/components/ReviewQueue/utils.test.ts`
- Modify: `frontend/src/components/ReviewQueue.tsx` (import `TitleAction`; use the helper at `:160` and `:281-292`)

- [ ] **Step 1: Add the helper to `utils.ts`**

At the top of `frontend/src/components/ReviewQueue/utils.ts`, add an import for
`normalizeEpisodeCode`:

```typescript
import { normalizeEpisodeCode } from './coverage';
```

Then append to the end of the file:

```typescript
/** A staged review decision for a single title. */
export type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';

/**
 * Build the initial staged selections/actions from persisted match results.
 *
 * An auto-deferred extra (matched_episode === "extra") pre-fills as the "extra"
 * action so the review UI shows it selected as an extra and a no-op save re-files
 * it as one. Any other matched_episode pre-fills as a (canonicalized) episode pick.
 */
export function buildInitialSelections(titles: DiscTitle[]): {
    episodes: Record<number, string>;
    actions: Record<number, TitleAction>;
} {
    const episodes: Record<number, string> = {};
    const actions: Record<number, TitleAction> = {};
    for (const title of titles) {
        if (title.matched_episode === 'extra') {
            episodes[title.id] = 'extra';
            actions[title.id] = 'extra';
        } else if (title.matched_episode) {
            episodes[title.id] = normalizeEpisodeCode(title.matched_episode);
            actions[title.id] = 'episode';
        }
    }
    return { episodes, actions };
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/ReviewQueue/utils.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import { buildInitialSelections } from './utils';

const t = (id: number, matched_episode: string | null): DiscTitle =>
    ({ id, matched_episode } as DiscTitle);

describe('buildInitialSelections', () => {
    it('pre-fills a deferred extra as the "extra" action', () => {
        const { episodes, actions } = buildInitialSelections([t(1, 'extra')]);
        expect(episodes[1]).toBe('extra');
        expect(actions[1]).toBe('extra');
    });

    it('pre-fills a matched episode as an "episode" action, canonicalized', () => {
        const { episodes, actions } = buildInitialSelections([t(2, 'S1E3')]);
        expect(episodes[2]).toBe('S01E03');
        expect(actions[2]).toBe('episode');
    });

    it('omits unmatched titles', () => {
        const { episodes, actions } = buildInitialSelections([t(3, null)]);
        expect(episodes[3]).toBeUndefined();
        expect(actions[3]).toBeUndefined();
    });
});
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ReviewQueue/utils.test.ts`
Expected: PASS (3 tests). If `node_modules` is missing, run `npm install` first.

- [ ] **Step 4: Wire the helper into `ReviewQueue.tsx`**

In `frontend/src/components/ReviewQueue.tsx`:

(a) Update the utils import (currently `import { formatDuration, formatSize, titleDisplayName } from './ReviewQueue/utils';` at line 8) to also pull in the helper and type:

```typescript
import { formatDuration, formatSize, titleDisplayName, buildInitialSelections, type TitleAction } from './ReviewQueue/utils';
```

(b) Delete the local type declaration `type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';` (line 160) — it now comes from utils.

(c) Replace the pre-fill block inside `fetchJobDetails` (currently lines 281-292):

```typescript
                // Pre-fill selections from existing match results
                const episodes: Record<number, string> = {};
                const actions: Record<number, TitleAction> = {};
                titlesData.forEach((title: DiscTitle) => {
                    if (title.matched_episode) {
                        // Canonicalize so unpadded matcher output (e.g. "S1E14")
                        // dedupes/collides against padded codes and the roster.
                        episodes[title.id] = normalizeEpisodeCode(title.matched_episode);
                        actions[title.id] = 'episode';
                    }
                });
                setSelectedEpisodes(episodes);
                setTitleActions(actions);
```

with:

```typescript
                // Pre-fill selections from existing match results. A deferred
                // extra (matched_episode === "extra") pre-fills as the "extra"
                // action so it shows selected as an extra and is reassignable.
                const { episodes, actions } = buildInitialSelections(titlesData);
                setSelectedEpisodes(episodes);
                setTitleActions(actions);
```

- [ ] **Step 5: Verify the build/lint pass**

Run: `cd frontend && npm run build && npm run lint`
Expected: PASS. (`normalizeEpisodeCode` is still imported and used elsewhere in
`ReviewQueue.tsx` at `handleEpisodeChange`, so its import stays. If lint flags an unused
import, remove only the genuinely-unused one.) If `node_modules` is missing, run
`npm install` first; afterwards `git checkout package-lock.json` to drop the unrelated
lockfile churn.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ReviewQueue/utils.ts frontend/src/components/ReviewQueue/utils.test.ts frontend/src/components/ReviewQueue.tsx
git commit -m "fix(review): pre-fill deferred extras as the extra action"
```

---

## Task 5: Simulation check + full verification

- [ ] **Step 1: Confirm the simulation path needs no change**

The keep-policy deferral lives in `_handle_extras`, which only the real matching path
calls. Confirm simulation fabricates titles directly and does not depend on the old
early-organize behaviour:

Run: `cd backend && grep -n "is_extra\|organize_tv_extras\|matched_episode" app/services/simulation_service.py`
Expected: no call into `_handle_extras` and no assertion that extras are `COMPLETED`
mid-rip. If simulation sets extras to `COMPLETED` early itself, change those to
`TitleState.MATCHED` + `matched_episode="extra"` to match the new behaviour; otherwise
no change is needed. (No code change is expected here.)

- [ ] **Step 2: Run the full backend test suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS. Watch specifically for any other test that asserted keep-policy extras
reach `COMPLETED` mid-match or that `organize_tv_extras` is called during matching — if
one exists, update it to expect the deferred `MATCHED`/`"extra"` behaviour.

> Note: in a fresh worktree the backend DB may be a 0-byte stub causing
> "no such table: app_config". If pipeline/integration tests fail on that, run a
> one-off `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"`
> first (env setup, not a regression).

- [ ] **Step 3: Run backend lint/format**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 4: Run the full frontend unit suite**

Run: `cd frontend && npm run test:unit`
Expected: PASS.

- [ ] **Step 5: Add a CHANGELOG entry**

In `CHANGELOG.md`, under the `## [Unreleased]` section's `### Fixed` (create the
subsection if absent), add:

```markdown
- Auto-detected extras are no longer filed away mid-rip and frozen in the review panel. They now stay with the rest of the disc's tracks and are organized at the end — so when a disc goes to review you can reassign a misdetected extra (e.g. a dual-episode track) to an episode before it's filed. (#NNN)
```

(Replace `#NNN` with the PR number once opened.)

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note overridable auto-detected extras"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 implements the core deferral; Task 3 covers reassignment correctness; Task 4 covers the frontend pre-fill; Tasks 2 & 5 cover the testing section and the simulation-path risk called out in the spec. The auto-complete path (clean disc files the extra at finalize) is covered by the pre-existing `test_all_matched_invokes_finalize` + `test_extra_title_routes_to_extras_folder`.
- **Out of scope (do not implement):** duration-heuristic changes, multi-episode (`S01E01-E02`) support, forcing extras into review, and any "move an already-organized extra" path. Extras never organize early anymore, so that path does not arise.
- **Naming consistency:** the helper is `buildInitialSelections` and the type is `TitleAction` in both `utils.ts` and `ReviewQueue.tsx`. The synthetic code is the string `"extra"` everywhere; the deferred `match_details.action` value is `"deferred"`.

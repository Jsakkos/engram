# Inline AI-match Feedback in the Review Inspector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the `reason` returned by the "Try AI match" endpoint as inline feedback in the Review Inspector (instead of silent failure), plus a "Matching…" pending state on the button while the 1–3 min request is in flight.

**Architecture:** Frontend-only. A pure mapping helper (`reason → feedback`) is unit-tested in isolation. The Inspector renders the feedback (in the same slot as the success suggestion card) and the pending button state; both are component-tested with vitest + React Testing Library. ReviewQueue holds two title-keyed state maps and wires them through. No backend changes — the consumed `reason` values are exactly what `llm_match_title` returns today (`null` / `"cached"` / `"no_suggestion"` / `"internal_error"`).

**Tech Stack:** React 18 + TypeScript, vitest + @testing-library/react (jsdom), existing Synapse `SvNotice` / `SvActionButton` primitives, `IcoRetry` icon.

**Spec:** `docs/superpowers/specs/2026-06-06-llm-match-feedback-inspector-design.md`

---

## File Structure

- **Create** `frontend/src/components/ReviewQueue/llmFeedback.ts` — one responsibility: map an `LLMMatchResult` to inline Inspector feedback. Exports the `LLMFeedback` type and `llmResultToFeedback()`.
- **Create** `frontend/src/components/ReviewQueue/llmFeedback.test.ts` — unit tests for the helper.
- **Create** `frontend/src/components/ReviewQueue/Inspector.test.tsx` — component tests for the feedback notice + pending button.
- **Modify** `frontend/src/components/ReviewQueue/Inspector.tsx` — add `llmFeedback` + `isLlmMatching` props; render the notice; pending button state.
- **Modify** `frontend/src/components/ReviewQueue.tsx` — add two title-keyed state maps; rewrite `handleTryLLMMatch`; pass the two new props to `<Inspector>`.
- **Modify** `CHANGELOG.md` — `### Fixed` bullet under `## [Unreleased]`.

---

## Task 0: Environment setup (worktree)

A fresh worktree often lacks `frontend/node_modules`, and the committed `package-lock.json` may be stale so `npm install` rewrites it (~13k-line diff). Do NOT commit that churn.

- [ ] **Step 1: Install frontend deps**

Run (from repo root):
```bash
cd frontend && npm install
```
Expected: completes; `node_modules/` populated.

- [ ] **Step 2: Discard any package-lock churn from install**

Run:
```bash
cd frontend && git checkout package-lock.json
```
Expected: `package-lock.json` back to committed state (no-op if install didn't touch it).

- [ ] **Step 3: Baseline the unit-test runner**

Run:
```bash
cd frontend && npm run test:unit
```
Expected: existing suite passes (green). Confirms vitest is wired before we add tests.

---

## Task 1: Pure `reason → feedback` helper

**Files:**
- Create: `frontend/src/components/ReviewQueue/llmFeedback.ts`
- Test: `frontend/src/components/ReviewQueue/llmFeedback.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ReviewQueue/llmFeedback.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { llmResultToFeedback } from './llmFeedback';
import type { LLMMatchResult } from '../../api/client';

const suggestion: LLMMatchResult['suggestion'] = {
    episode: 4,
    confidence: 0.91,
    reasoning: 'matched dialogue',
    runner_up: null,
    model: 'gemini',
};

describe('llmResultToFeedback', () => {
    it('returns null on a fresh suggestion (success surfaces as the card)', () => {
        expect(llmResultToFeedback({ suggestion, reason: null })).toBeNull();
    });

    it('returns null for a cached suggestion', () => {
        expect(llmResultToFeedback({ suggestion, reason: 'cached' })).toBeNull();
    });

    it('warns when no confident match was found', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'no_suggestion' })).toEqual({
            tone: 'warn',
            text: 'No confident AI match found.',
        });
    });

    it('errors on an internal server error', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'internal_error' })).toEqual({
            tone: 'error',
            text: 'AI match failed — check the server log.',
        });
    });

    it('falls through unknown non-error reasons to the warn message', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'ai_disabled' })).toEqual({
            tone: 'warn',
            text: 'No confident AI match found.',
        });
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd frontend && npx vitest run src/components/ReviewQueue/llmFeedback.test.ts
```
Expected: FAIL — cannot resolve `./llmFeedback` (module not created yet).

- [ ] **Step 3: Write the minimal implementation**

Create `frontend/src/components/ReviewQueue/llmFeedback.ts`:
```ts
import type { LLMMatchResult } from '../../api/client';
import type { SvNoticeTone } from '../../app/components/synapse';

/** Inline feedback shown in the Inspector after a "Try AI match" run. */
export interface LLMFeedback {
    tone: SvNoticeTone;
    text: string;
}

/**
 * Map a `runLLMMatch` result to inline Inspector feedback.
 *
 * Returns null when the result surfaces on its own as the cyan suggestion card
 * (a fresh suggestion, or a `"cached"` one). Only the "silent" outcomes — where
 * the endpoint returned HTTP 200 but produced no suggestion — get a notice.
 *
 * Unknown future reasons (e.g. `ai_disabled`, `not_configured`) fall through to
 * the generic "no confident match" message rather than breaking.
 */
export function llmResultToFeedback(result: LLMMatchResult): LLMFeedback | null {
    if (result.suggestion) return null;
    if (!result.reason || result.reason === 'cached') return null;
    if (result.reason === 'internal_error') {
        return { tone: 'error', text: 'AI match failed — check the server log.' };
    }
    return { tone: 'warn', text: 'No confident AI match found.' };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd frontend && npx vitest run src/components/ReviewQueue/llmFeedback.test.ts
```
Expected: PASS — 5 tests green.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/components/ReviewQueue/llmFeedback.ts src/components/ReviewQueue/llmFeedback.test.ts
git commit -m "feat(review): add llmResultToFeedback helper for AI-match feedback"
```

---

## Task 2: Inspector renders feedback notice + pending button

**Files:**
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx`
- Test: `frontend/src/components/ReviewQueue/Inspector.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/components/ReviewQueue/Inspector.test.tsx`:
```tsx
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Inspector } from './Inspector';
import type { DiscTitle, Job } from '../../types';
import type { LLMFeedback } from './llmFeedback';

function makeTitle(overrides: Partial<DiscTitle> = {}): DiscTitle {
    return {
        id: 1,
        job_id: 1,
        title_index: 1,
        duration_seconds: 1320,
        file_size_bytes: 1_000_000,
        chapter_count: 5,
        is_selected: true,
        output_filename: null,
        matched_episode: null,
        match_confidence: 0,
        state: 'review',
        ...overrides,
    };
}

function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: 1,
        drive_id: 'E:',
        volume_label: 'SHOW_S1D1',
        content_type: 'tv',
        state: 'review_needed',
        current_speed: '',
        eta_seconds: 0,
        progress_percent: 0,
        current_title: 0,
        total_titles: 0,
        error_message: null,
        detected_title: 'Show',
        detected_season: 1,
        ...overrides,
    };
}

function renderInspector(props: {
    llmFeedback?: LLMFeedback | null;
    isLlmMatching?: boolean;
    aiEpisodeMatchingEnabled?: boolean;
} = {}) {
    return render(
        <Inspector
            title={makeTitle()}
            job={makeJob()}
            candidates={[]}
            suggestion={null}
            selection={undefined}
            action={undefined}
            episodes={[]}
            coverage={{}}
            holders={new Map()}
            titleIndexById={{ 1: 1 }}
            isRematching={false}
            aiEpisodeMatchingEnabled={props.aiEpisodeMatchingEnabled ?? true}
            llmFeedback={props.llmFeedback ?? null}
            isLlmMatching={props.isLlmMatching ?? false}
            onAssign={vi.fn()}
            onAction={vi.fn()}
            onRematch={vi.fn()}
            onDeepRematch={vi.fn()}
            onTryLLMMatch={vi.fn()}
            onAcceptLLMSuggestion={vi.fn()}
        />,
    );
}

describe('Inspector — AI match feedback', () => {
    it('shows a notice when llmFeedback is set and there is no suggestion', () => {
        renderInspector({ llmFeedback: { tone: 'warn', text: 'No confident AI match found.' } });
        expect(screen.getByText(/No confident AI match found\./)).toBeInTheDocument();
    });

    it('shows no notice when there is no feedback', () => {
        renderInspector({ llmFeedback: null });
        expect(screen.queryByText(/No confident AI match found\./)).not.toBeInTheDocument();
    });

    it('disables the button and shows Matching… while in flight', () => {
        renderInspector({ isLlmMatching: true });
        const btn = screen.getByRole('button', { name: /matching/i });
        expect(btn).toBeDisabled();
    });

    it('shows the default Try AI match label when idle', () => {
        renderInspector({ isLlmMatching: false });
        expect(screen.getByRole('button', { name: /try ai match/i })).toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd frontend && npx vitest run src/components/ReviewQueue/Inspector.test.tsx
```
Expected: FAIL — TypeScript/runtime error because `Inspector` does not yet accept `llmFeedback` / `isLlmMatching`, and the "Matching…" label / disabled state don't exist.

- [ ] **Step 3: Add the new props to the Inspector type + signature**

In `frontend/src/components/ReviewQueue/Inspector.tsx`, add the import for the feedback type. Place it next to the other local `./` type imports (near line 8 `import type { LLMSuggestion, RosterEpisode } from './types';`):
```tsx
import type { LLMFeedback } from './llmFeedback';
```

In the props type object (the `}: { ... }` block, after `aiEpisodeMatchingEnabled: boolean;` ~line 64), add:
```tsx
    llmFeedback: LLMFeedback | null;
    isLlmMatching: boolean;
```

In the destructured parameter list (after `aiEpisodeMatchingEnabled,` ~line 44), add:
```tsx
    llmFeedback,
    isLlmMatching,
```

- [ ] **Step 4: Render the feedback notice in the LLM-suggestion slot**

In `frontend/src/components/ReviewQueue/Inspector.tsx`, immediately AFTER the existing `{llmSuggestion && ( ... )}` block (which ends ~line 192, just before `{/* ranked candidates */}`), add:
```tsx
                {/* AI match feedback — silent outcomes (no confident match / error).
                    Shares the suggestion slot; only shown when there is no suggestion. */}
                {!llmSuggestion && llmFeedback && (
                    <div style={{ marginBottom: 14 }}>
                        <SvNotice tone={llmFeedback.tone}>› {llmFeedback.text}</SvNotice>
                    </div>
                )}
```
(`SvNotice` is already imported at the top of the file.)

- [ ] **Step 5: Add the pending state to the "Try AI match" button**

In `frontend/src/components/ReviewQueue/Inspector.tsx`, replace the existing button block (~lines 354–363):
```tsx
                        {aiEpisodeMatchingEnabled && (
                            <SvActionButton
                                tone="cyan"
                                size="sm"
                                onClick={() => onTryLLMMatch(title.id)}
                                title="Run AI episode matching"
                            >
                                Try AI match
                            </SvActionButton>
                        )}
```
with:
```tsx
                        {aiEpisodeMatchingEnabled && (
                            <SvActionButton
                                tone="cyan"
                                size="sm"
                                onClick={() => onTryLLMMatch(title.id)}
                                disabled={isLlmMatching}
                                title="Run AI episode matching"
                            >
                                {isLlmMatching ? (
                                    <>
                                        <IcoRetry size={11} className="animate-spin" /> Matching…
                                    </>
                                ) : (
                                    'Try AI match'
                                )}
                            </SvActionButton>
                        )}
```
(`IcoRetry` is already imported at the top of the file — line 3.)

- [ ] **Step 6: Run the test to verify it passes**

Run:
```bash
cd frontend && npx vitest run src/components/ReviewQueue/Inspector.test.tsx
```
Expected: PASS — 4 tests green.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/components/ReviewQueue/Inspector.tsx src/components/ReviewQueue/Inspector.test.tsx
git commit -m "feat(review): render AI-match feedback notice + pending state in Inspector"
```

---

## Task 3: Wire feedback + pending state through ReviewQueue

**Files:**
- Modify: `frontend/src/components/ReviewQueue.tsx`

No new unit test here — this is glue (React state + prop passing) verified by `tsc` (`npm run build`) and the existing component/unit tests; behavior is confirmed in the manual check (Task 4, Step 3).

- [ ] **Step 1: Import the helper and type**

In `frontend/src/components/ReviewQueue.tsx`, add near the existing `./ReviewQueue/...` imports (the file already imports `Inspector` from `./ReviewQueue/Inspector`):
```tsx
import { llmResultToFeedback, type LLMFeedback } from './ReviewQueue/llmFeedback';
```
(`runLLMMatch` is already imported — the existing handler uses it.)

- [ ] **Step 2: Add the two title-keyed state maps**

In `frontend/src/components/ReviewQueue.tsx`, after `const [rematchNotice, setRematchNotice] = useState<string | null>(null);` (~line 174), add:
```tsx
    const [llmFeedback, setLlmFeedback] = useState<Record<number, LLMFeedback | null>>({});
    const [llmMatchingId, setLlmMatchingId] = useState<number | null>(null);
```

- [ ] **Step 3: Rewrite `handleTryLLMMatch`**

In `frontend/src/components/ReviewQueue.tsx`, replace the existing handler (~lines 374–386):
```tsx
    // Run the LLM matcher for a single title, then refresh so the persisted
    // llm_suggestion in match_details surfaces in the Inspector.
    const handleTryLLMMatch = async (titleId: number) => {
        if (!jobId) return;
        setError(null);
        try {
            await runLLMMatch(parseInt(jobId), titleId);
            await fetchJobDetails();
        } catch (err) {
            console.error('LLM match failed', err);
            setError(err instanceof Error ? err.message : 'AI match failed');
        }
    };
```
with:
```tsx
    // Run the LLM matcher for a single title, then refresh so the persisted
    // llm_suggestion surfaces in the Inspector. The endpoint always returns 200,
    // so a "silent" outcome (no_suggestion / internal_error) is reported via
    // inline Inspector feedback rather than a thrown error. Pending + feedback
    // are keyed by title id so they follow the selected title.
    const handleTryLLMMatch = async (titleId: number) => {
        if (!jobId) return;
        setError(null);
        setLlmFeedback((prev) => ({ ...prev, [titleId]: null }));
        setLlmMatchingId(titleId);
        try {
            const result = await runLLMMatch(parseInt(jobId), titleId);
            await fetchJobDetails();
            const feedback = llmResultToFeedback(result);
            if (feedback) {
                setLlmFeedback((prev) => ({ ...prev, [titleId]: feedback }));
            }
        } catch (err) {
            console.error('LLM match failed', err);
            setLlmFeedback((prev) => ({
                ...prev,
                [titleId]: {
                    tone: 'error',
                    text: err instanceof Error ? err.message : 'AI match failed.',
                },
            }));
        } finally {
            // Only clear if this title is still the in-flight one (a fast click on
            // another title must not clear the wrong spinner).
            setLlmMatchingId((cur) => (cur === titleId ? null : cur));
        }
    };
```

- [ ] **Step 4: Pass the two new props to `<Inspector>`**

In `frontend/src/components/ReviewQueue.tsx`, in the `<Inspector ... />` element (~line 1052), after `aiEpisodeMatchingEnabled={aiEpisodeMatchingEnabled}` (~line 1064), add:
```tsx
                                llmFeedback={llmFeedback[selectedTitle.id] ?? null}
                                isLlmMatching={llmMatchingId === selectedTitle.id}
```

- [ ] **Step 5: Type-check the whole frontend**

Run:
```bash
cd frontend && npm run build
```
Expected: `tsc` passes with no errors, Vite build completes. (Catches any missing/mismatched prop or type.)

- [ ] **Step 6: Run the full unit suite**

Run:
```bash
cd frontend && npm run test:unit
```
Expected: all tests green, including `llmFeedback.test.ts` and `Inspector.test.tsx`.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/components/ReviewQueue.tsx
git commit -m "feat(review): wire AI-match feedback + pending state into ReviewQueue"
```

---

## Task 4: Changelog + final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the changelog entry**

In `CHANGELOG.md`, under `## [Unreleased]`, add (create the `### Fixed` subsection if it does not already exist):
```markdown
### Fixed

- Review Inspector's "Try AI match" button now reports its outcome: an inline notice when the AI matcher finds no confident match ("No confident AI match found.") or errors ("AI match failed — check the server log."), and a "Matching…" pending state while the request runs — previously it failed silently. (#NNN)
```
Note: replace `#NNN` with the PR number once the PR is opened. The `CHANGELOG.md merge=union` driver handles concurrent `[Unreleased]` edits, so don't worry about reordering.

- [ ] **Step 2: Run lint**

Run:
```bash
cd frontend && npm run lint
```
Expected: `eslint` passes with no errors/warnings (`--max-warnings 0`).

- [ ] **Step 3: Manual verification**

Start the backend with `DEBUG=true` and open a job that has a TV title awaiting review (with `ai_episode_matching_enabled` on). Click **Try AI match** on a title that won't match.
Expected:
1. Button shows a spinning icon + "Matching…" and is disabled while the request is in flight.
2. Afterward, an inline notice appears in the Inspector (yellow "No confident AI match found." for `no_suggestion`, red "AI match failed — check the server log." for `internal_error`) — instead of the previous silent refresh.
3. A successful match still shows the cyan "Suggested: SxxExx" card as before (no notice).

If the backend AI provider isn't configured, the unit/component tests + build are the authoritative automated evidence; record in the PR that the manual check covered the configured path.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note AI-match feedback in review Inspector"
```

- [ ] **Step 5: Final full check before PR**

Run:
```bash
cd frontend && npm run lint && npm run build && npm run test:unit
```
Expected: all three pass. Then `git checkout package-lock.json` if `npm install` rewrote it earlier and it slipped into staging.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Inline notice for `no_suggestion` / `internal_error` / thrown error → Task 1 (mapping) + Task 2 (render) + Task 3 (catch path).
- Success/`cached` keep the suggestion card (no notice) → Task 1 returns null; Task 2 guard `!llmSuggestion && llmFeedback`.
- Pending "Matching…" state → Task 2 (button) + Task 3 (`isLlmMatching` wiring).
- Title-keyed feedback/pending → Task 3 state maps + prop selectors.
- No backend changes → confirmed; no backend task.
- Verification (lint/build/manual) + changelog → Task 4.

**Placeholder scan:** Only `#NNN` in the changelog, which is intentional (filled at PR time) and called out. No other placeholders.

**Type consistency:** `LLMFeedback { tone: SvNoticeTone; text: string }` defined in Task 1 is the exact shape constructed in Task 3's catch path and consumed by Task 2's prop type and render. `llmResultToFeedback` signature matches `LLMMatchResult` from `api/client.ts`. Inspector prop names (`llmFeedback`, `isLlmMatching`) are identical across Task 2 (definition) and Task 3 (call site).

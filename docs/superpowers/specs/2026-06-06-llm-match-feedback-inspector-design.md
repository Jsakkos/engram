# Inline AI-match feedback in the Review Inspector

**Date:** 2026-06-06
**Status:** Design — pending user approval
**Follow-up to:** PR #347 (restored the broken import so the Inspector's "Try AI match" button works again)

## Problem

The Review Inspector's **Try AI match** button gives the user no signal when the
AI matcher fails or finds nothing.

- `handleTryLLMMatch` (`frontend/src/components/ReviewQueue.tsx`, ~line 376) calls
  `runLLMMatch(...)`, **discards the return value**, and refreshes the job. It relies
  on a `try/catch` to surface errors.
- The backend endpoint `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`
  (`backend/app/api/routes.py`, `llm_match_title` ~line 3405) **always returns
  HTTP 200**, with a `reason` field:
  - `null` → success (a `suggestion` is present)
  - `"cached"` → already had a suggestion
  - `"no_suggestion"` → ran but found nothing confident
  - `"internal_error"` → an exception was caught server-side
- Because the response is always 200, the `try/catch` never fires on a logical
  failure. On `no_suggestion` / `internal_error` the UI silently refreshes and
  shows nothing — the failure only appears in the server log.

Secondary gap: the AI match runs Whisper transcription and can take **1–3 minutes**,
during which the button also gives no signal that anything is happening.

## Goal

Read `reason` from the `runLLMMatch` result and surface user-facing feedback
**inline in the Inspector** — in the same slot the successful suggestion uses — plus
a lightweight in-flight "running…" state on the button.

## Chosen direction

**Inline in the Inspector** (chosen over a top page banner and a transient toast via
mockup review on 2026-06-06).

Rationale: a *successful* AI match already surfaces inside the Inspector as the cyan
"Suggested: SxxExx" card. Putting the *failure* message in the same slot makes
success and failure symmetric and keeps the feedback next to the button the user just
clicked, rather than at the top of a potentially-scrolled-away page.

This is frontend-only. No backend changes — the `reason` values consumed are exactly
what `llm_match_title` returns today.

## Design

### 1. State — `frontend/src/components/ReviewQueue.tsx`

Two maps keyed by title id, so feedback/pending follow the title they belong to when
the user switches the selected title:

```ts
const [llmFeedback, setLlmFeedback] =
  useState<Record<number, { tone: SvNoticeTone; text: string } | null>>({});
const [llmMatchingId, setLlmMatchingId] = useState<number | null>(null);
```

`SvNoticeTone` is imported from `../app/components/synapse` (already exported there).

### 2. Handler — `handleTryLLMMatch` (~line 376)

Stop discarding the result; map `reason` to feedback and manage the pending flag:

```ts
const handleTryLLMMatch = async (titleId: number) => {
    if (!jobId) return;
    setError(null);
    setLlmFeedback((prev) => ({ ...prev, [titleId]: null }));
    setLlmMatchingId(titleId);
    try {
        const result = await runLLMMatch(parseInt(jobId), titleId);
        await fetchJobDetails();
        // success (reason null) and "cached" both surface the suggestion card →
        // no inline message needed. Only the silent reasons get a notice.
        if (result.reason && result.reason !== 'cached' && !result.suggestion) {
            const text =
                result.reason === 'internal_error'
                    ? 'AI match failed — check the server log.'
                    : 'No confident AI match found.';
            const tone: SvNoticeTone =
                result.reason === 'internal_error' ? 'error' : 'warn';
            setLlmFeedback((prev) => ({ ...prev, [titleId]: { tone, text } }));
        }
    } catch (err) {
        console.error('LLM match failed', err);
        // Keep all AI-match feedback in one place (the Inspector), not the top banner.
        setLlmFeedback((prev) => ({
            ...prev,
            [titleId]: {
                tone: 'error',
                text: err instanceof Error ? err.message : 'AI match failed.',
            },
        }));
    } finally {
        setLlmMatchingId((cur) => (cur === titleId ? null : cur));
    }
};
```

Notes:
- Any *future* reason that isn't `internal_error` (e.g. `ai_disabled`,
  `not_configured`) falls through to the generic "No confident AI match found."
  message today. Differentiating them is a backend follow-up and out of scope here;
  this design handles the reasons that exist now without breaking on new ones.
- The `finally` only clears the pending flag if it still points at this title, so a
  fast second click on another title can't clear the wrong spinner.

### 3. Prop into the Inspector (~line 1052)

```tsx
llmFeedback={llmFeedback[selectedTitle.id] ?? null}
isLlmMatching={llmMatchingId === selectedTitle.id}
```

### 4. Render — `frontend/src/components/ReviewQueue/Inspector.tsx`

Add to the prop type:

```ts
llmFeedback: { tone: SvNoticeTone; text: string } | null;
isLlmMatching: boolean;
```

(`SvNoticeTone` imported from `../../app/components/synapse`.)

**a. Inline notice** — in the LLM-suggestion area (~line 163), render the feedback
when present and no suggestion exists, in the same slot as the success card:

```tsx
{!llmSuggestion && llmFeedback && (
    <div style={{ marginBottom: 14 }}>
        <SvNotice tone={llmFeedback.tone}>› {llmFeedback.text}</SvNotice>
    </div>
)}
```

**b. Pending state** — the "Try AI match" button (~line 354) gains a spinner +
"Matching…" label and disables while in flight, mirroring the existing deep-re-match
spinner idiom (`IcoRetry` + `animate-spin`):

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
            <><IcoRetry size={11} className="animate-spin" /> Matching…</>
        ) : (
            'Try AI match'
        )}
    </SvActionButton>
)}
```

## Wording

| reason | tone | message |
|--------|------|---------|
| `null` / `cached` (suggestion present) | — | cyan suggestion card (existing) |
| `no_suggestion` (and any unknown non-error reason) | warn (yellow) | "No confident AI match found." |
| `internal_error` | error (red) | "AI match failed — check the server log." |
| thrown network/HTTP error | error (red) | error message |

## Out of scope

- Backend changes to differentiate `ai_disabled` / `not_configured` from
  `no_suggestion` (separate follow-up; this design degrades gracefully if they land).
- Re-running an already-cached match (the endpoint is intentionally idempotent).

## Verification

- `cd frontend && npm run lint && npm run build`
- Manual: start backend with `DEBUG=true`, open a review job, click **Try AI match**
  on a title that won't match → confirm the button shows "Matching…" while in flight
  and an inline notice ("No confident AI match found.") appears in the Inspector
  afterward instead of silence.
- `CHANGELOG.md`: add a `### Fixed` bullet under `## [Unreleased]`.

## Files touched

- `frontend/src/components/ReviewQueue.tsx` — state, `handleTryLLMMatch`, two new
  Inspector props.
- `frontend/src/components/ReviewQueue/Inspector.tsx` — prop types, inline notice,
  pending button state.
- `CHANGELOG.md` — `[Unreleased]` entry.

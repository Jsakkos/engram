import { ApiError } from '../../api/client';
import type { LLMMatchResult } from '../../api/client';
import type { SvNoticeTone } from '../../app/components/synapse';

/** Inline feedback shown in the Inspector after a "Try AI match" run. */
export interface LLMFeedback {
    tone: SvNoticeTone;
    text: string;
}

/** Last resort when we cannot say anything specific. Shared by both mappers so a
 *  200 `internal_error` and a thrown 500 never drift into two different wordings
 *  for what is the same outcome to the user. */
const GENERIC_FAILURE = 'AI match failed. Check the server log.';

/** Reason to user-facing message. Only `no_match` means the model actually ran
 *  and was not confident; every other reason is a guard that fired first, so
 *  reporting them all as "no confident match" tells the user the AI ran when it
 *  did not. */
const REASON_MESSAGES: Record<string, string> = {
    ai_disabled: 'AI episode matching is turned off. Enable it in Settings.',
    not_configured: 'No AI API key is set. Add one in Settings, then use Test Connection.',
    no_show: 'This job has no detected show title, so there is nothing to match against.',
    no_season: 'This job has no detected season, so there is nothing to match against.',
    show_not_found: 'The show could not be found on TMDB.',
    no_match: 'No confident AI match found.',
};

/**
 * Map a `runLLMMatch` result to inline Inspector feedback.
 *
 * Returns null when the result surfaces on its own as the cyan suggestion card
 * (a fresh suggestion, or a `"cached"` one). Only the "silent" outcomes, where
 * the endpoint returned HTTP 200 but produced no suggestion, get a notice.
 */
export function llmResultToFeedback(result: LLMMatchResult): LLMFeedback | null {
    if (result.suggestion) return null;
    if (!result.reason || result.reason === 'cached') return null;
    if (result.reason === 'internal_error') {
        return { tone: 'error', text: GENERIC_FAILURE };
    }
    const known = REASON_MESSAGES[result.reason];
    return {
        tone: 'warn',
        text: known ?? `AI match did not produce a suggestion (${result.reason}).`,
    };
}

/** Retryable 503 reasons, for when the body carries no provider message. */
const RETRYABLE_MESSAGES: Record<string, string> = {
    matcher_unavailable: 'The episode matcher is not ready for this show yet. Try again shortly.',
    transcription_failed: 'Could not transcribe this file, so there was nothing to match.',
    llm_error: 'The AI provider call failed. Check the provider key in Settings.',
};

/**
 * Map a thrown `runLLMMatch` error (503 or 500) to Inspector feedback.
 *
 * Prefers the backend's `message`, which the provider-error classifier composed
 * from the provider's own response body. Falls back to a per-reason sentence so
 * the raw JSON body of ApiError.message never reaches the user.
 */
export function llmErrorToFeedback(err: unknown): LLMFeedback {
    if (err instanceof ApiError) {
        try {
            const body = JSON.parse(err.body) as { reason?: string; message?: string };
            if (body.message) return { tone: 'error', text: body.message };
            const known = body.reason ? RETRYABLE_MESSAGES[body.reason] : undefined;
            if (known) return { tone: 'error', text: known };
        } catch {
            // Unparseable body falls through to the generic message below.
        }
    }
    return { tone: 'error', text: 'AI match failed. Check the server log.' };
}

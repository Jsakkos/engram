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

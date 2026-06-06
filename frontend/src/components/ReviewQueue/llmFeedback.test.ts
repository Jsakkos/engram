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

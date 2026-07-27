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

    it.each([
        ['ai_disabled', 'AI episode matching is turned off. Enable it in Settings.'],
        ['not_configured', 'No AI API key is set. Add one in Settings, then use Test Connection.'],
        ['no_show', 'This job has no detected show title, so there is nothing to match against.'],
        ['no_season', 'This job has no detected season, so there is nothing to match against.'],
        ['show_not_found', 'The show could not be found on TMDB.'],
        ['no_match', 'No confident AI match found.'],
    ])('maps reason %s to its own message', (reason, text) => {
        expect(llmResultToFeedback({ suggestion: null, reason })).toEqual({
            tone: 'warn',
            text,
        });
    });

    it('keeps internal_error as an error tone', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'internal_error' })).toEqual({
            tone: 'error',
            text: 'AI match failed. Check the server log.',
        });
    });

    it('falls back to a generic message for an unrecognised reason', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'brand_new_reason' })).toEqual({
            tone: 'warn',
            text: 'AI match did not produce a suggestion (brand_new_reason).',
        });
    });
});

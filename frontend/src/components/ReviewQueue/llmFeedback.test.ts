import { describe, expect, it } from 'vitest';
import { llmErrorToFeedback, llmResultToFeedback } from './llmFeedback';
import { ApiError } from '../../api/client';
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

describe('llmErrorToFeedback', () => {
    it('renders the provider message from a 503 body', () => {
        const err = new ApiError(503, 'Service Unavailable', JSON.stringify({
            suggestion: null, reason: 'llm_error',
            detail: 'no_credits', message: 'This account has no API credits.',
        }));
        expect(llmErrorToFeedback(err)).toEqual({
            tone: 'error', text: 'This account has no API credits.',
        });
    });

    it('falls back to a per-reason sentence when the body has no message', () => {
        const err = new ApiError(503, 'Service Unavailable', JSON.stringify({
            suggestion: null, reason: 'transcription_failed',
        }));
        expect(llmErrorToFeedback(err)).toEqual({
            tone: 'error',
            text: 'Could not transcribe this file, so there was nothing to match.',
        });
    });

    it('never leaks the raw JSON body for an unrecognised reason', () => {
        const err = new ApiError(503, 'Service Unavailable', JSON.stringify({
            suggestion: null, reason: 'something_new',
        }));
        const feedback = llmErrorToFeedback(err);
        expect(feedback.text).not.toContain('{');
        expect(feedback.tone).toBe('error');
    });

    it('handles an unparseable body', () => {
        expect(llmErrorToFeedback(new ApiError(503, 'x', 'not json'))).toEqual({
            tone: 'error', text: 'AI match failed. Check the server log.',
        });
    });

    it('handles a non-ApiError', () => {
        expect(llmErrorToFeedback(new Error('boom'))).toEqual({
            tone: 'error', text: 'AI match failed. Check the server log.',
        });
    });
});

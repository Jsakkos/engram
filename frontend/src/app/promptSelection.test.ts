import { describe, expect, it } from 'vitest';
import {
    classifyPromptJob,
    parseIdentityPrompt,
    pruneDismissedIds,
    selectPromptJobs,
    shouldAutoOpenPrompt,
} from './promptSelection';
import type { Job } from '../types';

function makeJob(overrides: Partial<Job>): Job {
    return {
        id: 1,
        drive_id: 'E:',
        volume_label: 'DISC',
        content_type: 'tv',
        state: 'review_needed',
        current_speed: '',
        eta_seconds: 0,
        progress_percent: 0,
        current_title: 0,
        total_titles: 1,
        error_message: null,
        detected_title: null,
        ...overrides,
    } as Job;
}

describe('selectPromptJobs', () => {
    const unreadable = makeJob({
        id: 3,
        review_reason: 'Disc label unreadable. Please enter the title to continue.',
    });
    const seasonless = makeJob({
        id: 4,
        detected_title: 'Eureka',
        review_reason: 'Show identified — select a season to continue.',
    });

    it('surfaces the name prompt for an unreadable label', () => {
        const { namePromptJob } = selectPromptJobs([unreadable], new Set());
        expect(namePromptJob?.id).toBe(3);
    });

    it('does not re-surface a dismissed name prompt', () => {
        const { namePromptJob } = selectPromptJobs([unreadable], new Set([3]));
        expect(namePromptJob).toBeNull();
    });

    it('surfaces the season prompt and honors dismissal', () => {
        expect(selectPromptJobs([seasonless], new Set()).seasonPromptJob?.id).toBe(4);
        expect(selectPromptJobs([seasonless], new Set([4])).seasonPromptJob).toBeNull();
    });

    it('skips a dismissed job but surfaces the next undismissed one', () => {
        const second = makeJob({
            id: 9,
            review_reason: 'Disc label unreadable. Please enter the title to continue.',
        });
        const { namePromptJob } = selectPromptJobs([unreadable, second], new Set([3]));
        expect(namePromptJob?.id).toBe(9);
    });

    it('ignores review_reason on jobs that are not in review', () => {
        const ripping = makeJob({ id: 5, state: 'ripping', review_reason: 'label unreadable' });
        expect(selectPromptJobs([ripping], new Set()).namePromptJob).toBeNull();
    });

    it('surfaces a live identity prompt on a RIPPING job (walk-away Phase B)', () => {
        const rippingWithPrompt = makeJob({
            id: 6,
            state: 'ripping',
            identity_prompt_json: JSON.stringify({
                kind: 'name',
                reason: 'Disc label unreadable.',
            }),
        });
        expect(selectPromptJobs([rippingWithPrompt], new Set()).namePromptJob?.id).toBe(6);
    });

    it('honors dismissal for a ripping prompt job', () => {
        const rippingWithPrompt = makeJob({
            id: 6,
            state: 'ripping',
            identity_prompt_json: JSON.stringify({ kind: 'season', reason: 'Pick a season.' }),
        });
        expect(selectPromptJobs([rippingWithPrompt], new Set([6])).seasonPromptJob).toBeNull();
    });

    it('surfaces a reidentify prompt in its own slot', () => {
        const collision = makeJob({
            id: 7,
            state: 'ripping',
            identity_prompt_json: JSON.stringify({
                kind: 'reidentify',
                reason: 'Multiple shows share this name.',
            }),
        });
        const { namePromptJob, seasonPromptJob, reidentifyPromptJob } = selectPromptJobs(
            [collision],
            new Set(),
        );
        expect(reidentifyPromptJob?.id).toBe(7);
        expect(namePromptJob).toBeNull();
        expect(seasonPromptJob).toBeNull();
    });
});

describe('parseIdentityPrompt', () => {
    const withPrompt = (identity_prompt_json: string | null | undefined) =>
        makeJob({ identity_prompt_json });

    it('parses each known kind', () => {
        for (const kind of ['name', 'season', 'reidentify'] as const) {
            const job = withPrompt(JSON.stringify({ kind, reason: 'why' }));
            expect(parseIdentityPrompt(job)).toEqual({ kind, reason: 'why' });
        }
    });

    it('returns null for absent, null, and "" (the WS clear sentinel)', () => {
        expect(parseIdentityPrompt(withPrompt(undefined))).toBeNull();
        expect(parseIdentityPrompt(withPrompt(null))).toBeNull();
        expect(parseIdentityPrompt(withPrompt(''))).toBeNull();
    });

    it('returns null for malformed JSON without throwing', () => {
        expect(parseIdentityPrompt(withPrompt('{not json'))).toBeNull();
    });

    it('returns null for non-object payloads', () => {
        expect(parseIdentityPrompt(withPrompt('"name"'))).toBeNull();
        expect(parseIdentityPrompt(withPrompt('42'))).toBeNull();
        expect(parseIdentityPrompt(withPrompt('null'))).toBeNull();
    });

    it('returns null for an unknown kind', () => {
        expect(
            parseIdentityPrompt(withPrompt(JSON.stringify({ kind: 'mystery', reason: 'x' }))),
        ).toBeNull();
    });

    it('tolerates a missing reason', () => {
        expect(parseIdentityPrompt(withPrompt(JSON.stringify({ kind: 'name' })))).toEqual({
            kind: 'name',
            reason: '',
        });
    });
});

describe('classifyPromptJob', () => {
    it('classifies an unreadable label (no detected title) as a name prompt', () => {
        // makeJob defaults detected_title to absent — the unreadable-label case.
        const job = makeJob({
            review_reason: 'Disc label unreadable. Please enter the title to continue.',
        });
        expect(classifyPromptJob(job)).toBe('name');
    });

    it('classifies a merged-without-separators TV label as a name prompt', () => {
        const job = makeJob({
            review_reason: 'Disc label merged without separators — confirm the show.',
            content_type: 'tv',
        });
        expect(classifyPromptJob(job)).toBe('name');
    });

    it('classifies a season-unknown reason as a season prompt', () => {
        const job = makeJob({
            detected_title: 'Eureka',
            review_reason: 'Show identified — select a season to continue.',
        });
        expect(classifyPromptJob(job)).toBe('season');
    });

    it('returns null for a job that needs no identify prompt', () => {
        const job = makeJob({ review_reason: 'Low-confidence episode matches need review.' });
        expect(classifyPromptJob(job)).toBeNull();
    });

    it('does not treat an unreadable label with a detected title as a name prompt', () => {
        const job = makeJob({
            review_reason: 'Disc label unreadable. Please enter the title to continue.',
            detected_title: 'Already Known',
        });
        expect(classifyPromptJob(job)).toBeNull();
    });

    it('classifies a live identity prompt on a ripping job for all three kinds', () => {
        for (const kind of ['name', 'season', 'reidentify'] as const) {
            const job = makeJob({
                state: 'ripping',
                identity_prompt_json: JSON.stringify({ kind, reason: 'open question' }),
            });
            expect(classifyPromptJob(job)).toBe(kind);
        }
    });

    it('classifies a live identity prompt on a stall-parked review job', () => {
        const job = makeJob({
            state: 'review_needed',
            identity_prompt_json: JSON.stringify({ kind: 'reidentify', reason: 'twins' }),
        });
        expect(classifyPromptJob(job)).toBe('reidentify');
    });

    it('prefers the identity prompt over review_reason when both are present', () => {
        const job = makeJob({
            state: 'review_needed',
            identity_prompt_json: JSON.stringify({ kind: 'season', reason: 'pick one' }),
            review_reason: 'Disc label unreadable. Please enter the title to continue.',
        });
        expect(classifyPromptJob(job)).toBe('season');
    });

    it('does not surface a leftover prompt on states outside ripping/review (e.g. matching)', () => {
        const job = makeJob({
            state: 'matching',
            identity_prompt_json: JSON.stringify({ kind: 'season', reason: 'pick one' }),
        });
        expect(classifyPromptJob(job)).toBeNull();
    });

    it('falls back to review_reason when the prompt is malformed or unknown-kind', () => {
        const malformed = makeJob({
            identity_prompt_json: '{broken',
            review_reason: 'Disc label unreadable. Please enter the title to continue.',
        });
        expect(classifyPromptJob(malformed)).toBe('name');

        const unknownKind = makeJob({
            identity_prompt_json: JSON.stringify({ kind: 'mystery', reason: 'x' }),
            review_reason: 'Show identified — select a season to continue.',
        });
        expect(classifyPromptJob(unknownKind)).toBe('season');
    });

    it('returns null for a ripping job with a malformed prompt and no review fallback', () => {
        const job = makeJob({ state: 'ripping', identity_prompt_json: '{broken' });
        expect(classifyPromptJob(job)).toBeNull();
    });
});

describe('shouldAutoOpenPrompt', () => {
    const promptJob = makeJob({
        id: 3,
        review_reason: 'Disc label unreadable. Please enter the title to continue.',
    });

    it('auto-opens when the prompt job is the only job', () => {
        expect(shouldAutoOpenPrompt(promptJob, [promptJob])).toBe(true);
    });

    it('auto-opens when every other job has reached a terminal state', () => {
        const done = makeJob({ id: 1, state: 'completed' });
        const failed = makeJob({ id: 2, state: 'failed' });
        // The walk-away happy path: stale done/failed cards from earlier jobs
        // must not suppress the waiting prompt.
        expect(shouldAutoOpenPrompt(promptJob, [done, failed, promptJob])).toBe(true);
    });

    it('does NOT auto-open while another job is actively ripping', () => {
        const ripping = makeJob({ id: 1, state: 'ripping' });
        // The P13 scenario: the user is watching another disc rip — don't steal
        // focus with a blocking modal; surface the card CTA instead.
        expect(shouldAutoOpenPrompt(promptJob, [ripping, promptJob])).toBe(false);
    });

    it('does NOT auto-open while another job is still in review', () => {
        const otherReview = makeJob({ id: 1, state: 'review_needed' });
        expect(shouldAutoOpenPrompt(promptJob, [otherReview, promptJob])).toBe(false);
    });

    it('does NOT auto-open while the prompt-bearing job is itself ripping (CTA only mid-rip)', () => {
        // A disc rips with an open identity question (walk-away Phase B). The
        // rip is already running, so a blocking modal that looks like the old
        // pre-rip gate ("Start Ripping →" / "Awaiting Input") misrepresents the
        // state and invites an accidental Cancel. Surface the on-card CTA
        // instead; the modal auto-opens once the job parks for review at rip-end.
        const rippingPrompt = makeJob({
            id: 8,
            state: 'ripping',
            identity_prompt_json: JSON.stringify({ kind: 'name', reason: 'unreadable' }),
        });
        const done = makeJob({ id: 1, state: 'completed' });
        expect(shouldAutoOpenPrompt(rippingPrompt, [done, rippingPrompt])).toBe(false);
    });

    it('does NOT auto-open a ripping prompt while another disc is active', () => {
        const rippingPrompt = makeJob({
            id: 8,
            state: 'ripping',
            identity_prompt_json: JSON.stringify({ kind: 'name', reason: 'unreadable' }),
        });
        const otherRipping = makeJob({ id: 9, state: 'ripping' });
        expect(shouldAutoOpenPrompt(rippingPrompt, [otherRipping, rippingPrompt])).toBe(false);
    });

    it('auto-opens a review-parked prompt job that carries a live prompt when alone', () => {
        // The rip-end convergence (B4) parks an unanswered prompt as a
        // REVIEW_NEEDED job still carrying identity_prompt_json. THAT is the
        // genuine "waiting for you" moment, so it auto-opens when it is the
        // only active job — preserving the single-disc walk-away guarantee.
        const parkedPrompt = makeJob({
            id: 8,
            state: 'review_needed',
            identity_prompt_json: JSON.stringify({ kind: 'name', reason: 'unreadable' }),
        });
        expect(shouldAutoOpenPrompt(parkedPrompt, [parkedPrompt])).toBe(true);
    });
});

describe('pruneDismissedIds', () => {
    it('drops ids whose jobs are gone so a recycled id is not silently suppressed', () => {
        // DEBUG reset-all-jobs resets SQLite auto-increment: a fresh job can
        // reuse a previously-dismissed id. Pruning ids absent from the job list
        // keeps the dismissal memory scoped to jobs that still exist.
        const dismissed = new Set([3, 9]);
        pruneDismissedIds(dismissed, [makeJob({ id: 3 })]);
        expect(dismissed.has(3)).toBe(true);
        expect(dismissed.has(9)).toBe(false);
    });
});

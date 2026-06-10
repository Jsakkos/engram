import { describe, expect, it } from 'vitest';
import {
    classifyPromptJob,
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

    it('ignores jobs that are not in review', () => {
        const ripping = makeJob({ id: 5, state: 'ripping', review_reason: 'label unreadable' });
        expect(selectPromptJobs([ripping], new Set()).namePromptJob).toBeNull();
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

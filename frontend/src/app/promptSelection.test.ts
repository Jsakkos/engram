import { describe, expect, it } from 'vitest';
import { selectPromptJobs } from './promptSelection';
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

import { describe, expect, it } from 'vitest';
import type { DiscTitle, Job } from '../../types';
import { isIdentityUnresolved } from './identity';

function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: 1,
        state: 'review_needed',
        content_type: 'tv',
        detected_title: 'Frasier',
        tmdb_id: 3452,
        ...overrides,
    } as Job;
}

function makeTitle(state: DiscTitle['state']): DiscTitle {
    return { id: 1, job_id: 1, title_index: 1, state } as DiscTitle;
}

const twins = JSON.stringify([
    { tmdb_id: 1, name: 'Frasier', year: '1993' },
    { tmdb_id: 2, name: 'Frasier', year: '2023' },
]);

describe('isIdentityUnresolved', () => {
    it('is true when the show has no tmdb_id', () => {
        expect(isIdentityUnresolved(makeJob({ tmdb_id: null }), [])).toBe(true);
    });

    it('is false once the show is confirmed', () => {
        expect(isIdentityUnresolved(makeJob(), [makeTitle('review')])).toBe(false);
    });

    it('is true for same-name twins while nothing has been processed', () => {
        const job = makeJob({ candidates_json: twins });
        expect(isIdentityUnresolved(job, [makeTitle('pending')])).toBe(true);
    });

    it('lets a post-rip wrong-show disc through even with twins', () => {
        const job = makeJob({ candidates_json: twins });
        expect(isIdentityUnresolved(job, [makeTitle('matched')])).toBe(false);
    });

    it('is false outside review_needed', () => {
        expect(isIdentityUnresolved(makeJob({ state: 'ripping', tmdb_id: null }), [])).toBe(false);
    });

    it('survives malformed candidates_json', () => {
        const job = makeJob({ candidates_json: '{not json' });
        expect(isIdentityUnresolved(job, [])).toBe(false);
    });

    describe('heuristic-only mode (no TMDB token)', () => {
        it('treats a typed show name as confirmed', () => {
            const job = makeJob({ tmdb_id: null });
            expect(isIdentityUnresolved(job, [], { tmdbConfigured: false })).toBe(false);
        });

        it('still gates a disc with no usable name', () => {
            const job = makeJob({ tmdb_id: null, detected_title: '  ' });
            expect(isIdentityUnresolved(job, [], { tmdbConfigured: false })).toBe(true);
        });

        it('keeps gating when TMDB is configured', () => {
            const job = makeJob({ tmdb_id: null });
            expect(isIdentityUnresolved(job, [], { tmdbConfigured: true })).toBe(true);
        });
    });
});

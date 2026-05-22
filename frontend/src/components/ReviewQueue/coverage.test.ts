import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import {
    computeCoverage,
    suggestGapCode,
    collidingCodes,
    buildCandidates,
    type RosterEpisode,
} from './coverage';

const episodes: RosterEpisode[] = [
    { episode_code: 'S03E01', episode_number: 1, name: 'Polaris' },
    { episode_code: 'S03E02', episode_number: 2, name: 'Game Changer' },
    { episode_code: 'S03E03', episode_number: 3, name: 'All In' },
    { episode_code: 'S03E04', episode_number: 4, name: 'Happy Valley' },
    { episode_code: 'S03E05', episode_number: 5, name: 'Seven Minutes of Terror' },
    { episode_code: 'S03E06', episode_number: 6, name: 'New Eden' },
];

describe('computeCoverage', () => {
    it('marks assigned, duplicate, missing-in-range, and off', () => {
        const cov = computeCoverage(
            { 10: 'S03E01', 11: 'S03E02', 12: 'S03E05', 13: 'S03E05' },
            episodes,
        );
        expect(cov['S03E01'].status).toBe('assigned');
        expect(cov['S03E05'].status).toBe('duplicate');
        expect([...cov['S03E05'].titleIds].sort()).toEqual([12, 13]);
        expect(cov['S03E03'].status).toBe('missing'); // gap inside range 1..5
        expect(cov['S03E04'].status).toBe('missing');
        expect(cov['S03E06'].status).toBe('off'); // outside covered range
    });

    it('ignores extra/skip pseudo-selections', () => {
        const cov = computeCoverage({ 10: 'extra', 11: 'skip', 12: 'S03E02' }, episodes);
        expect(cov['S03E02'].status).toBe('assigned');
        expect(cov['S03E01'].status).toBe('off'); // only E02 present → range 2..2
    });
});

describe('suggestGapCode', () => {
    it('returns the lowest missing episode inside the covered range', () => {
        expect(suggestGapCode({ 10: 'S03E01', 12: 'S03E05' }, episodes)).toBe('S03E02');
    });
    it('returns null when there is no gap', () => {
        expect(suggestGapCode({ 10: 'S03E01', 11: 'S03E02' }, episodes)).toBeNull();
    });
});

describe('collidingCodes', () => {
    it('flags episodes claimed by more than one title', () => {
        const set = collidingCodes({ 10: 'S03E05', 11: 'S03E05', 12: 'S03E01' });
        expect(set.has('S03E05')).toBe(true);
        expect(set.has('S03E01')).toBe(false);
    });
});

describe('buildCandidates', () => {
    it('orders best match first then runner_ups, attaching episode names', () => {
        const title = {
            matched_episode: 'S03E05',
            match_confidence: 0.54,
            match_details: JSON.stringify({
                vote_count: 4,
                runner_ups: [
                    { episode: 'S03E04', confidence: 0.49, vote_count: 4 },
                    { episode: 'S03E03', confidence: 0.31, vote_count: 2 },
                ],
            }),
        } as unknown as DiscTitle;
        const names: Record<string, string> = {
            S03E05: 'Seven Minutes of Terror',
            S03E04: 'Happy Valley',
            S03E03: 'All In',
        };
        const cands = buildCandidates(title, (code) => names[code] ?? '');
        expect(cands.map((c) => c.episodeCode)).toEqual(['S03E05', 'S03E04', 'S03E03']);
        expect(cands[0].isBest).toBe(true);
        expect(cands[0].episodeName).toBe('Seven Minutes of Terror');
        expect(cands[1].voteCount).toBe(4);
    });
});

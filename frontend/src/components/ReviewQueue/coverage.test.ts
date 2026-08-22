import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import {
    buildRangeCode,
    computeCoverage,
    displayEpisodeCode,
    suggestGapCode,
    collidingCodes,
    buildCandidates,
    episodeParts,
    inferSpan,
    isMultiEpisode,
    normalizeEpisodeCode,
    parseEpisodeCode,
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

    it('normalizes unpadded codes to canonical zero-padded form', () => {
        const title = {
            matched_episode: 'S3E5',
            match_confidence: 0.18,
            match_details: JSON.stringify({ runner_ups: [{ episode: 'S3E4', confidence: 0.1 }] }),
        } as unknown as DiscTitle;
        const names: Record<string, string> = { S03E05: 'Charity Drive' };
        const cands = buildCandidates(title, (code) => names[code] ?? '');
        expect(cands[0].episodeCode).toBe('S03E05');
        expect(cands[0].episodeName).toBe('Charity Drive'); // name lookup uses padded code
        expect(cands[1].episodeCode).toBe('S03E04');
    });
});

describe('episode-code normalization across padding', () => {
    it('treats unpadded and padded selections as the same episode (collision)', () => {
        // Real-world: matcher emitted "S3E5" for one title, "S03E05" for another.
        const cov = computeCoverage({ 10: 'S3E5', 11: 'S03E05' }, episodes);
        expect(cov['S03E05'].status).toBe('duplicate');
        expect([...cov['S03E05'].titleIds].sort()).toEqual([10, 11]);
    });

    it('collidingCodes flags the collision regardless of padding', () => {
        const set = collidingCodes({ 10: 'S3E5', 11: 'S03E05' });
        expect(set.has('S03E05')).toBe(true);
    });
});

describe('combined episode codes', () => {
    // One track holding several episodes — a segment-format show's DVD carries
    // three ~7min segments as one 22min title.
    it('reads a hyphen as a range and a run-on as a set', () => {
        // Plex/Jellyfin semantics, and what hand-organized libraries mean.
        expect(parseEpisodeCode('S01E01-E03')).toEqual({ season: 1, episodes: [1, 2, 3] });
        expect(parseEpisodeCode('S01E01-E02-E03')).toEqual({ season: 1, episodes: [1, 2, 3] });
        expect(parseEpisodeCode('S01E01E03')).toEqual({ season: 1, episodes: [1, 3] });
        expect(parseEpisodeCode('s1e7')).toEqual({ season: 1, episodes: [7] });
    });

    it('rejects pseudo-codes and junk', () => {
        for (const code of ['extra', 'skip', '', 'S01', 'S01E01 extra']) {
            expect(parseEpisodeCode(code)).toBeNull();
        }
    });

    it('canonicalizes every spelling to one key', () => {
        expect(normalizeEpisodeCode('s1e1-e2')).toBe('S01E01-E02');
        expect(normalizeEpisodeCode('S01E01E02')).toBe('S01E01-E02');
        expect(normalizeEpisodeCode('S01E01-E02-E03')).toBe('S01E01-E03');
        // A gapped set keeps the run-on form — a hyphen would claim E02.
        expect(normalizeEpisodeCode('S01E01E03')).toBe('S01E01E03');
        expect(normalizeEpisodeCode('extra')).toBe('extra');
    });

    it('expands to the episodes it claims', () => {
        expect(episodeParts('S03E01-E03')).toEqual(['S03E01', 'S03E02', 'S03E03']);
        expect(episodeParts('S03E01E03')).toEqual(['S03E01', 'S03E03']);
        expect(episodeParts('S03E01')).toEqual(['S03E01']);
        expect(isMultiEpisode('S03E01-E02')).toBe(true);
        expect(isMultiEpisode('S03E01')).toBe(false);
    });

    it('fills every roster slot a combined track claims', () => {
        const cov = computeCoverage({ 10: 'S03E01-E03' }, episodes);
        expect(cov['S03E01'].status).toBe('assigned');
        expect(cov['S03E02'].status).toBe('assigned');
        expect(cov['S03E03'].status).toBe('assigned');
        expect(cov['S03E01'].titleIds).toEqual([10]);
    });

    it('collides with another track claiming one of its episodes', () => {
        const collisions = collidingCodes({ 10: 'S03E01-E02', 11: 'S03E02' });
        expect(collisions.has('S03E02')).toBe(true);
        expect(collisions.has('S03E01')).toBe(false);
    });

    it('builds a range code from a first episode and a span', () => {
        expect(buildRangeCode(1, 1, 3)).toBe('S01E01-E03');
        expect(buildRangeCode(12, 7, 1)).toBe('S12E07');
    });
});

describe('displayEpisodeCode', () => {
    it('shows a contiguous run as the compact range the file will use', () => {
        expect(displayEpisodeCode('S01E01-E02-E03')).toBe('S01E01-E03');
    });

    it('keeps a gapped set in the run-on form so it claims nothing extra', () => {
        expect(displayEpisodeCode('S01E01E03')).toBe('S01E01E03');
    });

    it('leaves single episodes and pseudo-codes alone', () => {
        expect(displayEpisodeCode('S01E01')).toBe('S01E01');
        expect(displayEpisodeCode('extra')).toBe('extra');
    });
});

describe('inferSpan', () => {
    const segments: RosterEpisode[] = [1, 2, 3, 4].map((n) => ({
        episode_code: `S01E0${n}`,
        episode_number: n,
        name: `Segment ${n}`,
        runtime: 7,
    }));

    it('reads a 22min track against 7min segments as three episodes', () => {
        expect(inferSpan(22.3 * 60, segments)).toBe(3);
    });

    it('leaves an ordinary episode alone', () => {
        const normal: RosterEpisode[] = [
            { episode_code: 'S01E01', episode_number: 1, name: 'One', runtime: 44 },
        ];
        expect(inferSpan(46 * 60, normal)).toBe(1);
    });

    it('claims nothing without runtimes or a duration', () => {
        expect(inferSpan(1340, episodes)).toBe(1);
        expect(inferSpan(null, segments)).toBe(1);
    });

    it('allows for the credits a DVD track carries and TMDB omits', () => {
        // The real regression: a 24.6min block of three 7min segments. Rounding
        // its 3.5 ratio picked 4 and rejected it, so the one track that most
        // needed a span offered none.
        expect(inferSpan(24.65 * 60, segments)).toBe(3);
    });

    it('does not guess for a track shorter than two whole episodes', () => {
        expect(inferSpan(9.8 * 60, segments)).toBe(1);
    });

    it('considers every runtime a season lists, not just the commonest', () => {
        // A season can list 7min segments alongside 11min ones; a 22min track is
        // two of the latter as readily as three of the former.
        const mixed: RosterEpisode[] = [
            { episode_code: 'S01E01', episode_number: 1, name: 'a', runtime: 7 },
            { episode_code: 'S01E02', episode_number: 2, name: 'b', runtime: 7 },
            { episode_code: 'S01E03', episode_number: 3, name: 'c', runtime: 11 },
        ];
        expect(inferSpan(22.2 * 60, mixed)).toBe(2);
    });
});

import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import { buildInitialSelections, isHeldMovieExtra, movieReviewNotice, movieReviewTitles } from './utils';

describe('movie extras in review (#773)', () => {
    const title = (id: number, state: DiscTitle['state'], is_extra: boolean): DiscTitle =>
        ({ id, state, is_extra } as DiscTitle);

    it('treats only an extra in review as held for a keep/discard choice', () => {
        expect(isHeldMovieExtra(title(1, 'review', true))).toBe(true);
        // A feature parked on a library conflict is in review but is no extra.
        expect(isHeldMovieExtra(title(2, 'review', false))).toBe(false);
        expect(isHeldMovieExtra(title(3, 'matched', true))).toBe(false);
    });

    it('drops extras the skip policy already settled from the version picker', () => {
        const kept = movieReviewTitles([
            title(1, 'matched', false),
            title(2, 'completed', true),
            title(3, 'skipped', false),
            title(4, 'review', true),
            title(5, 'matched', true),
            { ...title(6, 'failed', true), matched_episode: 'skip' },
        ]);
        expect(kept.map((t) => t.id)).toEqual([1, 4, 5]);
    });

    it('keeps a title whose rip failed, so its re-rip notice still shows', () => {
        expect(movieReviewTitles([title(1, 'failed', false)])).toHaveLength(1);
    });
});

describe('movie review after a Select deleted the other rips (#812)', () => {
    const title = (id: number, state: DiscTitle['state'], is_extra: boolean, match_details?: string): DiscTitle =>
        ({ id, state, is_extra, match_details } as DiscTitle);
    const removed = (id: number) => title(id, 'failed', true, JSON.stringify({ reason: 'Unselected by user' }));

    it('drops titles whose file the Select cleanup removed', () => {
        const kept = movieReviewTitles([title(1, 'review', false), removed(2), removed(3)]);
        expect(kept.map((t) => t.id)).toEqual([1]);
    });

    it('says so when nothing is left to select, instead of asking for a version', () => {
        expect(movieReviewNotice([removed(1), removed(2)])).toBe('nothing_selectable');
        // Held extras are kept or discarded, never selected as the feature.
        expect(movieReviewNotice([title(1, 'review', true), removed(2)])).toBe('nothing_selectable');
    });

    it('keeps the existing notices when there is still a choice', () => {
        const conflict = title(1, 'review', false, JSON.stringify({ error: 'file_exists' }));
        expect(movieReviewNotice([conflict, removed(2)])).toBe('conflict');
        expect(movieReviewNotice([title(1, 'matched', false), title(2, 'review', true)])).toBe('held_extras');
        expect(movieReviewNotice([title(1, 'matched', false), title(2, 'matched', false)])).toBe('pick_version');
    });
});

const t = (id: number, matched_episode: string | null): DiscTitle =>
    ({ id, matched_episode } as DiscTitle);

describe('buildInitialSelections', () => {
    it('pre-fills a deferred extra as the "extra" action', () => {
        const { episodes, actions } = buildInitialSelections([t(1, 'extra')]);
        expect(episodes[1]).toBe('extra');
        expect(actions[1]).toBe('extra');
    });

    it('pre-fills a matched episode as an "episode" action, canonicalized', () => {
        const { episodes, actions } = buildInitialSelections([t(2, 'S1E3')]);
        expect(episodes[2]).toBe('S01E03');
        expect(actions[2]).toBe('episode');
    });

    it('omits unmatched titles', () => {
        const { episodes, actions } = buildInitialSelections([t(3, null)]);
        expect(episodes[3]).toBeUndefined();
        expect(actions[3]).toBeUndefined();
    });
});

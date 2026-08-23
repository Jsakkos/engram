import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import { buildInitialSelections, reviewSubtitle } from './utils';

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

describe('reviewSubtitle', () => {
    it('names the disc as well as the show and season', () => {
        // Every disc in a box set shares a show and season, so those alone cannot
        // say which disc is on screen.
        expect(
            reviewSubtitle({
                detected_title: "A Cartoon",
                volume_label: 'A_CARTOON_S2_D8',
                detected_season: 2,
            }),
        ).toBe('› A Cartoon / SEASON 2 · A_CARTOON_S2_D8');
    });

    it('omits the season when the disc has none', () => {
        expect(reviewSubtitle({ detected_title: 'A Film', volume_label: 'A_FILM_2011' })).toBe(
            '› A Film · A_FILM_2011',
        );
    });

    it('does not repeat the label when it is standing in for the show name', () => {
        expect(reviewSubtitle({ volume_label: 'UNKNOWN_DISC', detected_season: 1 })).toBe(
            '› UNKNOWN_DISC / SEASON 1',
        );
    });
});

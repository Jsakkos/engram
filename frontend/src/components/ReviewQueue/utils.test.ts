import { describe, it, expect } from 'vitest';
import type { DiscTitle } from '../../types';
import { buildInitialSelections } from './utils';

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

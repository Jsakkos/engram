import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SeasonRosterStrip } from './SeasonRosterStrip';
import type { CoverageEntry } from './coverage';
import type { RosterEpisode } from './types';

function ep(overrides: Partial<RosterEpisode>): RosterEpisode {
    return {
        episode_code: 'S02E05',
        episode_number: 5,
        name: 'The New Girl',
        has_reference: true,
        ...overrides,
    };
}

function renderStrip(episodes: RosterEpisode[]) {
    const coverage: Record<string, CoverageEntry> = Object.fromEntries(
        episodes.map((e) => [e.episode_code, { status: 'missing', titleIds: [] }]),
    );
    return render(
        <SeasonRosterStrip
            episodes={episodes}
            coverage={coverage}
            suggestedCode={null}
            titleIndexById={{}}
        />,
    );
}

describe('SeasonRosterStrip reference flag', () => {
    it('marks an episode with no reference subtitle', () => {
        renderStrip([ep({ episode_code: 'S02E05', episode_number: 5, has_reference: false })]);
        // The warning glyph carries an accessible label.
        expect(screen.getByLabelText('No reference subtitle')).toBeInTheDocument();
    });

    it('does not flag an episode that has a reference', () => {
        renderStrip([ep({ episode_code: 'S02E04', episode_number: 4, has_reference: true })]);
        expect(screen.queryByLabelText('No reference subtitle')).not.toBeInTheDocument();
    });

    it('does not flag when reference availability is unknown (undefined)', () => {
        renderStrip([ep({ episode_code: 'S02E03', episode_number: 3, has_reference: undefined })]);
        expect(screen.queryByLabelText('No reference subtitle')).not.toBeInTheDocument();
    });
});

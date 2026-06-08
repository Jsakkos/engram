import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ReIdentifyModal from './ReIdentifyModal';
import type { Job } from '../types';

/** Build a minimal valid Job, overriding only the fields a test cares about. */
function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: 1,
        drive_id: 'E:',
        volume_label: 'FRASIER_S1D1',
        content_type: 'tv',
        state: 'review_needed',
        current_speed: '',
        eta_seconds: 0,
        progress_percent: 0,
        current_title: 0,
        total_titles: 0,
        error_message: null,
        detected_title: 'Frasier',
        detected_season: 1,
        ...overrides,
    };
}

const FRASIER_CANDIDATES = JSON.stringify([
    { tmdb_id: 3452, name: 'Frasier', year: '1993', popularity: 75.6 },
    { tmdb_id: 195241, name: 'Frasier', year: '2023', popularity: 5.7 },
]);

describe('ReIdentifyModal quick-pick candidates', () => {
    it('renders a one-click button per same-name candidate when candidates_json has >=2 entries', () => {
        render(
            <ReIdentifyModal
                job={makeJob({ candidates_json: FRASIER_CANDIDATES })}
                onSubmit={vi.fn()}
                onCancel={vi.fn()}
            />,
        );

        // Labels carry the disambiguating year AND the tmdb id.
        expect(screen.getByRole('button', { name: /frasier \(1993\).*#3452/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /frasier \(2023\).*#195241/i })).toBeInTheDocument();
    });

    it('submits the chosen candidate tmdb_id with content_type tv and the detected season', async () => {
        const user = userEvent.setup();
        const onSubmit = vi.fn();
        render(
            <ReIdentifyModal
                job={makeJob({ candidates_json: FRASIER_CANDIDATES, detected_season: 2 })}
                onSubmit={onSubmit}
                onCancel={vi.fn()}
            />,
        );

        await user.click(screen.getByRole('button', { name: /frasier \(2023\)/i }));

        expect(onSubmit).toHaveBeenCalledTimes(1);
        expect(onSubmit).toHaveBeenCalledWith('Frasier', 'tv', 2, 195241);
    });

    it('falls back to season 1 when the job has no detected_season', async () => {
        // A null season serializes to `season: null`, the backend skips updating
        // detected_season, and subtitle re-download is then silently skipped for
        // the TV disc. Mirror the manual form's `|| 1` fallback.
        const user = userEvent.setup();
        const onSubmit = vi.fn();
        render(
            <ReIdentifyModal
                job={makeJob({ candidates_json: FRASIER_CANDIDATES, detected_season: undefined })}
                onSubmit={onSubmit}
                onCancel={vi.fn()}
            />,
        );

        await user.click(screen.getByRole('button', { name: /frasier \(2023\)/i }));

        expect(onSubmit).toHaveBeenCalledWith('Frasier', 'tv', 1, 195241);
    });

    it('does not render a quick-pick section when candidates_json is absent', () => {
        render(
            <ReIdentifyModal job={makeJob()} onSubmit={vi.fn()} onCancel={vi.fn()} />,
        );

        expect(screen.queryByText(/did you mean/i)).not.toBeInTheDocument();
    });

    it('does not render a quick-pick section when only one candidate is present', () => {
        const single = JSON.stringify([
            { tmdb_id: 3452, name: 'Frasier', year: '1993', popularity: 75.6 },
        ]);
        render(
            <ReIdentifyModal
                job={makeJob({ candidates_json: single })}
                onSubmit={vi.fn()}
                onCancel={vi.fn()}
            />,
        );

        expect(screen.queryByText(/did you mean/i)).not.toBeInTheDocument();
    });

    it('does not crash or render a quick-pick when candidates_json is malformed', () => {
        render(
            <ReIdentifyModal
                job={makeJob({ candidates_json: 'not valid json' })}
                onSubmit={vi.fn()}
                onCancel={vi.fn()}
            />,
        );

        expect(screen.queryByText(/did you mean/i)).not.toBeInTheDocument();
        // The free-text search fallback is still available.
        expect(screen.getByPlaceholderText(/search for correct title/i)).toBeInTheDocument();
    });
});

describe('ReIdentifyModal selection + current identity', () => {
    it('shows the selected result year + tmdb id after picking a search hit, and clears it on manual edit', async () => {
        const user = userEvent.setup();
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue({
                ok: true,
                json: async () => ({
                    results: [
                        {
                            tmdb_id: 195241,
                            name: 'Frasier',
                            type: 'tv',
                            year: '2023',
                            poster_path: null,
                            popularity: 5.7,
                        },
                    ],
                }),
            }),
        );
        try {
            render(<ReIdentifyModal job={makeJob()} onSubmit={vi.fn()} onCancel={vi.fn()} />);

            await user.type(
                screen.getByPlaceholderText(/search for correct title/i),
                'frasier',
            );
            // The search is debounced 500ms; findBy polls until the result renders.
            const resultBtn = await screen.findByRole(
                'button',
                { name: /frasier.*tv.*2023/i },
                { timeout: 2000 },
            );
            await user.click(resultBtn);

            expect(screen.getByText(/TMDB #195241/i)).toBeInTheDocument();
            expect(screen.getByText(/\(2023\)/)).toBeInTheDocument();

            // Editing the title manually drops the confirmed-match line.
            await user.clear(screen.getByDisplayValue('Frasier'));
            expect(screen.queryByText(/TMDB #195241/i)).not.toBeInTheDocument();
        } finally {
            vi.unstubAllGlobals();
        }
    });

    it('shows the current identification when the job has a committed tmdb_id', () => {
        render(
            <ReIdentifyModal
                job={makeJob({ tmdb_id: 18409, tmdb_name: 'The Office', tmdb_year: 2005 })}
                onSubmit={vi.fn()}
                onCancel={vi.fn()}
            />,
        );
        expect(screen.getByText(/currently: the office \(2005\)/i)).toBeInTheDocument();
        expect(screen.getByText(/tmdb #18409/i)).toBeInTheDocument();
    });

    it('omits the current identification when tmdb_id is null (ambiguous disc)', () => {
        render(
            <ReIdentifyModal
                job={makeJob({ tmdb_id: null })}
                onSubmit={vi.fn()}
                onCancel={vi.fn()}
            />,
        );
        expect(screen.queryByText(/currently:/i)).not.toBeInTheDocument();
    });
});

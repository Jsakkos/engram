import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SeasonPromptModal from './SeasonPromptModal';
import type { Job } from '../types';

const job: Job = {
    id: 7,
    drive_id: 'D:',
    volume_label: 'EUREKA_D3',
    content_type: 'tv',
    state: 'review_needed',
    current_speed: '',
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 11,
    error_message: null,
    detected_title: 'Eureka',
    // detected_season intentionally absent — the modal is triggered precisely
    // when the season is unknown (undefined satisfies the optional number type).
};

function mockRosterFetch(seasonCount: number | null) {
    vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ available: false, season_count: seasonCount }),
        }),
    );
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('SeasonPromptModal (#370)', () => {
    it('offers one option per season from season_count', async () => {
        mockRosterFetch(5);
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 05' })).toBeInTheDocument(),
        );
        expect(screen.queryByRole('option', { name: 'Season 06' })).not.toBeInTheDocument();
    });

    it('submits the chosen season', async () => {
        mockRosterFetch(5);
        const onSubmit = vi.fn();
        render(<SeasonPromptModal job={job} onSubmit={onSubmit} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 03' })).toBeInTheDocument(),
        );
        fireEvent.change(screen.getByLabelText('Season'), { target: { value: '3' } });
        fireEvent.click(screen.getByRole('button', { name: /continue/i }));
        expect(onSubmit).toHaveBeenCalledWith(3);
    });

    it('submits undefined for "match across all seasons"', async () => {
        mockRosterFetch(5);
        const onSubmit = vi.fn();
        render(<SeasonPromptModal job={job} onSubmit={onSubmit} onCancel={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: /all seasons/i }));
        expect(onSubmit).toHaveBeenCalledWith(undefined);
    });

    it('falls back to 15 season options when the count is unavailable', async () => {
        mockRosterFetch(null);
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={vi.fn()} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 15' })).toBeInTheDocument(),
        );
    });

    it('invokes onCancel from the Cancel button', async () => {
        mockRosterFetch(5);
        const onCancel = vi.fn();
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={onCancel} />);
        fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
        expect(onCancel).toHaveBeenCalled();
    });

    it('focuses the season select on open so Escape works immediately', async () => {
        mockRosterFetch(5);
        render(<SeasonPromptModal job={job} onSubmit={vi.fn()} onCancel={vi.fn()} />);
        await waitFor(() => expect(screen.getByLabelText('Season')).toHaveFocus());
    });
});

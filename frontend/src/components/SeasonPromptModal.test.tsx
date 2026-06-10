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

const noop = { onSubmit: vi.fn(), onDismiss: vi.fn(), onCancelJob: vi.fn() };

describe('SeasonPromptModal (#370)', () => {
    it('offers one option per season from season_count', async () => {
        mockRosterFetch(5);
        render(<SeasonPromptModal job={job} {...noop} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 05' })).toBeInTheDocument(),
        );
        expect(screen.queryByRole('option', { name: 'Season 06' })).not.toBeInTheDocument();
    });

    it('submits the chosen season', async () => {
        mockRosterFetch(5);
        const onSubmit = vi.fn();
        render(<SeasonPromptModal job={job} {...noop} onSubmit={onSubmit} />);
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
        render(<SeasonPromptModal job={job} {...noop} onSubmit={onSubmit} />);
        fireEvent.click(screen.getByRole('button', { name: /all seasons/i }));
        expect(onSubmit).toHaveBeenCalledWith(undefined);
    });

    it('falls back to 15 season options when the count is unavailable', async () => {
        mockRosterFetch(null);
        render(<SeasonPromptModal job={job} {...noop} />);
        await waitFor(() =>
            expect(screen.getByRole('option', { name: 'Season 15' })).toBeInTheDocument(),
        );
    });

    it('cancelling the job requires the explicit "Cancel job" button', async () => {
        mockRosterFetch(5);
        const onCancelJob = vi.fn();
        const onDismiss = vi.fn();
        render(<SeasonPromptModal job={job} {...noop} onCancelJob={onCancelJob} onDismiss={onDismiss} />);
        fireEvent.click(screen.getByRole('button', { name: /cancel job/i }));
        expect(onCancelJob).toHaveBeenCalled();
        expect(onDismiss).not.toHaveBeenCalled();
    });

    it('Escape dismisses without cancelling the job', async () => {
        mockRosterFetch(5);
        const onCancelJob = vi.fn();
        const onDismiss = vi.fn();
        render(<SeasonPromptModal job={job} {...noop} onCancelJob={onCancelJob} onDismiss={onDismiss} />);
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
        expect(onDismiss).toHaveBeenCalled();
        expect(onCancelJob).not.toHaveBeenCalled();
    });

    it('backdrop click dismisses without cancelling the job', async () => {
        mockRosterFetch(5);
        const onCancelJob = vi.fn();
        const onDismiss = vi.fn();
        render(<SeasonPromptModal job={job} {...noop} onCancelJob={onCancelJob} onDismiss={onDismiss} />);
        fireEvent.click(screen.getByTestId('season-prompt-backdrop'));
        expect(onDismiss).toHaveBeenCalled();
        expect(onCancelJob).not.toHaveBeenCalled();
    });

    it('focuses the season select on open so Escape works immediately', async () => {
        mockRosterFetch(5);
        render(<SeasonPromptModal job={job} {...noop} />);
        await waitFor(() => expect(screen.getByLabelText('Season')).toHaveFocus());
    });
});

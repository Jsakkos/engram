import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TvdbSuggestionNotice } from './TvdbSuggestionNotice';

const SUGGESTION = { season: 1, tmdb: 24, tvdb: 26 };

describe('TvdbSuggestionNotice', () => {
    it('states both counts', () => {
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={vi.fn()} onDismiss={vi.fn()} />);
        const text = screen.getByTestId('tvdb-suggestion').textContent ?? '';
        expect(text).toContain('Season 1');
        expect(text).toContain('26');
        expect(text).toContain('24');
    });

    it('switch and dismiss call their handlers', async () => {
        const onSwitch = vi.fn();
        const onDismiss = vi.fn();
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={onSwitch} onDismiss={onDismiss} />);
        await userEvent.click(screen.getByRole('button', { name: /Switch to TheTVDB numbering/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(onSwitch).toHaveBeenCalledOnce();
        expect(onDismiss).toHaveBeenCalledOnce();
    });

    it('shows the attribution link', () => {
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={vi.fn()} onDismiss={vi.fn()} />);
        expect(screen.getByRole('link', { name: /TheTVDB/ })).toBeTruthy();
    });

    it('surfaces a switch error', async () => {
        const onSwitch = vi.fn().mockRejectedValue(new Error('TheTVDB is unavailable right now'));
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={onSwitch} onDismiss={vi.fn()} />);
        await userEvent.click(screen.getByRole('button', { name: /Switch to TheTVDB numbering/ }));
        expect(await screen.findByText(/unavailable/)).toBeTruthy();
    });
});

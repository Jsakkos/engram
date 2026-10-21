import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { PlayerControls } from './PlayerControls';
import type { DiscTitle } from '../../types';

vi.mock('sonner', () => ({
    toast: { success: vi.fn(), error: vi.fn() },
}));

function makeTitle(overrides: Partial<DiscTitle> = {}): DiscTitle {
    return {
        id: 1,
        job_id: 1,
        title_index: 1,
        duration_seconds: 7200,
        file_size_bytes: 30_000_000_000,
        chapter_count: 24,
        is_selected: true,
        output_filename: null,
        matched_episode: null,
        match_confidence: 0,
        state: 'review',
        ...overrides,
    };
}

async function withClipboard(clipboard: unknown, fn: () => Promise<void>) {
    const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    if (clipboard === undefined) {
        delete (navigator as { clipboard?: unknown }).clipboard;
    } else {
        Object.assign(navigator, { clipboard });
    }
    try {
        await fn();
    } finally {
        if (original) {
            Object.defineProperty(navigator, 'clipboard', original);
        } else {
            delete (navigator as { clipboard?: unknown }).clipboard;
        }
    }
}

describe('PlayerControls', () => {
    it('renders both controls when the track has a ripped file', () => {
        render(<PlayerControls title={makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' })} />);

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /copy stream url/i })).toBeInTheDocument();
    });

    it('renders the controls for an organized track with no staging copy', () => {
        // A movie parked on a library conflict, or re-opened after organizing,
        // has only organized_to.
        render(
            <PlayerControls
                title={makeTitle({
                    output_filename: null,
                    organized_to: 'C:\\Movies\\Inception (2010)\\Inception (2010).mkv',
                })}
            />,
        );

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
    });

    it('renders nothing when the track has no file at all', () => {
        const { container } = render(
            <PlayerControls title={makeTitle({ output_filename: null, organized_to: null })} />,
        );

        expect(container).toBeEmptyDOMElement();
    });

    it('points the player link at the playlist endpoint for this job and title', () => {
        render(
            <PlayerControls
                title={makeTitle({ id: 42, job_id: 7, output_filename: 'C:\\staging\\title_01.mkv' })}
            />,
        );

        const link = screen.getByRole('link', { name: /open in player/i });
        expect(link).toHaveAttribute('href', '/api/jobs/7/titles/42/playlist.m3u');
    });

    it('opens the player link in a new tab so an error cannot discard review state', () => {
        render(<PlayerControls title={makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' })} />);

        const link = screen.getByRole('link', { name: /open in player/i });
        expect(link).toHaveAttribute('target', '_blank');
        expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    });

    it('copies an absolute media URL to the clipboard', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        await withClipboard({ writeText }, async () => {
            render(
                <PlayerControls
                    title={makeTitle({ id: 42, job_id: 7, output_filename: 'C:\\staging\\title_01.mkv' })}
                />,
            );

            await userEvent.click(screen.getByRole('button', { name: /copy stream url/i }));

            expect(writeText).toHaveBeenCalledWith(
                `${window.location.origin}/api/jobs/7/titles/42/media`,
            );
        });
    });

    it('shows an error toast with the URL when the clipboard is unavailable', async () => {
        // Real-world target case: plain-HTTP LAN access is a non-secure context,
        // where `navigator.clipboard` is undefined and the call throws
        // synchronously. The copy control must never fail silently.
        await withClipboard(undefined, async () => {
            render(
                <PlayerControls
                    title={makeTitle({ id: 42, job_id: 7, output_filename: 'C:\\staging\\title_01.mkv' })}
                />,
            );

            await userEvent.click(screen.getByRole('button', { name: /copy stream url/i }));

            expect(toast.error).toHaveBeenCalledWith(
                expect.stringContaining(`${window.location.origin}/api/jobs/7/titles/42/media`),
            );
        });
    });

    it('shows the standing hint whenever the controls render', () => {
        // The hint is permanent, not error-triggered: a failed .m3u handoff
        // gives the page no signal to react to.
        render(<PlayerControls title={makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' })} />);

        expect(screen.getByText(/open network stream/i)).toBeInTheDocument();
    });
});

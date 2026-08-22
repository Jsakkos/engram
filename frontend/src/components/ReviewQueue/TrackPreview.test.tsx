import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TrackPreview } from './TrackPreview';

const INFO = {
    available: true,
    reason: null,
    duration: 1327.759,
    direct: false,
    chapters: [
        { index: 1, start: 0, end: 31.16, title: 'Chapter 01' },
        { index: 2, start: 31.164467, end: 442.6422, title: 'Chapter 02' },
        { index: 3, start: 442.6422, end: 876.1753, title: 'Chapter 03' },
    ],
};

function mockPreview(body: unknown) {
    return vi.fn().mockResolvedValue({ ok: true, json: async () => body, text: async () => '' });
}

beforeEach(() => {
    vi.stubGlobal('fetch', mockPreview(INFO));
});

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('TrackPreview', () => {
    it('transcodes nothing until the user asks for a preview', () => {
        render(<TrackPreview jobId="7" titleId={3} />);
        expect(screen.getByText('Preview')).toBeInTheDocument();
        expect(fetch).not.toHaveBeenCalled();
    });

    it('offers the disc chapters as seek points', async () => {
        render(<TrackPreview jobId="7" titleId={3} />);
        await userEvent.click(screen.getByText('Preview'));
        // Chapter starts are fractional and must read as clock times.
        await waitFor(() => expect(screen.getByText('Chapter 02 · 0:31')).toBeInTheDocument());
        expect(screen.getByText('Chapter 03 · 7:22')).toBeInTheDocument();
    });

    it('plays the selected chapter', async () => {
        const { container } = render(<TrackPreview jobId="7" titleId={3} />);
        await userEvent.click(screen.getByText('Preview'));
        await waitFor(() => expect(container.querySelector('video')).toBeInTheDocument());
        await userEvent.click(screen.getByText('Chapter 03 · 7:22'));
        await waitFor(() =>
            expect(container.querySelector('video')?.getAttribute('src')).toBe(
                '/api/jobs/7/titles/3/preview/clip?t=442.6422',
            ),
        );
    });

    it('falls back to evenly spaced points when the track has no chapters', async () => {
        vi.stubGlobal('fetch', mockPreview({ ...INFO, chapters: [] }));
        render(<TrackPreview jobId="7" titleId={3} />);
        await userEvent.click(screen.getByText('Preview'));
        await waitFor(() => expect(screen.getByText('50% · 11:03')).toBeInTheDocument());
        expect(screen.getByText(/no chapter marks/)).toBeInTheDocument();
    });

    it('explains itself when previews are unavailable', async () => {
        vi.stubGlobal(
            'fetch',
            mockPreview({ ...INFO, available: false, reason: 'ffprobe was not found.' }),
        );
        render(<TrackPreview jobId="7" titleId={3} />);
        await userEvent.click(screen.getByText('Preview'));
        await waitFor(() => expect(screen.getByText(/ffprobe was not found/)).toBeInTheDocument());
    });
});

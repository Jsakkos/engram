import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewQueue from './ReviewQueue';

vi.mock('../hooks/useWebSocket', () => ({
    useWebSocket: () => ({ addMessageListener: () => () => {} }),
}));

const title = (id: number) => ({
    id,
    job_id: 1,
    title_index: id,
    duration_seconds: 1320,
    file_size_bytes: 1_000_000,
    chapter_count: 5,
    is_selected: true,
    output_filename: null,
    matched_episode: null,
    match_confidence: 0,
    state: 'review',
});

function mockBackend(job: Record<string, unknown>, config: Record<string, unknown> = {}) {
    vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string) => {
            const body = url.endsWith('/titles')
                ? [title(1), title(2)]
                : url.includes('/season-roster')
                  ? { available: false, episodes: [] }
                  : url.startsWith('/api/config')
                    ? { tmdb_configured: true, ...config }
                    : { id: 1, state: 'review_needed', content_type: 'tv', detected_title: 'Frasier', detected_season: 1, ...job };
            return { ok: true, status: 200, json: async () => body, text: async () => '' };
        }),
    );
}

function renderPage() {
    return render(
        <MemoryRouter initialEntries={['/review/1']}>
            <Routes>
                <Route path="/review/:jobId" element={<ReviewQueue />} />
            </Routes>
        </MemoryRouter>,
    );
}

describe('ReviewQueue identity step (#715)', () => {
    beforeEach(() => vi.unstubAllGlobals());
    afterEach(() => vi.unstubAllGlobals());

    it('leads with identifying an unknown disc and withholds episode assignment', async () => {
        mockBackend({ tmdb_id: null });
        renderPage();
        expect(await screen.findByRole('heading', { name: /identify this disc/i })).toBeInTheDocument();
        expect(screen.queryByText(/pick episode/i)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /re-match all/i })).not.toBeInTheDocument();
    });

    it('opens straight into episode review once the show is confirmed', async () => {
        mockBackend({ tmdb_id: 3452 });
        renderPage();
        expect(await screen.findByLabelText(/manual episode for title 1/i)).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: /identify this disc/i })).not.toBeInTheDocument();
    });

    it('does not strand heuristic-only mode behind an identity step', async () => {
        mockBackend({ tmdb_id: null }, { tmdb_configured: false });
        renderPage();
        await waitFor(() =>
            expect(screen.queryByRole('heading', { name: /identify this disc/i })).not.toBeInTheDocument(),
        );
        expect(await screen.findByLabelText(/manual episode for title 1/i)).toBeInTheDocument();
    });
});

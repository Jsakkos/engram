import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewQueue from './ReviewQueue';

vi.mock('../hooks/useWebSocket', () => ({
    useWebSocket: () => ({ addMessageListener: () => () => {} }),
}));

const title = (id: number, extra: Record<string, unknown> = {}) => ({
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
    ...extra,
});

function mockBackend(
    job: Record<string, unknown>,
    config: Record<string, unknown> = {},
    titles: unknown[] = [title(1), title(2)],
) {
    vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string) => {
            const body = url.endsWith('/titles')
                ? titles
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

describe('ReviewQueue keyboard shortcuts (#716)', () => {
    const ranked = (id: number) =>
        title(id, {
            matched_episode: 'S01E02',
            match_confidence: 0.4,
            match_details: JSON.stringify({
                runner_ups: [
                    { episode: 'S01E05', score: 0.3 },
                    { episode: 'S01E07', score: 0.2 },
                ],
            }),
        });

    beforeEach(() => vi.unstubAllGlobals());
    afterEach(() => vi.unstubAllGlobals());

    async function open(titles: unknown[] = [ranked(1), ranked(2), ranked(3)]) {
        mockBackend({ tmdb_id: 3452 }, {}, titles);
        renderPage();
        await screen.findByLabelText(/manual episode for title 1/i);
    }

    const selectedRow = () => document.querySelector('[data-title-row][aria-current="true"]');

    it('moves between tracks with j/k and the arrow keys, clamped at the ends', async () => {
        await open();
        expect(selectedRow()).toHaveAttribute('data-title-row', '1');
        fireEvent.keyDown(window, { key: 'j' });
        expect(selectedRow()).toHaveAttribute('data-title-row', '2');
        fireEvent.keyDown(window, { key: 'ArrowDown' });
        fireEvent.keyDown(window, { key: 'ArrowDown' });
        expect(selectedRow()).toHaveAttribute('data-title-row', '3');
        fireEvent.keyDown(window, { key: 'k' });
        expect(selectedRow()).toHaveAttribute('data-title-row', '2');
        fireEvent.keyDown(window, { key: 'ArrowUp' });
        fireEvent.keyDown(window, { key: 'ArrowUp' });
        expect(selectedRow()).toHaveAttribute('data-title-row', '1');
    });

    it('accepts the ranked candidate for the number pressed', async () => {
        await open();
        fireEvent.keyDown(window, { key: '2' });
        const row = document.querySelector('[data-title-row="1"]') as HTMLElement;
        expect(row).toHaveTextContent('S01E05');
    });

    it('ignores a rank with no candidate', async () => {
        await open([title(1), title(2)]);
        fireEvent.keyDown(window, { key: '3' });
        expect(document.querySelector('[data-title-row="1"]')).toHaveTextContent(/needs review/i);
    });

    it('marks the track as extra with e and leaves it unresolved with s', async () => {
        await open();
        fireEvent.keyDown(window, { key: 'e' });
        const row = () => document.querySelector('[data-title-row="1"]') as HTMLElement;
        expect(row()).toHaveTextContent(/extra/i);
        fireEvent.keyDown(window, { key: 's' });
        expect(row()).toHaveTextContent(/needs review/i);
    });

    it('focuses the episode picker on /', async () => {
        await open();
        fireEvent.keyDown(window, { key: '/' });
        expect(screen.getByLabelText(/manual episode for title 1/i)).toHaveFocus();
    });

    it('does nothing while a form control has focus', async () => {
        await open();
        const picker = screen.getByLabelText(/manual episode for title 1/i);
        picker.focus();
        fireEvent.keyDown(picker, { key: 'j' });
        fireEvent.keyDown(picker, { key: 'e' });
        expect(selectedRow()).toHaveAttribute('data-title-row', '1');
        expect(document.querySelector('[data-title-row="1"]')).not.toHaveTextContent(/extra/i);
    });

    it('opens the ? overlay from the header hint and the key, and closes it with Escape', async () => {
        await open();
        fireEvent.keyDown(window, { key: '?', shiftKey: true });
        const dialog = await screen.findByRole('dialog', { name: /keyboard shortcuts/i });
        expect(dialog).toHaveTextContent(/next track/i);
        fireEvent.keyDown(dialog, { key: 'Escape' });
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: /keyboard shortcuts/i }));
        expect(await screen.findByRole('dialog', { name: /keyboard shortcuts/i })).toBeInTheDocument();
    });

    it('stays off while the identity step is showing', async () => {
        mockBackend({ tmdb_id: null }, {}, [ranked(1)]);
        renderPage();
        await screen.findByRole('heading', { name: /identify this disc/i });
        fireEvent.keyDown(window, { key: '?', shiftKey: true });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
});

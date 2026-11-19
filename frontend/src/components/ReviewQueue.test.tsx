import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
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

function NavigableReview() {
    const navigate = useNavigate();
    return <><button onClick={() => navigate('/review/1')}>Open first job</button><button onClick={() => navigate('/review/2')}>Open second job</button><ReviewQueue /></>;
}

describe('ReviewQueue AI provider availability', () => {
    beforeEach(() => vi.unstubAllGlobals());
    afterEach(() => vi.unstubAllGlobals());

    it('ignores a stale detail response even after navigating away and back to the same job', async () => {
        let finishOld: (() => void) | undefined;
        let firstTitles = true;
        vi.stubGlobal('fetch', vi.fn(async (url: string) => {
            if (url.startsWith('/api/config')) return { ok: true, json: async () => ({ tmdb_configured: true }) };
            if (url.includes('/season-roster')) return { ok: true, json: async () => ({ available: false, episodes: [] }) };
            const id = url.startsWith('/api/jobs/2') ? 2 : 1;
            if (url === '/api/jobs/1/titles' && firstTitles) {
                firstTitles = false;
                return { ok: true, json: () => new Promise((resolve) => {
                    finishOld = () => resolve([title(100)]);
                }) };
            }
            return { ok: true, json: async () => url.endsWith('/titles')
                ? [title(id * 100, { job_id: id, matched_episode: 'S01E05' })]
                : { id, state: 'review_needed', content_type: 'tv', detected_title: `Show ${id}`, detected_season: 1, tmdb_id: 3452 },
            };
        }));
        render(<MemoryRouter initialEntries={['/review/1']}><Routes><Route path="/review/:jobId" element={<NavigableReview />} /></Routes></MemoryRouter>);
        await waitFor(() => expect(finishOld).toBeDefined());
        fireEvent.click(screen.getByRole('button', { name: 'Open second job' }));
        await screen.findByLabelText(/manual episode for title 200/i);
        fireEvent.click(screen.getByRole('button', { name: 'Open first job' }));
        await waitFor(() => expect(screen.getByLabelText(/manual episode for title 100/i)).toHaveValue('S01E05'));
        await act(async () => finishOld!());
        expect(screen.getByLabelText(/manual episode for title 100/i)).toHaveValue('S01E05');
    });

    it('keeps an unsaved manual assignment when a pending AI request refreshes the page', async () => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '' },
        );
        const backendFetch = fetch;
        let finish: (() => void) | undefined;
        vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
            if (url.includes('/llm-match')) return new Promise<Response>((resolve) => {
                finish = () => resolve({ ok: true, status: 200, json: async () => ({ suggestion: null, reason: 'no_match' }) } as Response);
            });
            return backendFetch(url, init);
        }));
        renderPage();
        const button = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);
        await waitFor(() => expect(finish).toBeDefined());
        fireEvent.change(screen.getByLabelText(/manual episode for title 1/i), { target: { value: 'S01E07' } });
        expect(screen.getByLabelText(/manual episode for title 1/i)).toHaveValue('S01E07');
        await act(async () => finish!());
        expect(screen.getByLabelText(/manual episode for title 1/i)).toHaveValue('S01E07');
    });

    it('discards old-season choices when a pending request reports a changed identity', async () => {
        const job = { tmdb_id: 3452, detected_season: 1 };
        mockBackend(job, { ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '' });
        const backendFetch = fetch;
        let finish: (() => void) | undefined;
        vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
            if (url.includes('/llm-match')) return new Promise<Response>((resolve) => {
                finish = () => {
                    job.detected_season = 2;
                    resolve({ ok: true, status: 200, json: async () => ({ suggestion: null, reason: 'context_changed' }) } as Response);
                };
            });
            return backendFetch(url, init);
        }));
        renderPage();
        const button = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);
        await waitFor(() => expect(finish).toBeDefined());
        fireEvent.change(screen.getByLabelText(/manual episode for title 1/i), { target: { value: 'S01E07' } });
        await act(async () => finish!());
        expect(screen.getByLabelText(/manual episode for title 1/i)).toHaveValue('');
        expect(screen.getByText(/Matching details changed while AI was running/)).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'S02E07' })).toBeInTheDocument();
    });

    it('clears unsaved episode numbers if numbering changes before the AI refresh', async () => {
        const job = { tmdb_id: 3452, episode_namespace: 'tmdb' };
        mockBackend(job, { ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '' });
        const backendFetch = fetch;
        vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
            if (url.includes('/llm-match')) {
                // The numbering can change after inference but before the page
                // refreshes, even when the result carries no context_changed flag.
                job.episode_namespace = 'tvdb';
                return Promise.resolve({ ok: true, status: 200, json: async () => ({ suggestion: null, reason: 'no_match' }) } as Response);
            }
            return backendFetch(url, init);
        }));
        renderPage();
        const button = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.change(screen.getByLabelText(/manual episode for title 1/i), { target: { value: 'S01E07' } });
        fireEvent.click(button);
        await waitFor(() => expect(screen.getByLabelText(/manual episode for title 1/i)).toHaveValue(''));
    });

    it.each(['success', 'error'])('ignores an old job’s pending AI %s after navigation', async (outcome) => {
        let complete: (() => void) | undefined;
        const fetchMock = vi.fn(async (url: string) => {
            if (url.includes('/llm-match')) {
                return new Promise<Response>((resolve) => {
                    complete = () => resolve({
                        ok: outcome === 'success', status: outcome === 'success' ? 200 : 503,
                        json: async () => ({ suggestion: null, reason: 'no_match' }),
                        text: async () => JSON.stringify({ reason: 'llm_error', message: 'Old job error' }),
                    } as Response);
                });
            }
            if (url.startsWith('/api/config')) return { ok: true, json: async () => ({
                tmdb_configured: true, ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '',
            }) };
            if (url.includes('/season-roster')) return { ok: true, json: async () => ({ available: false, episodes: [] }) };
            const id = url.startsWith('/api/jobs/2') ? 2 : 1;
            return { ok: true, json: async () => url.endsWith('/titles')
                ? [title(id * 100, { job_id: id })]
                : { id, state: 'review_needed', content_type: 'tv', detected_title: `Show ${id}`, detected_season: 1, tmdb_id: 3452 },
            };
        });
        vi.stubGlobal('fetch', fetchMock);
        render(<MemoryRouter initialEntries={['/review/1']}><Routes><Route path="/review/:jobId" element={<NavigableReview />} /></Routes></MemoryRouter>);
        const match = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(match).toBeEnabled());
        fireEvent.click(match);
        await waitFor(() => expect(complete).toBeDefined());
        fireEvent.click(screen.getByRole('button', { name: 'Open second job' }));
        await screen.findByLabelText(/manual episode for title 200/i);
        const oldRequests = fetchMock.mock.calls.filter(([url]) => /^\/api\/jobs\/1(?:\/titles)?$/.test(url)).length;
        await act(async () => complete!());
        expect(fetchMock.mock.calls.filter(([url]) => /^\/api\/jobs\/1(?:\/titles)?$/.test(url))).toHaveLength(oldRequests);
        expect(screen.getByLabelText(/manual episode for title 200/i)).toBeInTheDocument();
        expect(screen.queryByText('Old job error')).not.toBeInTheDocument();
    });

    it('tracks each pending title when navigating between concurrent AI matches', async () => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '' },
        );
        const backendFetch = fetch;
        const pending = new Map<string, () => void>();
        vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
            if (url.includes('/llm-match')) {
                return new Promise<Response>((resolve) => pending.set(url, () => resolve({
                    ok: true, status: 200, json: async () => ({ suggestion: null, reason: 'no_match' }),
                } as Response)));
            }
            return backendFetch(url, init);
        }));
        renderPage();
        try {
            const first = await screen.findByRole('button', { name: /try ai match/i });
            await waitFor(() => expect(first).toBeEnabled());
            fireEvent.click(first);
            await waitFor(() => expect(pending.has('/api/jobs/1/titles/1/llm-match')).toBe(true));
            fireEvent.keyDown(window, { key: 'j' });
            fireEvent.click(screen.getByRole('button', { name: /try ai match/i }));
            await waitFor(() => expect(pending.has('/api/jobs/1/titles/2/llm-match')).toBe(true));
            fireEvent.keyDown(window, { key: 'k' });
            expect(screen.getByRole('button', { name: /matching/i })).toBeDisabled();
            await act(async () => pending.get('/api/jobs/1/titles/1/llm-match')!());
            await waitFor(() => expect(screen.getByRole('button', { name: /try ai match/i })).toBeEnabled());
            fireEvent.keyDown(window, { key: 'j' });
            expect(screen.getByRole('button', { name: /matching/i })).toBeDisabled();
        } finally {
            await act(async () => { for (const finish of pending.values()) finish(); });
        }
    });

    it('requests fresh inference when retrying an existing suggestion', async () => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: 'lmstudio', ai_api_key: '' },
            [title(1, { match_details: JSON.stringify({ llm_suggestion: {
                episode: 3, confidence: 0.9, reasoning: 'Cargo.', runner_up: null, model: 'old-model',
            } }) })],
        );
        renderPage();
        const button = await screen.findByRole('button', { name: /try ai again/i });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);
        await waitFor(() => expect(fetch).toHaveBeenCalledWith(
            '/api/jobs/1/titles/1/llm-match?force=true', { method: 'POST' },
        ));
    });

    it.each(['lmstudio', 'ollama'])('allows %s without an API key', async (provider) => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: provider, ai_api_key: '' },
        );
        renderPage();

        const button = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(button).toBeEnabled());
        expect(button).toHaveAttribute('title', 'Run AI episode matching');

        fireEvent.click(button);

        await waitFor(() => expect(fetch).toHaveBeenCalledWith(
            '/api/jobs/1/titles/1/llm-match',
            { method: 'POST' },
        ));
    });

    it('requires a key for a hosted provider', async () => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: 'openai', ai_api_key: '' },
        );
        renderPage();

        const button = await screen.findByRole('button', { name: /try ai match/i });
        expect(button).toBeDisabled();
        expect(button).toHaveAttribute('title', expect.stringMatching(/no ai api key/i));
    });

    it('allows a hosted provider with a stored key', async () => {
        mockBackend(
            { tmdb_id: 3452 },
            { ai_episode_matching_enabled: true, ai_provider: 'openai', ai_api_key: '***' },
        );
        renderPage();

        const button = await screen.findByRole('button', { name: /try ai match/i });
        await waitFor(() => expect(button).toBeEnabled());
    });
});

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

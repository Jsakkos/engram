import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import App from './App';
import { useJobManagement } from './hooks/useJobManagement';
import type { Job } from '../types';

// The prompt-surfacing behavior (P13) lives in MainDashboard's effect + the
// DiscCard CTA wiring. We drive it by controlling the jobs the hook returns,
// so the test exercises the real effect, real adapters, and real modals with
// zero backend / drive-sentinel risk.
vi.mock('./hooks/useJobManagement');

function makeJob(overrides: Partial<Job>): Job {
    return {
        id: 1,
        drive_id: 'E:',
        volume_label: 'DISC',
        content_type: 'tv',
        state: 'review_needed',
        current_speed: '',
        eta_seconds: 0,
        progress_percent: 0,
        current_title: 0,
        total_titles: 1,
        error_message: null,
        ...overrides,
    } as Job;
}

function mockJobs(jobs: Job[]) {
    (useJobManagement as unknown as Mock).mockReturnValue({
        jobs,
        titlesMap: {},
        isConnected: true,
        updateStatus: null,
        parkedDiscs: [],
        cancelJob: vi.fn(),
        advanceJob: vi.fn(),
        clearCompleted: vi.fn(),
        setJobName: vi.fn(),
        reIdentifyJob: vi.fn(),
        disclosure: null,
        clearDisclosure: vi.fn(),
    });
}

const UNREADABLE = 'Disc label unreadable. Please enter the title to continue.';

function renderApp() {
    return render(
        <MemoryRouter initialEntries={['/']}>
            <App />
        </MemoryRouter>,
    );
}

beforeEach(() => {
    // jsdom has no matchMedia; SvRipAnimation (rendered for a ripping job)
    // reads prefers-reduced-motion through it. Shim it as "no preference".
    vi.stubGlobal(
        'matchMedia',
        vi.fn().mockImplementation((query: string) => ({
            matches: false,
            media: query,
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(),
        })),
    );

    // App fires config/detect-tools/poster/asr fetches on mount. Keep the happy
    // path quiet: setup complete, TMDB configured, Windows (no banners), and a
    // benign fallback for everything else (posters, asr-status, side rail).
    vi.stubGlobal(
        'fetch',
        vi.fn((input: RequestInfo | URL) => {
            const url = typeof input === 'string' ? input : input.toString();
            if (url.includes('/api/config')) {
                return Promise.resolve({
                    ok: true,
                    json: async () => ({
                        setup_complete: true,
                        tmdb_configured: true,
                        discdb_contributions_enabled: false,
                    }),
                });
            }
            if (url.includes('/api/detect-tools')) {
                return Promise.resolve({ ok: true, json: async () => ({ platform: 'win32' }) });
            }
            return Promise.resolve({ ok: false, json: async () => ({}) });
        }),
    );
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
});

describe('App — P13 prompt surfacing', () => {
    it('does NOT auto-open the modal while another job is active; shows the card CTA instead', async () => {
        mockJobs([
            makeJob({ id: 1, state: 'ripping', volume_label: 'INCEPTION_2010', content_type: 'movie' }),
            makeJob({ id: 2, state: 'review_needed', review_reason: UNREADABLE }),
        ]);
        renderApp();

        // The non-modal affordance is present...
        const cta = await screen.findByTestId('disccard-identify-cta');
        expect(cta).toHaveTextContent(/name this disc/i);
        // ...and the blocking modal did NOT steal focus.
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('opens the Identify Disc modal on demand when the card CTA is clicked', async () => {
        mockJobs([
            makeJob({ id: 1, state: 'ripping', volume_label: 'INCEPTION_2010', content_type: 'movie' }),
            makeJob({ id: 2, state: 'review_needed', review_reason: UNREADABLE }),
        ]);
        renderApp();

        fireEvent.click(await screen.findByTestId('disccard-identify-cta'));

        const dialog = await screen.findByRole('dialog');
        expect(dialog).toBeInTheDocument();
        expect(screen.getByText(/identify disc/i)).toBeInTheDocument();
    });

    it('auto-opens the modal when the review job is the only active job (walk-away path)', async () => {
        mockJobs([makeJob({ id: 2, state: 'review_needed', review_reason: UNREADABLE })]);
        renderApp();

        expect(await screen.findByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText(/identify disc/i)).toBeInTheDocument();
    });

    it('stale completed jobs do not suppress the auto-open (walk-away path with done cards)', async () => {
        mockJobs([
            makeJob({ id: 1, state: 'completed', volume_label: 'OLD_MOVIE_2001', content_type: 'movie' }),
            makeJob({ id: 2, state: 'review_needed', review_reason: UNREADABLE }),
        ]);
        renderApp();

        expect(await screen.findByRole('dialog')).toBeInTheDocument();
    });
});

import '@testing-library/jest-dom';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MotionConfig } from 'motion/react';
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

// Wrap the app in MotionConfig with reducedMotion="always" so Framer Motion
// completes all animations synchronously in jsdom — without this, exiting
// AnimatePresence children linger in the DOM at fractional opacity and
// cause false-positive "dialog still present" failures.
function renderApp() {
    const result = render(
        <MotionConfig reducedMotion="always">
            <MemoryRouter initialEntries={['/']}>
                <App />
            </MemoryRouter>
        </MotionConfig>,
    );
    // Wrap rerender so callers don't have to repeat the boilerplate.
    return {
        ...result,
        rerender: () =>
            result.rerender(
                <MotionConfig reducedMotion="always">
                    <MemoryRouter initialEntries={['/']}>
                        <App />
                    </MemoryRouter>
                </MotionConfig>,
            ),
    };
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

describe('App — walk-away Phase B identity prompts on RIPPING jobs', () => {
    const namePrompt = JSON.stringify({ kind: 'name', reason: 'Disc label unreadable.' });
    const reidentifyPrompt = JSON.stringify({
        kind: 'reidentify',
        reason: 'Multiple shows share this name.',
    });

    it('auto-opens the name modal when the prompt-bearing ripping job is the only active job', async () => {
        mockJobs([makeJob({ id: 2, state: 'ripping', identity_prompt_json: namePrompt })]);
        renderApp();

        const dialog = await screen.findByRole('dialog');
        expect(dialog).toBeInTheDocument();
        expect(screen.getByText(/identify disc/i)).toBeInTheDocument();
    });

    it('routes a reidentify prompt to the Re-Identify modal', async () => {
        mockJobs([
            makeJob({
                id: 2,
                state: 'ripping',
                detected_title: 'Frasier',
                identity_prompt_json: reidentifyPrompt,
            }),
        ]);
        renderApp();

        expect(await screen.findByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText(/re-identify disc/i)).toBeInTheDocument();
    });

    it('shows the card CTA (no modal) when another job is also active', async () => {
        mockJobs([
            makeJob({ id: 1, state: 'ripping', volume_label: 'OTHER_DISC' }),
            makeJob({
                id: 2,
                state: 'ripping',
                volume_label: 'AMBIGUOUS',
                identity_prompt_json: reidentifyPrompt,
            }),
        ]);
        renderApp();

        const cta = await screen.findByTestId('disccard-identify-cta');
        expect(cta).toHaveTextContent(/confirm title/i);
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('opens the Re-Identify modal on demand from the ripping card CTA', async () => {
        mockJobs([
            makeJob({ id: 1, state: 'ripping', volume_label: 'OTHER_DISC' }),
            makeJob({
                id: 2,
                state: 'ripping',
                volume_label: 'AMBIGUOUS',
                identity_prompt_json: reidentifyPrompt,
            }),
        ]);
        renderApp();

        fireEvent.click(await screen.findByTestId('disccard-identify-cta'));

        expect(await screen.findByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText(/re-identify disc/i)).toBeInTheDocument();
    });
});

describe('App — B7 modal re-open race suppression', () => {
    // The backend clears identity_prompt_json after answering, but a WS progress
    // tick can arrive before that clearing broadcast, still carrying the old prompt.
    // Submit should add the job id to the dismissed set so the stale tick can't
    // re-open the modal the user just answered.

    const namePrompt = JSON.stringify({ kind: 'name', reason: 'Disc label unreadable.' });
    const reidentifyPrompt = JSON.stringify({ kind: 'reidentify', reason: 'Ambiguous title.' });

    it('name submit + stale tick: modal stays closed', async () => {
        // detected_title pre-fills the input so the submit button is enabled.
        const job = makeJob({ id: 3, state: 'ripping', identity_prompt_json: namePrompt, detected_title: 'Seinfeld' });
        mockJobs([job]);
        const { rerender } = renderApp();

        // Modal should auto-open (only active job).
        const dialogBeforeSubmit = await screen.findByRole('dialog');

        // Submit the name prompt.
        act(() => {
            fireEvent.click(screen.getByTestId('name-prompt-submit'));
        });

        // Simulate a stale progress tick: the job still carries the prompt
        // (backend clearing broadcast hasn't landed yet).
        act(() => {
            mockJobs([{ ...job, identity_prompt_json: namePrompt }]);
            rerender();
        });

        // Modal must stay closed — dismissal recorded on submit blocks re-open.
        // After submit AnimatePresence may keep the exiting element in DOM at
        // opacity:0; a stale tick must not open a NEW dialog on top of it.
        // Either the dialog is gone (exit completed) or it is the SAME node
        // still exiting — a different node would mean the modal re-opened.
        const afterDialog = screen.queryByRole('dialog');
        expect(afterDialog === null || afterDialog === dialogBeforeSubmit).toBe(true);
    });

    it('reidentify submit + stale tick: modal stays closed', async () => {
        // detected_title initialises the title input so submit is enabled.
        const job = makeJob({
            id: 4,
            state: 'ripping',
            detected_title: 'Frasier',
            identity_prompt_json: reidentifyPrompt,
        });
        mockJobs([job]);
        const { rerender } = renderApp();

        const dialogBeforeSubmit = await screen.findByRole('dialog');

        act(() => {
            fireEvent.click(screen.getByTestId('reidentify-submit'));
        });

        act(() => {
            mockJobs([{ ...job, identity_prompt_json: reidentifyPrompt }]);
            rerender();
        });

        // Either the dialog is gone (exit completed) or it is the SAME node
        // still exiting — a different node would mean the modal re-opened.
        const afterDialog = screen.queryByRole('dialog');
        expect(afterDialog === null || afterDialog === dialogBeforeSubmit).toBe(true);
    });
});

describe('App — auto-opened modal survives a new active job', () => {
    const reidentifyPrompt = JSON.stringify({ kind: 'reidentify', reason: 'Ambiguous title.' });

    it('an auto-opened reidentify modal STAYS open when a second job becomes active', async () => {
        // The only-active-job rule (P13) gates auto-OPENING; it deliberately
        // never auto-CLOSES. Yanking an open modal away mid-typing because an
        // unrelated disc started ripping would lose the user's input.
        const job = makeJob({
            id: 6,
            state: 'ripping',
            detected_title: 'Frasier',
            identity_prompt_json: reidentifyPrompt,
        });
        mockJobs([job]);
        const { rerender } = renderApp();

        const dialog = await screen.findByRole('dialog');
        expect(screen.getByText(/re-identify disc/i)).toBeInTheDocument();

        // A second job becomes active — the auto-open condition is now false.
        act(() => {
            mockJobs([job, makeJob({ id: 7, state: 'ripping', volume_label: 'OTHER_DISC' })]);
            rerender();
        });

        // Same dialog node, still open — not closed and not re-mounted.
        expect(screen.getByRole('dialog')).toBe(dialog);
    });
});

describe('App — B7 content-change re-arm', () => {
    // After a submit+clear cycle, if the backend later issues a DIFFERENT prompt
    // on the same job the modal should auto-open again (per-prompt dismissal).

    const namePrompt = JSON.stringify({ kind: 'name', reason: 'Disc label unreadable.' });
    const reidentifyPrompt = JSON.stringify({ kind: 'reidentify', reason: 'Ambiguous title.' });

    it('different prompt on same job re-opens modal after prior dismissal', async () => {
        const job = makeJob({ id: 5, state: 'ripping', identity_prompt_json: namePrompt, detected_title: 'Frasier' });
        mockJobs([job]);
        const { rerender } = renderApp();

        // Step 1: name modal auto-opens.
        const nameDialog = await screen.findByRole('dialog');
        expect(nameDialog).toBeInTheDocument();

        // Step 2: submit the name prompt — dismissal is recorded.
        act(() => {
            fireEvent.click(screen.getByTestId('name-prompt-submit'));
        });

        // Step 3: clearing broadcast — prompt goes away.
        act(() => {
            mockJobs([{ ...job, identity_prompt_json: '' }]);
            rerender();
        });
        // After clearing, any lingering dialog must be the same exiting node —
        // not a newly opened one (the prompt was cleared, not re-issued).
        const afterClearDialog = screen.queryByRole('dialog');
        if (afterClearDialog) {
            expect(afterClearDialog).toBe(nameDialog);
        }

        // Step 4: backend now issues a DIFFERENT prompt (reidentify).
        act(() => {
            mockJobs([{ ...job, identity_prompt_json: reidentifyPrompt }]);
            rerender();
        });

        // The new prompt should auto-open because the content changed.
        // It must be a NEW dialog element, not the old exiting one.
        const newDialog = await screen.findByRole('dialog');
        expect(newDialog).toBeInTheDocument();
        if (afterClearDialog) {
            // The re-arm opened a different dialog than the one that was closing.
            expect(newDialog).not.toBe(afterClearDialog);
        }
        expect(screen.getByText(/re-identify disc/i)).toBeInTheDocument();
    });
});

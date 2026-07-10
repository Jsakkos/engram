import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import ConfigWizard from './ConfigWizard';

/**
 * jsdom lacks scrollIntoView (our GPU deep-link calls it) and the
 * matchMedia/ResizeObserver Radix Select touches. Polyfill them per-test so the
 * Preferences step (which renders several EngramSelects + GpuAccelerationSetting)
 * mounts cleanly and the deep-link scroll is observable.
 */
beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    if (!window.matchMedia) {
        window.matchMedia = vi.fn().mockImplementation((query: string) => ({
            matches: false,
            media: query,
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(),
        })) as unknown as typeof window.matchMedia;
    }
    if (!(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
        (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
            observe() {}
            unobserve() {}
            disconnect() {}
        };
    }
    mockApi();
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

const ASR_STATUS = {
    device: 'cpu',
    compute_type: 'int8',
    model: 'small',
    workers: 1,
    cpu_threads: 4,
    max_concurrent_matches: 2,
    gpu_detected: true,
    gpu_enabled: false,
    gpu_runtime_installed: true,
    gpu_download_size_bytes: 1.2e9,
    gpu_download: { state: 'idle', downloaded: 0, total: 0, error: null },
    gpu_state: 'available_not_enabled',
};

/** Route the component's startup fetches (config, asr-status, detect-tools). */
function mockApi(configOverrides: Record<string, unknown> = {}) {
    const config = {
        setup_complete: true,
        staging_path: '/staging',
        library_movies_path: '/movies',
        library_tv_path: '/tv',
        ...configOverrides,
    };
    vi.stubGlobal(
        'fetch',
        vi.fn((input: RequestInfo | URL) => {
            const url = typeof input === 'string' ? input : input.toString();
            const json = async () => {
                if (url.includes('/api/asr-status')) return ASR_STATUS;
                if (url.includes('/api/detect-tools'))
                    return {
                        makemkv: { found: false, path: null, version: null, error: null },
                        ffmpeg: { found: false, path: null, version: null, error: null },
                        platform: 'win32',
                    };
                if (url.includes('/api/network/info'))
                    return { lan_access_enabled: false, active_lan_bound: false, lan_ip: null, port: 8000, lan_url: null };
                return config;
            };
            return Promise.resolve({ ok: true, status: 200, json, text: async () => JSON.stringify(config) });
        }),
    );
}

const noop = { onClose: vi.fn(), onComplete: vi.fn() };

describe('ConfigWizard — settings mode (M1)', () => {
    it('titles the modal "Settings" when opened from the gear', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        expect(await screen.findByRole('heading', { level: 2, name: 'Settings' })).toBeInTheDocument();
        expect(screen.queryByRole('heading', { level: 2, name: 'Setup Wizard' })).not.toBeInTheDocument();
    });

    it('renders a section nav instead of the numbered stepper', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        const nav = await screen.findByRole('navigation', { name: /settings sections/i });
        // Section list, not a linear stepper: the onboarding "Step N:" affordances are gone.
        expect(within(nav).getByRole('button', { name: 'Library Paths' })).toBeInTheDocument();
        expect(within(nav).getByRole('button', { name: 'Preferences' })).toBeInTheDocument();
        expect(screen.queryByLabelText(/^Step 1:/)).not.toBeInTheDocument();
    });

    it('clicking a section in the nav shows that section', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        const nav = await screen.findByRole('navigation', { name: /settings sections/i });
        // Default lands on Library Paths, not Preferences content.
        expect(screen.queryByText('Max Concurrent Matches')).not.toBeInTheDocument();
        fireEvent.click(within(nav).getByRole('button', { name: 'Preferences' }));
        expect(await screen.findByText('Max Concurrent Matches')).toBeInTheDocument();
    });

    it('preserves a single global "Save Changes" action (no stepper Next/Back)', async () => {
        const onComplete = vi.fn();
        render(<ConfigWizard {...noop} onComplete={onComplete} isOnboarding={false} />);
        const save = await screen.findByRole('button', { name: 'Save Changes' });
        expect(screen.queryByRole('button', { name: /next/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^back/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /complete setup/i })).not.toBeInTheDocument();

        fireEvent.click(save);
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        const putCall = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.find(
            (c) => c[1]?.method === 'PUT',
        );
        expect(putCall).toBeTruthy();
        expect(putCall?.[0]).toContain('/api/config');
    });
});

describe('ConfigWizard — onboarding mode unchanged (M1 regression)', () => {
    it('keeps the "Setup Wizard" title and the numbered stepper', async () => {
        render(<ConfigWizard {...noop} isOnboarding={true} />);
        expect(await screen.findByRole('heading', { level: 2, name: 'Setup Wizard' })).toBeInTheDocument();
        // Stepper affordance present…
        expect(screen.getByLabelText(/^Step 1: Paths/)).toBeInTheDocument();
        // …and the settings section nav absent.
        expect(screen.queryByRole('navigation', { name: /settings sections/i })).not.toBeInTheDocument();
    });
});

describe('ConfigWizard — deep-linking (M2)', () => {
    it('opens directly on a requested section via initialSection', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} initialSection="preferences" />);
        expect(await screen.findByText('Max Concurrent Matches')).toBeInTheDocument();
    });

    it('initialSection="gpu" opens Preferences and scrolls the GPU control into view', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} initialSection="gpu" />);
        // Preferences section is shown…
        expect(await screen.findByText('Max Concurrent Matches')).toBeInTheDocument();
        // …the GPU control has a scroll anchor…
        const anchor = document.getElementById('setting-gpu-acceleration');
        expect(anchor).not.toBeNull();
        // …and we scrolled to it.
        await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    });

    it('ignores initialSection in onboarding mode (always starts at step 1)', async () => {
        render(<ConfigWizard {...noop} isOnboarding={true} initialSection="gpu" />);
        // Onboarding always begins on Library Paths regardless of deep-link.
        expect(await screen.findByRole('heading', { level: 3, name: 'Library Paths' })).toBeInTheDocument();
        expect(screen.queryByText('Max Concurrent Matches')).not.toBeInTheDocument();
    });
});

describe('ConfigWizard — background pre-transcription toggles', () => {
    it('renders both toggles with defaults and sends the flipped value on save', async () => {
        const onComplete = vi.fn();
        render(<ConfigWizard {...noop} onComplete={onComplete} isOnboarding={false} initialSection="preferences" />);

        // Master switch ships enabled; the expensive full-file option ships off.
        const master = await screen.findByRole('checkbox', { name: /background pre-transcription/i });
        expect(master).toBeChecked();
        const fullFile = screen.getByRole('checkbox', { name: /pre-transcribe entire files/i });
        expect(fullFile).not.toBeChecked();

        // Disabling the master switch hides the dependent full-file option.
        fireEvent.click(master);
        expect(screen.queryByRole('checkbox', { name: /pre-transcribe entire files/i })).not.toBeInTheDocument();

        // The flipped value reaches the PUT payload (three-way sync, frontend leg).
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        const putCall = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.find(
            (c) => c[1]?.method === 'PUT',
        );
        const body = JSON.parse(putCall?.[1]?.body as string);
        expect(body.enable_background_pretranscription).toBe(false);
        expect(body.pretranscribe_full_file).toBe(false);
    });

    it('reads snake_case GET fields and does not fall back to defaults when values are present', async () => {
        // Verifies the camelCase←snake_case GET mapping: a typo in the reader key would
        // let the ?? fallback silently paper over the bug and this test would fail.
        mockApi({ enable_background_pretranscription: false, pretranscribe_full_file: true });
        const onComplete = vi.fn();
        render(<ConfigWizard {...noop} onComplete={onComplete} isOnboarding={false} initialSection="preferences" />);

        // Master off — GET value (false) must win over the ?? true default.
        const master = await screen.findByRole('checkbox', { name: /background pre-transcription/i });
        expect(master).not.toBeChecked();

        // Sub-toggle is hidden while master is off; re-enable master to reveal it.
        fireEvent.click(master);
        const fullFile = await screen.findByRole('checkbox', { name: /pre-transcribe entire files/i });
        // GET value (true) must win over the ?? false default — not just the default.
        expect(fullFile).toBeChecked();
    });

    it('sends pretranscribe_full_file=true in PUT when sub-toggle is explicitly enabled', async () => {
        // Pins the non-default value on the PUT leg (camelCase→snake_case serialisation).
        const onComplete = vi.fn();
        render(<ConfigWizard {...noop} onComplete={onComplete} isOnboarding={false} initialSection="preferences" />);

        // Master is on by default; turn on the sub-toggle (ships off).
        await screen.findByRole('checkbox', { name: /background pre-transcription/i });
        const fullFile = screen.getByRole('checkbox', { name: /pre-transcribe entire files/i });
        expect(fullFile).not.toBeChecked();
        fireEvent.click(fullFile);
        expect(fullFile).toBeChecked();

        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        const putCall = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.find(
            (c) => c[1]?.method === 'PUT',
        );
        const body = JSON.parse(putCall?.[1]?.body as string);
        // Both fields sent; sub-toggle carries non-default true value.
        expect(body.enable_background_pretranscription).toBe(true);
        expect(body.pretranscribe_full_file).toBe(true);
    });
});

describe('ConfigWizard — background effects preference', () => {
    it('shows a Background Animation checkbox in Preferences, on by default, and persists a toggle to localStorage without an API call', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        const nav = await screen.findByRole('navigation', { name: /settings sections/i });
        fireEvent.click(within(nav).getByRole('button', { name: 'Preferences' }));

        const toggle = await screen.findByRole('checkbox', { name: /background animation/i });
        expect(toggle).toBeChecked();

        await waitFor(() => expect((fetch as unknown as Mock).mock.calls.length).toBeGreaterThan(0));
        const callsBeforeToggle = (fetch as unknown as Mock).mock.calls.length;

        fireEvent.click(toggle);

        expect(toggle).not.toBeChecked();
        expect(localStorage.getItem('engram:backgroundEffectsEnabled')).toBe('false');
        expect((fetch as unknown as Mock).mock.calls.length).toBe(callsBeforeToggle);
    });
});

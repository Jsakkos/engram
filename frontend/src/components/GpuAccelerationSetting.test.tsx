import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AsrStatus } from '../app/components/asrStatus';
import GpuAccelerationSetting from './GpuAccelerationSetting';

const BASE: AsrStatus = {
    device: 'cpu',
    compute_type: 'int8',
    model: 'small',
    workers: 2,
    cpu_threads: 4,
    max_concurrent_matches: 2,
    gpu_detected: true,
    gpu_enabled: false,
    gpu_runtime_installed: false,
    gpu_download_size_bytes: 1.2e9,
    gpu_download: { state: 'idle', downloaded: 0, total: 0, error: null },
    gpu_fallback_reason: null,
    gpu_state: 'available_not_installed',
};

/** Serve `status` from /api/asr-status and record every request. */
function mockApi(status: Partial<AsrStatus>) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
        if (url.includes('/api/asr-status')) {
            return { ok: true, json: async () => ({ ...BASE, ...status }) };
        }
        return { ok: true, json: async () => ({ status: 'downloading' }) };
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

const posted = (fetchMock: ReturnType<typeof mockApi>, path: string) =>
    fetchMock.mock.calls.some(([url, init]) => url === path && init?.method === 'POST');

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('GpuAccelerationSetting', () => {
    // #694: a separate EULA checkbox read as "enable CUDA", but ticking it saved nothing and
    // started nothing. The button alone must start the download, with no checkbox gating it.
    it('starts the download from a single click, with no EULA checkbox', async () => {
        const fetchMock = mockApi({});
        render(<GpuAccelerationSetting />);

        const button = await screen.findByRole('button', { name: /Download & enable/ });
        expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
        expect(button).toBeEnabled();
        expect(screen.getByRole('link', { name: /NVIDIA CUDA EULA/ })).toBeInTheDocument();

        fireEvent.click(button);
        await waitFor(() => expect(posted(fetchMock, '/api/asr/gpu/enable')).toBe(true));
    });

    it('shows an enabled-but-not-restarted GPU as enabled, not as "Enable" again', async () => {
        mockApi({ gpu_enabled: true, gpu_runtime_installed: true, gpu_state: 'restart_pending' });
        render(<GpuAccelerationSetting />);

        expect(await screen.findByText(/Restart the backend/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Enable GPU/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Disable GPU acceleration/ })).toBeInTheDocument();
    });

    it('explains a GPU whose libraries failed to load at startup', async () => {
        mockApi({
            gpu_enabled: true,
            gpu_runtime_installed: true,
            gpu_state: 'enabled_not_active',
            gpu_fallback_reason: 'register_failed',
        });
        render(<GpuAccelerationSetting />);

        const hint = await screen.findByTestId('gpu-fallback-hint');
        expect(hint).toHaveTextContent(/failed to load at startup/);
        expect(screen.queryByRole('button', { name: /Download & enable/ })).not.toBeInTheDocument();
    });

    it('offers the download again when an enabled GPU is missing its libraries', async () => {
        mockApi({
            gpu_enabled: true,
            gpu_state: 'enabled_not_active',
            gpu_fallback_reason: 'runtime_missing',
        });
        render(<GpuAccelerationSetting />);

        expect(await screen.findByTestId('gpu-fallback-hint')).toHaveTextContent(/not installed/);
        expect(screen.getByRole('button', { name: /Download & enable/ })).toBeEnabled();
    });
});

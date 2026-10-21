import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SettingsTransferSection from './SettingsTransferSection';

const WARNING =
    'Anyone with this file can use your MakeMKV, TMDB, AI and Discord credentials. Store it like a password.';

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => handler(url, init));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

beforeEach(() => {
    // jsdom has no object URLs; the download helper only needs them to exist.
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
    vi.unstubAllGlobals();
});

const pick = (text: string, name = 'settings.json') => {
    const input = screen.getByLabelText('Choose a settings file');
    fireEvent.change(input, { target: { files: [new File([text], name, { type: 'application/json' })] } });
};

describe('SettingsTransferSection', () => {
    it('warns only once keys are included', () => {
        render(<SettingsTransferSection onImported={() => {}} />);
        expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox', { name: 'Include keys and passwords' }));
        expect(screen.getByText(WARNING)).toBeInTheDocument();
    });

    it('downloads with the keys flag in the query and nothing else', async () => {
        const fetchMock = stubFetch(() => ({
            ok: true,
            status: 200,
            headers: new Headers({ 'content-disposition': 'attachment; filename="engram-settings-2026-10-20-with-keys.json"' }),
            blob: async () => new Blob(['{}']),
        }));
        render(<SettingsTransferSection onImported={() => {}} />);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Include keys and passwords' }));
        fireEvent.click(screen.getByRole('button', { name: 'Download settings file' }));
        await waitFor(() => expect(fetchMock).toHaveBeenCalled());
        expect(fetchMock.mock.calls[0][0]).toBe('/api/config/export?include_secrets=true');
    });

    it('rejects a file that is not JSON without calling the server', async () => {
        const fetchMock = stubFetch(() => ({ ok: true, status: 200, json: async () => ({}) }));
        render(<SettingsTransferSection onImported={() => {}} />);
        pick('{not json');
        expect(await screen.findByText('That file is not valid JSON.')).toBeInTheDocument();
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('previews a chosen file as JSON and shows the review', async () => {
        const fetchMock = stubFetch(() => ({
            ok: true,
            status: 200,
            json: async () => ({
                schema_version: 1,
                engram_version: '0.41.0',
                includes_secrets: false,
                warnings: [],
                fields: [
                    {
                        key: 'extras_policy', status: 'change', current: 'keep', incoming: 'skip',
                        secret: false, path_missing: false, path_unchecked: false, error: null, selected: true,
                    },
                ],
                unknown: [],
            }),
        }));
        render(<SettingsTransferSection onImported={() => {}} />);
        pick(JSON.stringify({ format: 'engram-settings', settings: { extras_policy: 'skip' } }));
        expect(await screen.findByRole('button', { name: 'Apply 1 setting' })).toBeInTheDocument();
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe('/api/config/import/preview');
        expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' });
    });

    it('shows the server message for a file that is not an Engram settings file', async () => {
        stubFetch(() => ({
            ok: false,
            status: 400,
            json: async () => ({ detail: 'This is not an Engram settings file.' }),
        }));
        render(<SettingsTransferSection onImported={() => {}} />);
        pick(JSON.stringify({ hello: 1 }));
        expect(await screen.findByText('This is not an Engram settings file.')).toBeInTheDocument();
    });

    it('import-only mode hides export', () => {
        render(<SettingsTransferSection onImported={() => {}} importOnly />);
        expect(screen.queryByRole('button', { name: 'Download settings file' })).not.toBeInTheDocument();
        expect(screen.getByLabelText('Choose a settings file')).toBeInTheDocument();
    });
});

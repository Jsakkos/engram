import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ImportSettingsDialog from './ImportSettingsDialog';
import type { ImportPreview, ImportPreviewField } from './settings/settingsTransfer';

const field = (over: Partial<ImportPreviewField>): ImportPreviewField => ({
    key: 'extras_policy',
    status: 'change',
    current: 'keep',
    incoming: 'skip',
    secret: false,
    path_missing: false,
    path_unchecked: false,
    error: null,
    selected: true,
    ...over,
});

const PREVIEW: ImportPreview = {
    schema_version: 1,
    engram_version: '0.41.0',
    includes_secrets: true,
    warnings: [],
    fields: [
        field({ key: 'extras_policy' }),
        field({ key: 'tmdb_api_key', secret: true, current: '***', incoming: '***' }),
        field({ key: 'library_tv_path', current: '', incoming: 'Z:\\TV', path_missing: true, selected: false }),
        field({ key: 'backup_path', incoming: '\\\\nas\\backups', path_unchecked: true, selected: false }),
        field({ key: 'naming_movie_format', status: 'invalid', error: 'naming_movie_format: bad placeholder', selected: false }),
        field({ key: 'always_review', status: 'same', current: true, incoming: true, selected: false }),
    ],
    unknown: ['future_thing'],
};

const FILE = { format: 'engram-settings', settings: {} };

function stubApply(applied: string[] = []) {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
        ok: true,
        status: 200,
        json: async () => ({ applied }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('ImportSettingsDialog', () => {
    it('groups rows by what will happen to them', () => {
        render(<ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={() => {}} />);
        expect(screen.getByRole('heading', { name: /^Changes/ })).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: /Not found on this machine/ })).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: /Network paths/ })).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: /Invalid/ })).toBeInTheDocument();
        expect(screen.getByText(/Unchanged \(1\)/)).toBeInTheDocument();
        expect(screen.getByText(/Not recognized \(1\)/)).toBeInTheDocument();
        expect(screen.getByText('naming_movie_format: bad placeholder')).toBeInTheDocument();
    });

    it('defaults checkboxes from the preview and disables invalid rows', () => {
        render(<ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={() => {}} />);
        expect(screen.getByRole('checkbox', { name: /Extras handling/ })).toBeChecked();
        expect(screen.getByRole('checkbox', { name: /TV library folder/ })).not.toBeChecked();
        expect(screen.getByRole('checkbox', { name: /Disc backup folder/ })).not.toBeChecked();
        expect(screen.getByRole('checkbox', { name: /Movie folder format/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Apply 2 settings' })).toBeInTheDocument();
    });

    it('never shows a secret value, only that it will be replaced', () => {
        render(<ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={() => {}} />);
        expect(screen.getByText('will be replaced')).toBeInTheDocument();
        expect(screen.queryByText('***')).not.toBeInTheDocument();
    });

    it('applies only the ticked keys and updates the count', async () => {
        const fetchMock = stubApply(['tmdb_api_key', 'library_tv_path']);
        const onApplied = vi.fn();
        render(<ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={onApplied} />);

        fireEvent.click(screen.getByRole('checkbox', { name: /Extras handling/ }));
        fireEvent.click(screen.getByRole('checkbox', { name: /TV library folder/ }));
        const apply = screen.getByRole('button', { name: 'Apply 2 settings' });
        fireEvent.click(apply);

        await waitFor(() => expect(onApplied).toHaveBeenCalledWith(['tmdb_api_key', 'library_tv_path']));
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe('/api/config/import/apply');
        expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' });
        expect(JSON.parse(String(init?.body))).toEqual({
            file: FILE,
            keys: ['tmdb_api_key', 'library_tv_path'],
        });
    });

    it('shows per-key errors when the server refuses', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn(async () => ({
                ok: false,
                status: 422,
                json: async () => ({ detail: { errors: { extras_policy: 'extras_policy is broken' } } }),
            })),
        );
        render(<ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={() => {}} />);
        fireEvent.click(screen.getByRole('button', { name: 'Apply 2 settings' }));
        expect(await screen.findByText('extras_policy is broken')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('Some settings could not be applied. Nothing was changed.');
    });

    it('warns that unsaved edits will be replaced', () => {
        render(
            <ImportSettingsDialog file={FILE} preview={PREVIEW} onCancel={() => {}} onApplied={() => {}} hasUnsavedEdits />,
        );
        expect(screen.getByText('Your unsaved changes in Settings will be replaced.')).toBeInTheDocument();
    });

    it('disables apply when nothing is ticked', () => {
        const preview = { ...PREVIEW, fields: [field({ key: 'extras_policy', selected: false })] };
        render(<ImportSettingsDialog file={FILE} preview={preview} onCancel={() => {}} onApplied={() => {}} />);
        expect(screen.getByRole('button', { name: 'Apply 0 settings' })).toBeDisabled();
    });
});

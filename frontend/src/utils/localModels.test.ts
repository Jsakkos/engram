import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchLocalModels, LOCAL_PROVIDERS, localProviderDefaultUrl } from './localModels';

describe('localProviderDefaultUrl', () => {
    it('knows both default ports', () => {
        expect(localProviderDefaultUrl('ollama')).toBe('http://localhost:11434/v1');
        expect(localProviderDefaultUrl('lmstudio')).toBe('http://localhost:1234/v1');
    });

    it('returns an empty string for a remote provider', () => {
        expect(localProviderDefaultUrl('openai')).toBe('');
    });
});

describe('LOCAL_PROVIDERS', () => {
    it('matches the backend slugs', () => {
        expect(LOCAL_PROVIDERS).toEqual(['ollama', 'lmstudio']);
    });
});

describe('fetchLocalModels', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('returns the model list on success', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: ['llama3.1:8b'], error: null }),
        }));

        expect(await fetchLocalModels('ollama', '')).toEqual({
            models: ['llama3.1:8b'],
            error: null,
        });
    });

    it('passes the base URL through as a query parameter', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: [], error: null }),
        });
        vi.stubGlobal('fetch', spy);

        await fetchLocalModels('ollama', 'http://box:11434/v1');

        expect(spy.mock.calls[0][0]).toContain('base_url=http%3A%2F%2Fbox%3A11434%2Fv1');
    });

    it('surfaces the backend error string rather than throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: [], error: 'Could not reach Ollama' }),
        }));

        expect(await fetchLocalModels('ollama', '')).toEqual({
            models: [],
            error: 'Could not reach Ollama',
        });
    });

    it('reports a network failure without throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

        const result = await fetchLocalModels('ollama', '');
        expect(result.models).toEqual([]);
        expect(result.error).toContain('backend');
    });

    it('reports a non-ok response without throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false,
            status: 403,
            json: async () => ({}),
        }));

        const result = await fetchLocalModels('ollama', '');
        expect(result.models).toEqual([]);
        expect(result.error).toContain('403');
    });
});

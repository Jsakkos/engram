import { describe, expect, it, vi, afterEach } from 'vitest';
import { requestAiValidation } from './aiValidation';

afterEach(() => { vi.unstubAllGlobals(); });

function stubFetch(impl: unknown) {
    vi.stubGlobal('fetch', impl);
}

describe('requestAiValidation', () => {
    it('returns valid when the provider accepts the key', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true, version: 'gpt-4o-mini' }),
        }));
        expect(await requestAiValidation('openai', 'sk-x')).toEqual({
            status: 'valid', model: 'gpt-4o-mini',
        });
    });

    it('returns invalid with the backend message when rejected', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: false, error: 'no credits' }),
        }));
        expect(await requestAiValidation('openai', 'sk-x')).toEqual({
            status: 'invalid', error: 'no credits',
        });
    });

    it('omits api_key entirely when the field is blank', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true, version: 'gpt-4o-mini' }),
        });
        stubFetch(spy);
        await requestAiValidation('openai', '   ');
        expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({ provider: 'openai' });
    });

    it('sends the trimmed key when one is entered', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true }),
        });
        stubFetch(spy);
        await requestAiValidation('openai', '  sk-x  ');
        expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
            provider: 'openai', api_key: 'sk-x',
        });
    });

    it('reports a network failure as error, not invalid', async () => {
        stubFetch(vi.fn().mockRejectedValue(new Error('offline')));
        expect((await requestAiValidation('openai', 'sk-x')).status).toBe('error');
    });

    it('reports a non-OK response as error', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: false, status: 403, text: async () => 'forbidden',
        }));
        expect((await requestAiValidation('openai', 'sk-x')).status).toBe('error');
    });

    it('surfaces the backend detail on a 403 instead of a bare status', async () => {
        // The LAN gate fires on every Docker-behind-a-proxy deployment (the peer
        // is the proxy container, never loopback). Showing only "HTTP 403" sent
        // users hunting for a key problem that did not exist.
        stubFetch(vi.fn().mockResolvedValue({
            ok: false,
            status: 403,
            text: async () => JSON.stringify({
                detail: 'This endpoint is only reachable from the host machine. To use it '
                    + 'from another device (e.g. a Docker deployment), enable LAN access in Settings.',
            }),
        }));
        const result = await requestAiValidation('gemini', 'AIzaSy-x');
        expect(result).toMatchObject({ status: 'error' });
        expect(result.status === 'error' && result.error).toContain('enable LAN access');
    });

    it('falls back to the status when the error body has no detail', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: false, status: 502, text: async () => '<html>bad gateway</html>',
        }));
        const result = await requestAiValidation('openai', 'sk-x');
        expect(result).toMatchObject({ status: 'error' });
        expect(result.status === 'error' && result.error).toContain('502');
    });

    it('sends the model when one is entered', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true, version: 'gemini-3.5-flash' }),
        });
        stubFetch(spy);
        await requestAiValidation('gemini', 'AIzaSy-x', '  gemini-3.5-flash  ');
        expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
            provider: 'gemini', api_key: 'AIzaSy-x', model: 'gemini-3.5-flash',
        });
    });

    it('omits model entirely when blank so the backend picks the default', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true }),
        });
        stubFetch(spy);
        await requestAiValidation('gemini', 'AIzaSy-x', '  ');
        expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
            provider: 'gemini', api_key: 'AIzaSy-x',
        });
    });

    it('reports an unparseable body as error', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => { throw new Error('bad json'); },
        }));
        expect((await requestAiValidation('openai', 'sk-x')).status).toBe('error');
    });
});

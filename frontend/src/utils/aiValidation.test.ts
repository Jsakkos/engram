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

    it('reports an unparseable body as error', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => { throw new Error('bad json'); },
        }));
        expect((await requestAiValidation('openai', 'sk-x')).status).toBe('error');
    });
});

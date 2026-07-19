/**
 * Discord notification template validation — live per-keystroke check against
 * POST /api/validate/discord-template. Same three-outcome shape as
 * requestTmdbValidation: 'valid' | 'invalid' (rejected, user must fix) |
 * 'error' (the check itself failed, template was never actually checked).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requestDiscordTemplateValidation } from './discordTemplateValidation';

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  consoleError.mockRestore();
});

function stubFetch(impl: () => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn(impl));
}

describe('requestDiscordTemplateValidation', () => {
  it('returns valid when the endpoint accepts the template', async () => {
    stubFetch(async () => ({ ok: true, json: async () => ({ valid: true }) }));

    expect(await requestDiscordTemplateValidation('{{title}}')).toEqual({ status: 'valid' });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('returns invalid with the backend message when the template is rejected', async () => {
    stubFetch(async () => ({
      ok: true,
      json: async () => ({ valid: false, error: 'Unknown template variable(s): bogus' }),
    }));

    expect(await requestDiscordTemplateValidation('{{bogus}}')).toEqual({
      status: 'invalid',
      error: 'Unknown template variable(s): bogus',
    });
  });

  it('treats a non-OK HTTP response as a failed CHECK, not an invalid template, and logs it', async () => {
    stubFetch(async () => ({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
      json: async () => ({}),
    }));

    const result = await requestDiscordTemplateValidation('{{title}}');
    expect(result.status).toBe('error');
    expect(result.status === 'error' && result.error).toMatch(/couldn't check|could not check/i);
    expect(consoleError).toHaveBeenCalled();
  });

  it('treats a network failure as a failed CHECK and logs the underlying error', async () => {
    const boom = new TypeError('Failed to fetch');
    stubFetch(async () => {
      throw boom;
    });

    const result = await requestDiscordTemplateValidation('{{title}}');
    expect(result.status).toBe('error');
    expect(result.status === 'error' && result.error).toMatch(/reach/i);
    expect(consoleError).toHaveBeenCalledWith(expect.any(String), boom);
  });
});

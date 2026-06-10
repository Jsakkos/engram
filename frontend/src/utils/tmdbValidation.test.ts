/**
 * #243 — TMDB token validation must tell three outcomes apart:
 *  - the token was checked and is valid
 *  - the token was checked and REJECTED (user must fix the token)
 *  - the check itself failed (endpoint unreachable / server error) — the token
 *    was never checked, and the underlying error must be logged, not swallowed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requestTmdbValidation } from './tmdbValidation';

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

describe('requestTmdbValidation', () => {
  it('returns valid when the endpoint validates the token', async () => {
    stubFetch(async () => ({ ok: true, json: async () => ({ valid: true }) }));

    expect(await requestTmdbValidation('eyJtoken')).toEqual({ status: 'valid' });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('returns invalid with the backend message when the token is rejected', async () => {
    stubFetch(async () => ({
      ok: true,
      json: async () => ({ valid: false, error: 'Invalid API key or token' }),
    }));

    expect(await requestTmdbValidation('eyJbad')).toEqual({
      status: 'invalid',
      error: 'Invalid API key or token',
    });
  });

  it('treats a non-OK HTTP response as a failed CHECK, not an invalid token, and logs it', async () => {
    stubFetch(async () => ({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
      json: async () => ({}),
    }));

    const result = await requestTmdbValidation('eyJtoken');
    expect(result.status).toBe('error');
    expect(result.status === 'error' && result.error).toMatch(/couldn't check|could not check/i);
    expect(consoleError).toHaveBeenCalled();
  });

  it('treats a network failure as a failed CHECK and logs the underlying error', async () => {
    const boom = new TypeError('Failed to fetch');
    stubFetch(async () => {
      throw boom;
    });

    const result = await requestTmdbValidation('eyJtoken');
    expect(result.status).toBe('error');
    expect(result.status === 'error' && result.error).toMatch(/reach/i);
    // The underlying error must be logged, not swallowed.
    expect(consoleError).toHaveBeenCalledWith(expect.any(String), boom);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { requestOpenSubtitlesValidation } from './opensubtitlesValidation';

const reply = (body: unknown, status = 200) =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe('requestOpenSubtitlesValidation', () => {
  it('omits blank fields so the backend uses the saved ones', async () => {
    const fetchMock = reply({ valid: true, version: 'Logged in, 5 downloads left today' });
    vi.stubGlobal('fetch', fetchMock);
    const result = await requestOpenSubtitlesValidation('', ' me ', '');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ username: 'me' });
    expect(result).toEqual({ status: 'valid', detail: 'Logged in, 5 downloads left today' });
  });

  it('maps a refused login to invalid with the backend sentence', async () => {
    vi.stubGlobal('fetch', reply({ valid: false, error: 'rejected the username or password' }));
    expect(await requestOpenSubtitlesValidation('k', 'u', 'p')).toEqual({
      status: 'invalid',
      error: 'rejected the username or password',
    });
  });

  it('treats an HTTP failure as a check error, not a bad credential', async () => {
    vi.stubGlobal('fetch', reply({ detail: 'LAN access is off' }, 403));
    const result = await requestOpenSubtitlesValidation('k', 'u', 'p');
    expect(result.status).toBe('error');
    expect(result).toMatchObject({ error: expect.stringContaining('LAN access is off') });
  });

  it('treats a network failure as a check error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    expect((await requestOpenSubtitlesValidation('k', 'u', 'p')).status).toBe('error');
  });
});

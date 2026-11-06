/**
 * OpenSubtitles credential test against POST /api/validate/opensubtitles (#723).
 *
 * Same three-way split as aiValidation.ts: 'valid', 'invalid' (OpenSubtitles
 * answered and refused: bad login, bad key, quota used up) and 'error' (the
 * check itself could not run, so nothing was learned about the credentials).
 * Blank fields are omitted so the backend falls back to the saved value, which
 * is what makes the button useful for credentials already stored.
 */
export type OpenSubtitlesValidationResult =
  | { status: 'valid'; detail?: string }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestOpenSubtitlesValidation(
  apiKey: string,
  username: string,
  password: string,
): Promise<OpenSubtitlesValidationResult> {
  const payload: Record<string, string> = {};
  if (apiKey.trim()) payload.api_key = apiKey.trim();
  if (username.trim()) payload.username = username.trim();
  if (password) payload.password = password;

  let response: Response;
  try {
    response = await fetch('/api/validate/opensubtitles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    return {
      status: 'error',
      error: "Couldn't reach the validation endpoint. Is the backend running?",
    };
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '');
    let detail: string | null = null;
    try {
      const d = JSON.parse(body)?.detail;
      if (typeof d === 'string' && d.trim()) detail = d.trim();
    } catch {
      // Not JSON: the status is all there is.
    }
    return {
      status: 'error',
      error: detail
        ? `Couldn't run the test: ${detail}`
        : `Couldn't run the test: validation endpoint returned HTTP ${response.status}`,
    };
  }

  try {
    const result = await response.json();
    if (result.valid) return { status: 'valid', detail: result.version };
    return { status: 'invalid', error: result.error || 'OpenSubtitles rejected the login' };
  } catch {
    return { status: 'error', error: "Couldn't run the test: unexpected response" };
  }
}

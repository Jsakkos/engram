/**
 * TMDB Read Access Token validation against POST /api/validate/tmdb (#243).
 *
 * Three outcomes, deliberately distinct:
 *  - 'valid'   — TMDB accepted the token
 *  - 'invalid' — the check ran and TMDB REJECTED the token (user must fix it)
 *  - 'error'   — the check itself could not run (endpoint unreachable / server
 *                error); the token was never checked. Conflating this with
 *                'invalid' sends users hunting for a token problem that may
 *                not exist, so the underlying cause is also console.error'd.
 */
export type TmdbValidationResult =
  | { status: 'valid' }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestTmdbValidation(apiKey: string): Promise<TmdbValidationResult> {
  let response: Response;
  try {
    response = await fetch('/api/validate/tmdb', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    });
  } catch (err) {
    console.error('TMDB validation request failed (network):', err);
    return {
      status: 'error',
      error: "Couldn't reach the validation endpoint — is the backend running?",
    };
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    console.error(`TMDB validation endpoint returned HTTP ${response.status}:`, detail);
    return {
      status: 'error',
      error: `Couldn't check the token — validation endpoint returned HTTP ${response.status}`,
    };
  }

  try {
    const result = await response.json();
    if (result.valid) {
      return { status: 'valid' };
    }
    return { status: 'invalid', error: result.error || 'Invalid token' };
  } catch (err) {
    console.error('TMDB validation returned an unparseable response:', err);
    return { status: 'error', error: "Couldn't check the token — unexpected response" };
  }
}

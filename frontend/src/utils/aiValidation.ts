/**
 * AI provider credential validation against POST /api/validate/ai.
 *
 * Three outcomes, deliberately distinct (same split as tmdbValidation.ts):
 *  - 'valid'   — the provider answered a real completion
 *  - 'invalid' — the check ran and the provider REJECTED the credential
 *  - 'error'   — the check itself could not run; nothing was learned about the
 *                key. Conflating this with 'invalid' sends users hunting for a
 *                key problem that may not exist.
 */
export type AiValidationResult =
  | { status: 'valid'; model?: string }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestAiValidation(
  provider: string,
  apiKey: string,
): Promise<AiValidationResult> {
  // Omit the key entirely when blank so the backend falls back to the stored
  // one. Posting "" would be rejected as an empty key, which is what makes the
  // TMDB test button useless for an already-saved token.
  const trimmed = apiKey.trim();
  const payload: Record<string, string> = { provider };
  if (trimmed) payload.api_key = trimmed;

  let response: Response;
  try {
    response = await fetch('/api/validate/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('AI validation request failed (network):', err);
    return {
      status: 'error',
      error: "Couldn't reach the validation endpoint — is the backend running?",
    };
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    console.error(`AI validation endpoint returned HTTP ${response.status}:`, detail);
    return {
      status: 'error',
      error: `Couldn't check the key — validation endpoint returned HTTP ${response.status}`,
    };
  }

  try {
    const result = await response.json();
    if (result.valid) {
      return { status: 'valid', model: result.version };
    }
    return { status: 'invalid', error: result.error || 'Provider rejected the key' };
  } catch (err) {
    console.error('AI validation returned an unparseable response:', err);
    return { status: 'error', error: "Couldn't check the key — unexpected response" };
  }
}

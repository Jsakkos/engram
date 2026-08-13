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

/**
 * Pull the human sentence out of a FastAPI error body.
 *
 * A dependency that raises HTTPException produces `{"detail": "..."}` and no
 * server-side log line, so this string is the only explanation that exists for,
 * say, the LAN gate on this endpoint. Dropping it left users with a bare "HTTP
 * 403" and nothing to act on.
 */
function detailFrom(body: string): string | null {
  try {
    const parsed = JSON.parse(body);
    const detail = parsed?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
  } catch {
    // Not JSON (a proxy's HTML error page, say) — the status is all we have.
  }
  return null;
}

export async function requestAiValidation(
  provider: string,
  apiKey: string,
  model?: string,
): Promise<AiValidationResult> {
  // Omit the key entirely when blank so the backend falls back to the stored
  // one. Posting "" would be rejected as an empty key, which is what makes the
  // TMDB test button useless for an already-saved token. The model follows the
  // same rule: blank means "let the backend pick the provider default".
  const trimmed = apiKey.trim();
  const trimmedModel = (model ?? '').trim();
  const payload: Record<string, string> = { provider };
  if (trimmed) payload.api_key = trimmed;
  if (trimmedModel) payload.model = trimmedModel;

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
    const body = await response.text().catch(() => '');
    console.error(`AI validation endpoint returned HTTP ${response.status}:`, body);
    const detail = detailFrom(body);
    return {
      status: 'error',
      error: detail
        ? `Couldn't check the key — ${detail}`
        : `Couldn't check the key — validation endpoint returned HTTP ${response.status}`,
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

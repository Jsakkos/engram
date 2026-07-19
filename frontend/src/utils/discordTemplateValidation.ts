/**
 * Discord notification template validation against POST /api/validate/discord-template.
 *
 * Same three-outcome shape as requestTmdbValidation — 'valid' | 'invalid' (the
 * server rejected the template) | 'error' (the check itself couldn't run, the
 * template was never actually validated).
 */
export type DiscordTemplateValidationResult =
  | { status: 'valid' }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestDiscordTemplateValidation(
  template: string,
): Promise<DiscordTemplateValidationResult> {
  let response: Response;
  try {
    response = await fetch('/api/validate/discord-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template }),
    });
  } catch (err) {
    console.error('Discord template validation request failed (network):', err);
    return {
      status: 'error',
      error: "Couldn't reach the validation endpoint — is the backend running?",
    };
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    console.error(`Discord template validation endpoint returned HTTP ${response.status}:`, detail);
    return {
      status: 'error',
      error: `Couldn't check the template — validation endpoint returned HTTP ${response.status}`,
    };
  }

  try {
    const result = await response.json();
    if (result.valid) {
      return { status: 'valid' };
    }
    return { status: 'invalid', error: result.error || 'Invalid template' };
  } catch (err) {
    console.error('Discord template validation returned an unparseable response:', err);
    return { status: 'error', error: "Couldn't check the template — unexpected response" };
  }
}

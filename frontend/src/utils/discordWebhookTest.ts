/**
 * Discord webhook smoke test against POST /api/validate/discord-webhook.
 *
 * Same three-outcome shape as requestDiscordTemplateValidation: 'ok' | 'invalid'
 * (the server could not post) | 'error' (the check itself could not run).
 */
export type DiscordWebhookTestResult =
  | { status: 'ok' }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

/**
 * Tests the SAVED webhook. Deliberately sends no URL: letting the caller name
 * the destination would make an outbound server request steerable from a
 * request body. It also tests the right thing, since the saved value is the one
 * real notifications use.
 */
export async function requestDiscordWebhookTest(): Promise<DiscordWebhookTestResult> {
  let response: Response;
  try {
    response = await fetch('/api/validate/discord-webhook', { method: 'POST' });
  } catch (err) {
    console.error('Discord webhook test request failed (network):', err);
    return { status: 'error', error: "Couldn't reach the backend" };
  }

  if (!response.ok) {
    return { status: 'error', error: `Test endpoint returned HTTP ${response.status}` };
  }

  try {
    const result = await response.json();
    return result.valid
      ? { status: 'ok' }
      : { status: 'invalid', error: result.error || 'Webhook test failed' };
  } catch {
    return { status: 'error', error: "Couldn't read the response" };
  }
}

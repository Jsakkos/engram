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

export async function requestDiscordWebhookTest(
  webhookUrl: string,
): Promise<DiscordWebhookTestResult> {
  let response: Response;
  try {
    response = await fetch('/api/validate/discord-webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Blank means "use the saved webhook": GET /api/config redacts it to "***",
      // so there is nothing for the form to send back.
      body: JSON.stringify({ webhook_url: webhookUrl || null }),
    });
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

import { test, expect } from "@playwright/test";

/**
 * E2E spec for the JIT fingerprint-disclosure modal (I1.1).
 *
 * The modal is triggered by a WebSocket `fingerprint_disclosure_required` event
 * emitted during a ContributionUploader drain when:
 *   - There are pending rows in fingerprint_contributions, AND
 *   - fingerprint_disclosure_accepted is False.
 *
 * The test sequence:
 *   1. Reset disclosure config on the backend.
 *   2. Load the page (WebSocket connects).
 *   3. Seed a pending fingerprint row via the debug endpoint.
 *   4. Trigger a drain via the debug endpoint — WS event fires.
 *   5. Assert modal appears; interact with it.
 *
 * Requires a DEBUG backend (port 8001) and a running Vite dev server.
 * Run with: npm run test:e2e (starts both servers via playwright.config.ts).
 */

// The E2E backend always runs on port 8001 (see playwright.config.ts / api-helpers.ts)
const API = "http://localhost:8001";

async function resetDisclosure(
    request: import("@playwright/test").APIRequestContext,
    accepted: boolean,
): Promise<void> {
    await request.put(`${API}/api/config`, {
        data: {
            fingerprint_disclosure_accepted: accepted,
            enable_fingerprint_contributions: true,
            fingerprint_server_url: "https://fp.example.com",
        },
    });
}

// Tests share one backend DB and run serially (workers: 1). The "Decline" test
// leaves enable_fingerprint_contributions=false; restore a clean baseline after
// each test so later specs (e.g. fingerprint-toggle, which asserts the toggle is
// checked by default) don't inherit the disabled/declined state.
test.afterEach(async ({ request }) => {
    await request.put(`${API}/api/config`, {
        data: {
            enable_fingerprint_contributions: true,
            fingerprint_disclosure_accepted: true,
        },
    });
});

test("first queued contribution triggers the disclosure modal; Accept dismisses it", async ({
    page,
}) => {
    await resetDisclosure(page.request, false);
    await page.goto("/");

    // Wait for WS connection before triggering the drain
    await expect(page.locator("text=/LIVE/i")).toBeVisible({ timeout: 10000 });

    await page.request.post(`${API}/api/debug/fingerprint/seed`);
    await page.request.post(`${API}/api/debug/uploader/drain`);

    // Modal should appear via WS push
    await expect(
        page.getByRole("dialog", { name: /contributing audio fingerprints/i }),
    ).toBeVisible({ timeout: 10000 });

    // Accept consent
    await page.getByRole("button", { name: /accept and start contributing/i }).click();

    // Modal should close
    await expect(
        page.getByRole("dialog", { name: /contributing audio fingerprints/i }),
    ).toBeHidden();

    // Config must reflect accepted = true
    const cfg = await page.request.get(`${API}/api/config`).then((r) => r.json());
    expect(cfg.fingerprint_disclosure_accepted).toBe(true);
});

test("Decline disables contributions", async ({ page }) => {
    await resetDisclosure(page.request, false);
    await page.goto("/");

    // Wait for WS connection before triggering the drain
    await expect(page.locator("text=/LIVE/i")).toBeVisible({ timeout: 10000 });

    await page.request.post(`${API}/api/debug/fingerprint/seed`);
    await page.request.post(`${API}/api/debug/uploader/drain`);

    // Modal should appear via WS push
    await expect(
        page.getByRole("dialog", { name: /contributing audio fingerprints/i }),
    ).toBeVisible({ timeout: 10000 });

    // Decline — disables contributions
    await page.getByRole("button", { name: /disable contributions/i }).click();

    // Config must reflect contributions disabled
    const cfg = await page.request.get(`${API}/api/config`).then((r) => r.json());
    expect(cfg.enable_fingerprint_contributions).toBe(false);
});

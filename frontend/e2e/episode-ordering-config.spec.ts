import { test, expect } from '@playwright/test';

/**
 * Global "Episode Ordering" default selector in ConfigWizard (#255).
 *
 * Distinct from the per-show selector in the Review Queue (episode-ordering.spec.ts):
 * this is the *global* default on the Preferences tab (step 4). It interacts with the
 * real Settings modal and round-trips through GET/PUT /api/config — no route mocking.
 *
 * Tests verify:
 *   - The select renders on the Preferences tab and shows the aired default.
 *   - Switching to "DVD Order" and saving persists across a page reload.
 *   - Restores the aired default afterwards to leave global config clean.
 */

// The E2E backend always runs on port 8001 (see playwright.config.ts).
const API = 'http://localhost:8001';

async function openSettingsPreferences(page: import('@playwright/test').Page) {
    await page.locator('[data-testid="sv-settings-btn"]').click();
    await expect(page.getByRole('heading', { level: 2, name: 'Settings' })).toBeVisible({ timeout: 5000 });
    // Wait for config to load before navigating — the shared E2E backend can stall
    // briefly on startup work, leaving the body on "Loading configuration…".
    await expect(page.locator('.wizard-loading')).not.toBeVisible({ timeout: 10000 });
    // Settings opens with a section sidebar; jump straight to the Preferences section.
    await page
        .getByRole('navigation', { name: 'Settings sections' })
        .getByRole('button', { name: 'Preferences' })
        .click();
    await expect(page.getByText(/processed locally on this machine/i)).toBeVisible({
        timeout: 3000,
    });
}

async function selectEpisodeOrdering(page: import('@playwright/test').Page, optionName: RegExp) {
    // EngramSelect is a Radix Select: click the trigger, then the option in the portal.
    await page.locator('#episodeOrdering').click();
    await page.getByRole('option', { name: optionName }).click();
}

async function saveChanges(page: import('@playwright/test').Page) {
    await page.getByRole('button', { name: /Save Changes/i }).click();
    // Modal closes on a successful save.
    await expect(page.locator('[data-testid="sv-settings-btn"]')).toBeVisible({ timeout: 5000 });
}

test.describe('Global episode ordering default', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('/');
        // Wait for the WebSocket connection indicator before interacting.
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
    });

    // Tests share one backend DB and run serially (workers: 1). Restore the aired
    // default directly via the API after each test so a mid-test failure (which
    // could leave "dvd" persisted) doesn't bleed into later specs.
    test.afterEach(async ({ request }) => {
        await request.put(`${API}/api/config`, {
            data: { episode_ordering_preference: 'aired' },
        });
    });

    test('defaults to aired and persists DVD across reload', async ({ page }) => {
        // --- Step 1: open settings and normalise to the aired default ---
        await openSettingsPreferences(page);
        const trigger = page.locator('#episodeOrdering');
        await expect(trigger).toBeVisible();

        await selectEpisodeOrdering(page, /Aired Order/);
        await expect(trigger).toContainText('Aired Order');
        await saveChanges(page);

        // --- Step 2: switch to DVD Order and save ---
        await openSettingsPreferences(page);
        await selectEpisodeOrdering(page, /DVD Order/);
        await expect(page.locator('#episodeOrdering')).toContainText('DVD Order');
        await saveChanges(page);

        // --- Step 3: reload and confirm DVD persisted via GET /api/config ---
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
        await openSettingsPreferences(page);
        await expect(page.locator('#episodeOrdering')).toContainText('DVD Order');
        // The aired default is restored by afterEach via the API.
    });
});

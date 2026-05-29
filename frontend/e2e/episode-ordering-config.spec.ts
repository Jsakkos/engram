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

async function openSettingsPreferences(page: import('@playwright/test').Page) {
    await page.locator('[data-testid="sv-settings-btn"]').click();
    await expect(page.getByText('Preferences')).toBeVisible({ timeout: 5000 });
    // In settings mode all tabs are clickable; jump straight to step 4.
    await page.getByRole('button', { name: /Step 4: Preferences/i }).click();
    await expect(page.getByText('Configure additional options for your workflow')).toBeVisible({
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

    test('defaults to aired, persists DVD across reload, then restores aired', async ({ page }) => {
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

        // --- Cleanup: restore the aired default so other tests start clean ---
        await selectEpisodeOrdering(page, /Aired Order/);
        await expect(page.locator('#episodeOrdering')).toContainText('Aired Order');
        await saveChanges(page);
    });
});

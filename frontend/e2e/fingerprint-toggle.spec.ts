import { test, expect } from '@playwright/test';

/**
 * E2E tests for the "Contribute audio fingerprints" opt-out toggle in ConfigWizard.
 *
 * These tests do not require disc simulation — they interact directly with the
 * Settings modal (non-onboarding ConfigWizard). The toggle is on the Preferences
 * tab (step 4).
 *
 * Tests verify:
 *   - The toggle is checked by default (opt-in).
 *   - Unchecking and saving persists the false value across page reloads.
 *   - Re-checking and saving restores the true value (full round-trip).
 */

async function openSettingsDataSharing(page: import('@playwright/test').Page) {
    // Open the Settings modal
    await page.locator('[data-testid="sv-settings-btn"]').click();
    // ConfigWizard should be visible — wait for the modal heading
    await expect(page.getByText('Data Sharing')).toBeVisible({ timeout: 5000 });
    // Navigate to the Data Sharing tab (step 4) — in settings mode all tabs are clickable
    await page.getByRole('button', { name: /Step 4: Data Sharing/i }).click();
    // Confirm we're on the right step
    await expect(page.getByText(/governs data that leaves your machine/i)).toBeVisible({ timeout: 3000 });
}

async function saveFingerprintToggle(page: import('@playwright/test').Page) {
    await page.getByRole('button', { name: /Save Changes/i }).click();
    // Modal should close after a successful save
    await expect(page.locator('[data-testid="sv-settings-btn"]')).toBeVisible({ timeout: 5000 });
}

test.describe('Fingerprint contributions toggle', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('/');
        // Wait for the WebSocket connection indicator before interacting
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
    });

    test('toggle is checked by default (opt-in)', async ({ page }) => {
        await openSettingsDataSharing(page);

        const checkbox = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(checkbox).toBeVisible();
        await expect(checkbox).toBeChecked();
    });

    test('unchecking and saving persists false across reload', async ({ page }) => {
        // --- Step 1: uncheck the toggle and save ---
        await openSettingsDataSharing(page);

        const checkbox = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(checkbox).toBeVisible();

        // Ensure it starts checked before unchecking
        if (await checkbox.isChecked()) {
            await checkbox.uncheck();
        }
        await expect(checkbox).not.toBeChecked();

        await saveFingerprintToggle(page);

        // --- Step 2: reload and re-open settings to confirm it's still unchecked ---
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });

        await openSettingsDataSharing(page);

        const checkboxAfterReload = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(checkboxAfterReload).not.toBeChecked();
    });

    test('re-checking and saving restores true (full round-trip)', async ({ page }) => {
        // --- Step 1: uncheck and save (set to false) ---
        await openSettingsDataSharing(page);

        let checkbox = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(checkbox).toBeVisible();
        if (await checkbox.isChecked()) {
            await checkbox.uncheck();
        }
        await expect(checkbox).not.toBeChecked();
        await saveFingerprintToggle(page);

        // --- Step 2: reload, re-open, re-check, and save (set to true) ---
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });

        await openSettingsDataSharing(page);

        checkbox = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await checkbox.check();
        await expect(checkbox).toBeChecked();
        await saveFingerprintToggle(page);

        // --- Step 3: reload one more time and confirm it's checked ---
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });

        await openSettingsDataSharing(page);

        const checkboxFinal = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(checkboxFinal).toBeChecked();
    });
});

import { test, expect } from '@playwright/test';

/**
 * E2E tests for the "Auto-Eject Disc When Ripping Completes" toggle in ConfigWizard.
 *
 * These tests interact directly with the Settings modal (non-onboarding ConfigWizard).
 * The toggle is in the Preferences section → Maintenance & watchdog subsection.
 *
 * Tests verify:
 *   - The toggle is checked by default (auto-eject enabled).
 *   - Unchecking and saving persists the false value across page reloads.
 *   - Re-checking and saving restores the true value (full round-trip).
 */

async function openSettingsPreferences(page: import('@playwright/test').Page) {
    await page.locator('[data-testid="sv-settings-btn"]').click();
    await expect(page.getByRole('heading', { level: 2, name: 'Settings' })).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.wizard-loading')).not.toBeVisible({ timeout: 10000 });
    await page
        .getByRole('navigation', { name: 'Settings sections' })
        .getByRole('button', { name: 'Preferences' })
        .click();
    await expect(page.getByText(/How Engram matches, names, and tidies up/i)).toBeVisible({ timeout: 3000 });
    // Expand Maintenance & watchdog if collapsed
    const summary = page.getByRole('group').filter({ hasText: /Maintenance.*watchdog/i }).locator('summary');
    const isOpen = await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open);
    if (!isOpen) {
        await summary.click();
    }
}

async function savePreferences(page: import('@playwright/test').Page) {
    await page.getByRole('button', { name: /Save Changes/i }).click();
    await expect(page.locator('[data-testid="sv-settings-btn"]')).toBeVisible({ timeout: 5000 });
}

test.describe('Auto-eject disc toggle', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
    });

    test('toggle is checked by default (auto-eject on)', async ({ page }) => {
        await openSettingsPreferences(page);

        const checkbox = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await expect(checkbox).toBeVisible();
        await expect(checkbox).toBeChecked();
    });

    test('unchecking and saving persists false across reload', async ({ page }) => {
        await openSettingsPreferences(page);

        const checkbox = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await expect(checkbox).toBeVisible();

        if (await checkbox.isChecked()) {
            await checkbox.uncheck();
        }
        await expect(checkbox).not.toBeChecked();
        await savePreferences(page);

        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
        await openSettingsPreferences(page);

        const checkboxAfterReload = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await expect(checkboxAfterReload).not.toBeChecked();
    });

    test('re-checking and saving restores true (full round-trip)', async ({ page }) => {
        // Step 1: uncheck and save (set to false)
        await openSettingsPreferences(page);

        let checkbox = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await expect(checkbox).toBeVisible();
        if (await checkbox.isChecked()) {
            await checkbox.uncheck();
        }
        await expect(checkbox).not.toBeChecked();
        await savePreferences(page);

        // Step 2: reload, re-open, re-check, and save (set to true)
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
        await openSettingsPreferences(page);

        checkbox = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await checkbox.check();
        await expect(checkbox).toBeChecked();
        await savePreferences(page);

        // Step 3: reload one more time and confirm it's checked
        await page.reload();
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
        await openSettingsPreferences(page);

        const checkboxFinal = page.getByRole('checkbox', { name: /Auto-Eject Disc When Ripping Completes/i });
        await expect(checkboxFinal).toBeChecked();
    });
});

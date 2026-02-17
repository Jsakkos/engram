import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { AMBIGUOUS_DISC } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Review Flow - Engram UI', () => {
    test('ambiguous disc shows ANALYZING badge', async ({ page }) => {
        // Insert ambiguous disc (unknown content type)
        await simulateInsertDisc(AMBIGUOUS_DISC);

        // Wait for card to appear
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Should show disc label
        await expect(page.locator(SELECTORS.discTitle).first()).toBeVisible();

        // Should show ANALYZING badge for unknown content type
        await expect(page.getByText('ANALYZING').first()).toBeVisible({ timeout: 10000 });
    });

    test('disc card appears and displays basic info', async ({ page }) => {
        // Use ambiguous disc for testing
        const { job_id } = await simulateInsertDisc({
            ...AMBIGUOUS_DISC,
            simulate_ripping: false,
        });

        // Wait for card
        await page.waitForTimeout(2000);

        // Card should be visible
        const card = page.locator(SELECTORS.discCard).first();
        await expect(card).toBeVisible();

        // Should show disc title/label
        await expect(page.locator(SELECTORS.discTitle).first()).toBeVisible();

        // Should show subtitle with volume label
        await expect(page.locator(SELECTORS.discSubtitle).first()).toBeVisible();
    });

    test.skip('navigate to review page (requires review state)', async ({ page }) => {
        // Note: This test requires the backend to actually put a job into review_needed state,
        // which may not happen with simulation. Marked as skip for now.

        const { job_id } = await simulateInsertDisc({
            ...AMBIGUOUS_DISC,
            simulate_ripping: false,
        });

        // Manual navigation test - if review button exists
        await page.goto(`/review/${job_id}`);

        // Should load review page (even if no matches yet)
        await expect(page.locator('h1, h2')).toContainText(/review/i, { timeout: 5000 });
    });
});

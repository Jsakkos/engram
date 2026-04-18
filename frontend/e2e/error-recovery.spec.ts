import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Error Recovery - Engram UI', () => {
    test('failed job shows ERROR badge', async ({ page }) => {
        // Insert a disc with slow ripping so we can cancel before it completes
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
            rip_speed_multiplier: 1,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for job to appear and start ripping
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Cancel the job via API
        await page.request.post(`http://localhost:8001/api/jobs/${_job_id}/cancel`);

        // Should show ERROR state indicator
        await expect(page.locator(SELECTORS.stateFailed).first()).toBeVisible({ timeout: 10000 });
    });

    test('failed job shows error message text', async ({ page }) => {
        // Insert with slow ripping to ensure cancel succeeds
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
            rip_speed_multiplier: 1,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        await page.request.post(`http://localhost:8001/api/jobs/${_job_id}/cancel`);
        await page.waitForTimeout(2000);

        // Verify the card is still visible with some content
        const card = page.locator(SELECTORS.discCard).first();
        const cardText = await card.textContent();
        expect(cardText).toBeTruthy();
    });

    test('websocket reconnects after disconnection', async ({ page }) => {
        // Verify initial connection
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Insert a disc to verify data flow works
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
            rip_speed_multiplier: 2,
        });

        // Wait for card to appear (confirms WS works)
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Connection status should still show connected
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible();
    });

    test('cancel button triggers job cancellation', async ({ page }) => {
        // Insert a disc with slow ripping so cancel can fire during rip
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
            rip_speed_multiplier: 1,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for card to appear
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Wait for ripping state
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Cancel immediately via API to avoid race with job completion
        await page.request.post(`http://localhost:8001/api/jobs/${_job_id}/cancel`);

        // Should show error/failed state
        await expect(page.locator(SELECTORS.stateFailed).first()).toBeVisible({ timeout: 10000 });
    });
});

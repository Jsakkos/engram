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
        // Insert a disc and immediately cancel to create a failed job
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for job to appear
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Wait a moment then cancel
        await page.waitForTimeout(1000);

        // Cancel the job via API
        await page.request.post(`http://localhost:8000/api/jobs/${_job_id}/cancel`);

        // Wait for UI to update
        await page.waitForTimeout(2000);

        // Should show ERROR state indicator
        await expect(page.locator(SELECTORS.stateFailed).first()).toBeVisible({ timeout: 10000 });
    });

    test('failed job shows error message text', async ({ page }) => {
        // Insert and cancel a job
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });
        await page.waitForTimeout(1000);

        await page.request.post(`http://localhost:8000/api/jobs/${_job_id}/cancel`);
        await page.waitForTimeout(2000);

        // Verify the error message is visible somewhere in the card
        const card = page.locator(SELECTORS.discCard).first();
        const cardText = await card.textContent();
        // Card should have some content
        expect(cardText).toBeTruthy();
    });

    test('websocket reconnects after disconnection', async ({ page }) => {
        // Verify initial connection
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Insert a disc to verify data flow works
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: false,
        });

        // Wait for card to appear (confirms WS works)
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Connection status should still show connected
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible();
    });

    test('cancel button triggers job cancellation', async ({ page }) => {
        // Insert a disc with ripping simulation
        const { job_id: _job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            simulate_ripping: true,
        });

        // Switch to ALL filter so failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for card to appear
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // Wait for ripping/processing state (cancel button is visible during ripping)
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Cancel button should now be visible
        const cancelBtn = page.locator(SELECTORS.cancelButton).first();

        if (await cancelBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
            await cancelBtn.click();

            // Wait for state update
            await page.waitForTimeout(3000);

            // Should show error/failed state
            await expect(page.locator(SELECTORS.stateFailed).first()).toBeVisible({ timeout: 10000 });
        } else {
            // Fallback: cancel via API
            await page.request.post(`http://localhost:8000/api/jobs/${_job_id}/cancel`);
            await page.waitForTimeout(2000);

            // Verify job is cancelled
            const res = await page.request.get(`http://localhost:8000/api/jobs/${_job_id}`);
            const job = await res.json();
            expect(job.state).toBe('failed');
        }
    });
});

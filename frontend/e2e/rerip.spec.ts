import { test, expect } from '@playwright/test';
import { resetAllJobs, seedIncompleteRip } from './fixtures/api-helpers';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async () => {
    await resetAllJobs().catch(() => {});
});

test.describe('Re-rip affordance — damaged track', () => {
    test('damaged track shows re-rip notice and button in review queue', async ({ page }) => {
        const { job_id } = await seedIncompleteRip();

        // Navigate directly to the review queue for the seeded REVIEW_NEEDED job.
        // The ReviewQueue renders at /review/<job_id> for any job (does not require
        // the job to be actively ripping — the seed puts it straight into REVIEW_NEEDED).
        await page.goto(`/review/${job_id}`);

        // The damaged-track notice must be present and visible
        await expect(page.getByTestId('damaged-track-notice')).toBeVisible({ timeout: 10000 });

        // The re-rip action button must be present and visible
        await expect(page.getByTestId('rerip-button')).toBeVisible({ timeout: 10000 });
    });

    test('damaged badge appears on the dashboard disc card', async ({ page }) => {
        // Seed BEFORE navigating so the initial /api/jobs fetch already includes the job.
        // This avoids relying solely on the debounced WS-triggered REST refetch.
        await seedIncompleteRip();

        await page.goto('/');
        // Wait for the initial /api/jobs response to confirm the page has loaded job data.
        await page.waitForResponse(r => r.url().includes('/api/jobs') && r.ok());

        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // The seeded card must be visible
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // The damaged badge must appear on the card (filtered to just the card)
        const card = page.locator(SELECTORS.discCard).first();
        await expect(card.getByTestId('disccard-damaged-badge')).toBeVisible({ timeout: 10000 });
    });
});

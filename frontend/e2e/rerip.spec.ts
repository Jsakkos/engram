import { test, expect } from '@playwright/test';
import { resetAllJobs } from './fixtures/api-helpers';
import { SELECTORS } from './fixtures/selectors';

const API_BASE = 'http://localhost:8001';

/**
 * Seed a REVIEW_NEEDED job with one incomplete_rip review title.
 * Returns { job_id, title_id }.
 */
async function seedIncompleteRip(
    volumeLabel = 'DAMAGED_DISC_S1D1',
): Promise<{ job_id: number; title_id: number }> {
    const res = await fetch(
        `${API_BASE}/api/simulate/seed-incomplete-rip?volume_label=${encodeURIComponent(volumeLabel)}`,
        { method: 'POST' },
    );
    if (!res.ok) {
        throw new Error(`seed-incomplete-rip failed: ${res.status} ${await res.text()}`);
    }
    return res.json();
}

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
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
        await seedIncompleteRip();

        // The dashboard is already open — the seed broadcasts a job_update so the
        // card appears without a manual page refresh.
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

        // The damaged badge must appear on the card (filtered to just the card)
        const card = page.locator(SELECTORS.discCard).first();
        await expect(card.getByTestId('disccard-damaged-badge')).toBeVisible({ timeout: 10000 });
    });
});

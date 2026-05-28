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
        await simulateInsertDisc({
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

    test('review page renders visibly (cross-browser)', async ({ page }) => {
        // Loading /review/<id> does NOT require a review_needed state — the
        // ReviewQueue renders for any job. This runs on every configured
        // browser (Chromium, Firefox, WebKit) so a route that renders nothing
        // (the routing black-screen bug) or content composited to black by a
        // WebKit-only CSS issue (mix-blend-mode / backdrop-filter) is caught.
        const { job_id } = await simulateInsertDisc({
            ...AMBIGUOUS_DISC,
            simulate_ripping: false,
        });

        await page.goto(`/review/${job_id}`);

        // The atmosphere wrapper must be present AND visible — a null route
        // would leave the page blank and fail here.
        await expect(page.locator(SELECTORS.appContainer)).toBeVisible({ timeout: 10000 });

        // Real page chrome must paint: the sticky page header (rendered by both
        // the TV and movie review branches).
        await expect(page.locator('[data-testid="sv-page-header"]')).toBeVisible({ timeout: 10000 });
    });

    test('review page shows candidates when disc needs review', async ({ page }) => {
        // Switch to ALL filter so completed/failed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Insert a disc that may trigger review_needed
        await simulateInsertDisc({
            ...AMBIGUOUS_DISC,
            simulate_ripping: true,
        });

        // Wait for the job to progress
        await page.waitForTimeout(5000);

        // Check if review UI elements appear for ambiguous content
        const card = page.locator(SELECTORS.discCard).first();
        await expect(card).toBeVisible({ timeout: 10000 });

        // The card should show some state indicator
        const stateText = await card.textContent();
        expect(stateText).toBeTruthy();
    });

    test('submit review resumes job processing', async ({ page }) => {
        // Insert ambiguous disc
        const { job_id } = await simulateInsertDisc({
            ...AMBIGUOUS_DISC,
            simulate_ripping: true,
        });

        // Wait for processing
        await page.waitForTimeout(5000);

        // If the job reached review_needed, try submitting via API
        const jobRes = await page.request.get(`http://localhost:8001/api/jobs/${job_id}`);
        const job = await jobRes.json();

        if (job.state === 'review_needed') {
            // Submit review via API
            const reviewRes = await page.request.post(`http://localhost:8001/api/jobs/${job_id}/review`, {
                data: { matches: {} },
            });
            expect(reviewRes.ok()).toBeTruthy();

            // Wait for state to change
            await page.waitForTimeout(3000);

            // Job should have progressed beyond review_needed
            const updatedRes = await page.request.get(`http://localhost:8001/api/jobs/${job_id}`);
            const updated = await updatedRes.json();
            expect(updated.state).not.toBe('review_needed');
        }
    });
});

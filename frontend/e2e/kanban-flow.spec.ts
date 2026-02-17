import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT, MOVIE_DISC } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    // Reset all jobs (including active/ripping) to start with a clean slate
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    // Wait for connection indicator
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Kanban Flow - Engram UI', () => {
    test('TV disc flows through states with track-level detail', async ({ page }) => {
        // Insert simulated TV disc at moderate speed to observe intermediate states
        const { job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 5,
        });

        // Card should appear
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 5000 });

        // Should show TV badge
        await expect(page.locator(SELECTORS.tvBadge).first()).toBeVisible();

        // Should show detected title
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('Arrested Development');

        // Wait for ripping to show progress
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Should see per-track RIPPING labels in track grid
        await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 10000 });
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Wait for LISTENING state (transcribing phase)
        await expect(
            page.getByText(/LISTENING/i).first()
        ).toBeVisible({ timeout: 30000 });

        // Wait for match results (MATCHED state or episode codes)
        await expect(
            page.locator(SELECTORS.matchCandidate).first()
        ).toBeVisible({ timeout: 30000 });

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 60000 });
    });

    test('Movie disc flows through to completion', async ({ page }) => {
        const { job_id } = await simulateInsertDisc(MOVIE_DISC);

        // Card should appear with MOVIE badge
        await expect(page.locator(SELECTORS.movieBadge).first()).toBeVisible({ timeout: 5000 });
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('Inception');

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 30000 });
    });

    test('Filter buttons work correctly', async ({ page }) => {
        // Insert a disc with fast completion
        await simulateInsertDisc({ ...MOVIE_DISC, rip_speed_multiplier: 100 });

        // Switch to ALL filter first to see the completed state
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 30000 });

        // Test ACTIVE filter (should hide completed)
        await page.locator(SELECTORS.filterActive).click();
        await expect(page.locator(SELECTORS.discCard)).not.toBeVisible({ timeout: 2000 });

        // Test DONE filter (should show completed)
        await page.locator(SELECTORS.filterDone).click();
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 2000 });

        // Test ALL filter (should show all)
        await page.locator(SELECTORS.filterAll).click();
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 2000 });
    });

    test('Empty state displays correctly', async ({ page }) => {
        // Should show empty state on active filter (reset already ran in beforeEach)
        await page.locator(SELECTORS.filterActive).click();
        await expect(page.getByRole('heading', { name: /NO DISCS DETECTED/i })).toBeVisible({ timeout: 5000 });

        // Should show appropriate empty state on done filter
        await page.locator(SELECTORS.filterDone).click();
        await expect(page.getByText(/NO COMPLETED ARCHIVES/i).first()).toBeVisible({ timeout: 5000 });
    });

    test('Multiple discs display simultaneously', async ({ page }) => {
        // Insert two discs on different drives to avoid conflicts
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            drive_id: 'E:',
            rip_speed_multiplier: 1, // Slow
        });
        // Small delay to avoid race conditions in backend
        await page.waitForTimeout(500);
        await simulateInsertDisc({
            ...MOVIE_DISC,
            drive_id: 'F:',
            rip_speed_multiplier: 1, // Slow
        });

        // Wait for both cards to appear
        await expect(page.locator(SELECTORS.discCard)).toHaveCount(2, { timeout: 15000 });

        // Should show both TV and MOVIE badges
        await expect(page.locator(SELECTORS.tvBadge).first()).toBeVisible();
        await expect(page.locator(SELECTORS.movieBadge).first()).toBeVisible();
    });

    test('Progress percentage displays and updates', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 2, // Medium speed for visible progress
        });

        // Wait for ripping to start
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Should show progress percentage
        await expect(page.locator(SELECTORS.progressPercentage).first()).toBeVisible({ timeout: 5000 });
    });
});

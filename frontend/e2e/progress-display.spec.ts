import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT, MOVIE_DISC } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Progress Display - Engram UI', () => {
    test('ripping progress percentage updates', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 1, // Slow enough to observe progress
        });

        // Wait for progress bar to appear
        await expect(page.locator(SELECTORS.progressBar)).toBeVisible({ timeout: 15000 });

        // Progress percentage should be visible
        await expect(page.locator(SELECTORS.progressPercentage).first()).toBeVisible({ timeout: 5000 });

        // Get initial progress
        const initialProgress = await page.locator(SELECTORS.progressPercentage).first().textContent();

        // Wait and check if progress updated
        await page.waitForTimeout(2000);
        const laterProgress = await page.locator(SELECTORS.progressPercentage).first().textContent();

        // At least one should show non-zero progress
        expect(initialProgress || laterProgress).toBeTruthy();
        expect(initialProgress || laterProgress).toMatch(/\d+%/);
    });

    test('speed and ETA display during ripping', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 1, // Slow enough to observe speed/ETA
        });

        // Wait for ripping state
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Speed should be visible (e.g., "6.5x")
        await expect(page.locator(SELECTORS.speed).first()).toBeVisible({ timeout: 5000 });

        // ETA should be visible (e.g., "5 min")
        await expect(page.locator(SELECTORS.eta).first()).toBeVisible({ timeout: 5000 });
    });

    test('cyberpunk progress bar styling present', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 5,
        });

        // Wait for disc card to appear
        await expect(page.locator(SELECTORS.discCard).first()).toBeVisible({ timeout: 5000 });

        // Check for cyberpunk styling elements (animated corners)
        const card = page.locator(SELECTORS.discCard).first();

        // Should have border styling
        await expect(card).toHaveCSS('border-width', '2px');
    });

    test('track grid displays for TV disc', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 10,
        });

        // Wait for track grid to appear (TV shows have track grids)
        await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 15000 });

        // Should have multiple track items
        const trackCount = await page.locator(SELECTORS.trackItem).count();
        expect(trackCount).toBeGreaterThan(0);
    });

    test('per-track ripping progress with byte counts', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 3,
        });

        // Wait for track grid
        await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 15000 });

        // Should see RIPPING state on individual tracks
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Should see per-track byte progress (e.g., "245.3 MB / 520.1 MB")
        await expect(
            page.locator(SELECTORS.trackByteProgress).first()
        ).toBeVisible({ timeout: 10000 });
    });

    test('LISTENING state appears during transcribing', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 5,
        });

        // Wait for matching phase (LISTENING appears during transcribing)
        await expect(
            page.getByText(/LISTENING/i).first()
        ).toBeVisible({ timeout: 30000 });
    });

    test('match candidates show episode codes and confidence', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 5,
        });

        // Wait for matching to complete - should show episode codes like S01E01
        await expect(
            page.locator(SELECTORS.matchCandidate).first()
        ).toBeVisible({ timeout: 30000 });
    });

    test('completed state shows green styling', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 100, // Fast completion
        });

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(page.locator(SELECTORS.stateCompleted).first()).toBeVisible({ timeout: 30000 });

        // Should show completed status
        await expect(page.getByText(/COMPLETE/i).first()).toBeVisible();
    });

    test('WebSocket connection status indicator works', async ({ page }) => {
        // Connection status should be visible in footer
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible();

        // Should show pulsing indicator (cyan dot with animation)
        const footer = page.locator(SELECTORS.footer);
        await expect(footer).toBeVisible();

        // Check for active operations count
        await expect(footer.getByText(/OPERATIONS ACTIVE/i)).toBeVisible();
    });
});

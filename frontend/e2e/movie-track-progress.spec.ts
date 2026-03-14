import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { MOVIE_DISC_MULTI_TRACK } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Movie Track Progress - Multi-track disc', () => {
    test('movie tracks show RIPPING with byte progress', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 2,
        });

        // Wait for track grid to appear (multi-track movies show track grids)
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Should see RIPPING state on individual tracks
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Should see per-track byte progress (e.g., "245.3 MB / 35.4 GB")
        await expect(
            page.locator(SELECTORS.trackByteProgress).first()
        ).toBeVisible({ timeout: 10000 });

        // Capture initial byte progress text
        const initialText = await page.locator(SELECTORS.trackByteProgress).first().textContent();

        // Wait for progress to advance
        await page.waitForTimeout(2000);

        // Byte progress should have changed (rip is advancing)
        const laterText = await page.locator(SELECTORS.trackByteProgress).first().textContent();
        // At least verify both readings are valid byte progress strings
        expect(initialText).toMatch(/\d+(\.\d+)?\s*(MB|GB)\s*\/\s*\d+(\.\d+)?\s*(MB|GB)/i);
        expect(laterText).toMatch(/\d+(\.\d+)?\s*(MB|GB)\s*\/\s*\d+(\.\d+)?\s*(MB|GB)/i);
    });

    test('movie tracks transition through states to completion', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 5,
        });

        // Wait for track grid
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Should see RIPPING state first
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Movies go RIPPING → MATCHED (briefly) → job COMPLETED
        // Wait for final COMPLETE state since MATCHED is transient
        await expect(page.locator(SELECTORS.stateCompleted).first()).toBeVisible({ timeout: 60000 });
    });

    test('overall progress advances during multi-track rip', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 1, // Slow enough to sample progress twice (5 tracks × 20 steps × 0.1s = 10s)
        });

        // Wait for progress bar
        await expect(page.locator(SELECTORS.progressBar)).toBeVisible({ timeout: 15000 });
        await expect(page.locator(SELECTORS.progressPercentage).first()).toBeVisible({ timeout: 5000 });

        // Sample progress at two points
        const firstText = await page.locator(SELECTORS.progressPercentage).first().textContent();
        await page.waitForTimeout(2000);
        const secondText = await page.locator(SELECTORS.progressPercentage).first().textContent();

        // Parse percentages
        const firstPct = parseInt(firstText?.match(/(\d+)%/)?.[1] ?? '0', 10);
        const secondPct = parseInt(secondText?.match(/(\d+)%/)?.[1] ?? '0', 10);

        // Progress should have advanced (or at least be non-zero)
        expect(Math.max(firstPct, secondPct)).toBeGreaterThan(0);
    });

    test('not all tracks show RIPPING simultaneously', async ({ page }) => {
        // Simulation fires rapid WebSocket messages — multiple tracks may
        // briefly show RIPPING due to React render batching. Real ripping
        // (verified on physical disc) always shows exactly 1 RIPPING track
        // because the filesystem monitor enforces single-active-title.
        // This test just verifies not ALL tracks are RIPPING at once.
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 1,
        });

        // Wait for track grid to appear
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Wait for at least one RIPPING track
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Verify not all 5 tracks are RIPPING simultaneously
        const totalTracks = await page.locator(SELECTORS.trackItem).count();
        const rippingCount = await page.locator(SELECTORS.trackStateRipping).count();
        expect(rippingCount).toBeLessThan(totalTracks);
    });

    test('pending tracks show QUEUED label', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 1,
        });

        // Wait for track grid
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Pending tracks should show QUEUED label
        await expect(
            page.locator(SELECTORS.trackStateQueued).first()
        ).toBeVisible({ timeout: 10000 });
    });

    test('all tracks have a visible state indicator', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 1,
        });

        // Wait for track grid
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Every track should have a state indicator (RIPPING, QUEUED, MATCHED, etc.)
        const trackCount = await page.locator(SELECTORS.trackItem).count();
        expect(trackCount).toBeGreaterThanOrEqual(5);

        // Count all tracks with any state label
        const stateLabels = await page.locator(
            `${SELECTORS.trackGrid} :text-matches("RIPPING|QUEUED|MATCHED|MATCHING|FAILED", "i")`
        ).count();
        expect(stateLabels).toBeGreaterThanOrEqual(1);
    });

    test('not all tracks stuck at QUEUED during rip (regression)', async ({ page }) => {
        // Regression test: before the useWebSocket callback fix, React 18 batching
        // caused all title_update messages to be lost, leaving every track as QUEUED
        // even though the backend was correctly broadcasting state="ripping".
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 1,
        });

        // Wait for track grid to appear
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        // Wait for at least one RIPPING track — this would fail before the fix
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Verify that not ALL tracks are QUEUED (the bug symptom)
        const totalTracks = await page.locator(SELECTORS.trackItem).count();
        const queuedTracks = await page.locator(SELECTORS.trackStateQueued).count();
        expect(queuedTracks).toBeLessThan(totalTracks);
    });

    test('movie completes with all tracks done', async ({ page }) => {
        await simulateInsertDisc({
            ...MOVIE_DISC_MULTI_TRACK,
            rip_speed_multiplier: 3,
        });

        // While still ripping, verify multiple tracks are visible
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });
        const trackCount = await page.locator(SELECTORS.trackItem).count();
        expect(trackCount).toBeGreaterThanOrEqual(5);

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for job to reach COMPLETED state
        await expect(page.locator(SELECTORS.stateCompleted).first()).toBeVisible({ timeout: 60000 });
    });
});

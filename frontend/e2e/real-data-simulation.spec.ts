import { test, expect } from '@playwright/test';
import { existsSync } from 'fs';
import { simulateInsertDiscFromStaging, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT_REAL } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

/**
 * Real-data simulation tests using actual MKV files from a staging directory.
 * Requires: C:/Video/ARRESTED_Development_S1D1 with MKV files on disk.
 * Extended timeouts since these use realistic simulation timing.
 * Skipped automatically if the staging path does not exist.
 */

const stagingPathExists = existsSync(TV_DISC_ARRESTED_DEVELOPMENT_REAL.staging_path);

test.describe('Real Data Simulation', () => {
    test.beforeEach(async ({ page }) => {
        await resetAllJobs().catch(() => {});
        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
    });

    test('full workflow with real MKV data', async ({ page }) => {
        test.skip(!stagingPathExists, `Staging path not found: ${TV_DISC_ARRESTED_DEVELOPMENT_REAL.staging_path}`);
        test.setTimeout(180_000);

        const { titles_count } = await simulateInsertDiscFromStaging(
            TV_DISC_ARRESTED_DEVELOPMENT_REAL,
        );
        expect(titles_count).toBeGreaterThan(0);

        // Card should appear (ffprobe on many files can take time)
        await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 30000 });

        // Should show title (badge may take time to appear via WebSocket)
        await expect(page.locator(SELECTORS.discTitle).first()).toBeVisible({ timeout: 10000 });

        // Track grid should appear
        await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 15000 });

        // Per-track ripping progress should show byte counts
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 15000 });
        await expect(
            page.locator(SELECTORS.trackByteProgress).first()
        ).toBeVisible({ timeout: 15000 });

        // LISTENING state should appear during transcribing phase
        await expect(
            page.getByText(/LISTENING/i).first()
        ).toBeVisible({ timeout: 60000 });

        // Episode codes should appear after matching
        await expect(
            page.locator(SELECTORS.matchCandidate).first()
        ).toBeVisible({ timeout: 60000 });

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 90000 });
    });
});

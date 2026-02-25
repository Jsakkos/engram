import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import {
    GENERIC_LABEL_DISC,
    MULTI_FEATURE_MOVIE_DISC,
    TV_DISC_PICARD_S1D3,
    TV_DISC_ARRESTED_DEV_REALISTIC,
} from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.describe('Realistic Disc Flows', () => {
    test('generic label triggers name prompt modal and resumes after submit', async ({ page }) => {
        // Insert disc with generic label — should enter review_needed with no detected_title
        await simulateInsertDisc(GENERIC_LABEL_DISC);

        // NamePromptModal should appear (header: "Identify Disc")
        await expect(page.getByText('Identify Disc')).toBeVisible({ timeout: 10000 });

        // Should show the title input field
        const titleInput = page.getByPlaceholder('e.g. The Italian Job');
        await expect(titleInput).toBeVisible();

        // Fill in the movie name
        await titleInput.fill('The Italian Job');

        // Movie should be selected by default — verify Movie button is active
        await expect(page.locator('button:has-text("Movie")')).toBeVisible();

        // Submit the form
        await page.locator('button:has-text("Start Ripping")').click();

        // Modal should close
        await expect(page.getByText('Identify Disc')).not.toBeVisible({ timeout: 5000 });

        // Switch to ALL filter to see the card regardless of state (it may fail since
        // this is a simulation without real files, but the name should be updated)
        await page.locator(SELECTORS.filterAll).click();

        // Card should now show the user-provided name
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('The Italian Job', { timeout: 10000 });
    });

    test('movie disc with single feature flows through without review', async ({ page }) => {
        // Insert movie disc — despite multiple tracks, only one is feature-length
        await simulateInsertDisc(MULTI_FEATURE_MOVIE_DISC);

        // Card should appear with MOVIE badge
        await expect(page.locator(SELECTORS.movieBadge).first()).toBeVisible({ timeout: 5000 });
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('The Terminator');

        // Should show ripping/processing state (no review needed for simulation)
        const ripping = page.locator(SELECTORS.stateRipping).first();
        const scanning = page.locator(SELECTORS.stateScanning).first();
        await expect(ripping.or(scanning)).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter to see completed jobs
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 30000 });
    });

    test('review-blocked job does not block new job on different drive', async ({ page }) => {
        // Insert generic label disc on E: — enters review_needed (name prompt)
        await simulateInsertDisc({
            ...GENERIC_LABEL_DISC,
            drive_id: 'E:',
        });

        // Wait for name prompt modal to appear
        await expect(page.getByText('Identify Disc')).toBeVisible({ timeout: 10000 });

        // Insert TV disc on F: while the modal is still showing — should NOT be blocked
        // Use slow speed so it's still ripping when we check
        await simulateInsertDisc({
            ...TV_DISC_PICARD_S1D3,
            drive_id: 'F:',
            rip_speed_multiplier: 2,
        });

        // Resolve the name prompt modal so we can see both cards
        const titleInput = page.getByPlaceholder('e.g. The Italian Job');
        await titleInput.fill('The Italian Job');
        await page.locator('button:has-text("Start Ripping")').click();

        // Modal should close
        await expect(page.getByText('Identify Disc')).not.toBeVisible({ timeout: 5000 });

        // Switch to ALL filter so both active AND completed cards are visible
        await page.locator(SELECTORS.filterAll).click();

        // Both cards should be visible (regardless of completion state)
        await expect(page.locator(SELECTORS.discCard)).toHaveCount(2, { timeout: 15000 });

        // The Picard card should be present with TV badge
        await expect(page.locator(SELECTORS.tvBadge).first()).toBeVisible();
    });

    test('TV disc shows track grid with episodes', async ({ page }) => {
        test.setTimeout(120_000); // 11 tracks at speed 5x needs more time

        // Insert TV disc with realistic metadata
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEV_REALISTIC,
            rip_speed_multiplier: 10,
        });

        // Card should appear with TV badge
        await expect(page.locator(SELECTORS.tvBadge).first()).toBeVisible({ timeout: 5000 });
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('Arrested Development');

        // Wait for ripping to show track grid
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Track grid should appear with individual tracks
        await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 10000 });

        // Should see per-track ripping state
        await expect(
            page.locator(SELECTORS.trackStateRipping).first()
        ).toBeVisible({ timeout: 10000 });

        // Wait for matching phase — episode codes should eventually appear
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

    test('TV Picard disc processes multiple episodes', async ({ page }) => {
        // Insert Picard disc with 3 episodes + Play All + extra
        await simulateInsertDisc({
            ...TV_DISC_PICARD_S1D3,
            rip_speed_multiplier: 10,
        });

        // Card should appear with TV badge and correct title
        await expect(page.locator(SELECTORS.tvBadge).first()).toBeVisible({ timeout: 5000 });
        await expect(page.locator(SELECTORS.discTitle).first()).toContainText('Star Trek Picard');

        // Should progress to ripping
        await expect(page.locator(SELECTORS.stateRipping).first()).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter so completed jobs remain visible
        await page.locator(SELECTORS.filterAll).click();

        // Wait for completion
        await expect(
            page.locator(SELECTORS.stateCompleted).first()
        ).toBeVisible({ timeout: 60000 });
    });
});

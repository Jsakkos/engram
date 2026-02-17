import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT, MOVIE_DISC } from './fixtures/disc-scenarios';
import { SELECTORS, getDiscCardByTitle } from './fixtures/selectors';

const SCREENSHOT_DIR = 'e2e-screenshots/workflow';

// Run serially so disc cards don't bleed between tests
test.describe.configure({ mode: 'serial' });

test.describe('Screenshot Workflow - Captures every major UI state', () => {
    test('TV disc - full state progression screenshots', async ({ page }) => {
        test.setTimeout(120_000);

        // Wipe all jobs for a clean slate
        await resetAllJobs();

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter so the card stays visible through completion
        await page.locator(SELECTORS.filterAll).click();

        // 01: Empty state before any disc
        await page.screenshot({ path: `${SCREENSHOT_DIR}/01-initial-state.png`, fullPage: true });

        // Insert TV disc - speed 1 gives ~16s ripping + ~12s matching
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
        });

        const card = page.locator(getDiscCardByTitle('Arrested Development'));

        // 02: Card appears
        await expect(card).toBeVisible({ timeout: 10000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/02-card-appeared.png`, fullPage: true });

        // 03: PROCESSING state (ripping) — the StateIndicator label for ripping/matching
        await expect(card.getByText('PROCESSING')).toBeVisible({ timeout: 15000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/03-processing-state.png`, fullPage: true });

        // 04: Track grid visible
        await expect(card.locator('div.grid.grid-cols-2')).toBeVisible({ timeout: 15000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/04-track-grid-visible.png`, fullPage: true });

        // 05: Per-track RIPPING state on individual tracks
        const hasTrackRipping = await card.getByText('RIPPING').first()
            .isVisible({ timeout: 15000 }).catch(() => false);
        if (hasTrackRipping) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/05-per-track-ripping.png`, fullPage: true });
        }

        // 06: Per-track byte progress (e.g., "245 MB / 1.0 GB")
        const hasByteProgress = await card.locator(SELECTORS.trackByteProgress).first()
            .isVisible({ timeout: 15000 }).catch(() => false);
        if (hasByteProgress) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/06-byte-progress.png`, fullPage: true });
        }

        // 07: LISTENING state (transcribing phase)
        const hasListening = await card.getByText('LISTENING').first()
            .isVisible({ timeout: 60000 }).catch(() => false);
        if (hasListening) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/07-listening-state.png`, fullPage: true });
        }

        // 08: Episode match candidates appear (S01E01 etc.)
        const hasMatchCandidate = await card.locator('text=/S\\d{2}E\\d{2}/i').first()
            .isVisible({ timeout: 30000 }).catch(() => false);
        if (hasMatchCandidate) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/08-match-candidates.png`, fullPage: true });
        }

        // 09: COMPLETE state
        await expect(
            card.getByText('COMPLETE')
        ).toBeVisible({ timeout: 90000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/09-completed.png`, fullPage: true });
    });

    test('Movie disc - ripping through completion screenshots', async ({ page }) => {
        test.setTimeout(60_000);

        // Wipe all jobs for a clean slate
        await resetAllJobs();

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter
        await page.locator(SELECTORS.filterAll).click();

        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 5,
        });

        const card = page.locator(getDiscCardByTitle('Inception'));

        // 10: Card with MOVIE badge
        await expect(card).toBeVisible({ timeout: 10000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/10-movie-card.png`, fullPage: true });

        // 11: PROCESSING state (ripping)
        await expect(card.getByText('PROCESSING')).toBeVisible({ timeout: 15000 });
        await page.waitForTimeout(1000);
        await page.screenshot({ path: `${SCREENSHOT_DIR}/11-movie-processing.png`, fullPage: true });

        // 12: COMPLETE
        await expect(
            card.getByText('COMPLETE')
        ).toBeVisible({ timeout: 30000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/12-movie-completed.png`, fullPage: true });
    });
});

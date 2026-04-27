import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import {
    TV_DISC_ARRESTED_DEVELOPMENT,
    MOVIE_DISC,
    GENERIC_LABEL_DISC,
} from './fixtures/disc-scenarios';
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

        // 03: RIPPING state — the StateIndicator label during rip phase
        await expect(card.getByText('RIPPING').first()).toBeVisible({ timeout: 15000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/03-ripping-state.png`, fullPage: true });

        // 13: TV badge explicitly visible during ripping
        await page.screenshot({ path: `${SCREENSHOT_DIR}/13-tv-badge.png`, fullPage: true });

        // 14: Speed + ETA display (e.g., "6.5x" and "5 min")
        const hasSpeed = await page.locator(SELECTORS.speed).first()
            .waitFor({ state: 'visible', timeout: 10000 }).then(() => true).catch(() => false);
        if (hasSpeed) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/14-speed-eta.png`, fullPage: true });
        }

        // 15: Progress percentage on the main progress bar
        const hasProgress = await page.locator(SELECTORS.progressPercentage).first()
            .waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
        if (hasProgress) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/15-progress-percentage.png`, fullPage: true });
        }

        // 16: Cancel button visible during rip
        const hasCancel = await card.locator(SELECTORS.cancelButton)
            .waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
        if (hasCancel) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/16-cancel-button.png`, fullPage: true });
        }

        // 04: Track grid visible
        await expect(card.locator('div.grid.grid-cols-2.gap-2').first()).toBeVisible({ timeout: 15000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/04-track-grid-visible.png`, fullPage: true });

        // 05: Per-track RIPPING state on individual tracks
        const hasTrackRipping = await card.getByText('RIPPING').first()
            .waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false);
        if (hasTrackRipping) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/05-per-track-ripping.png`, fullPage: true });
        }

        // 06: Per-track byte progress (e.g., "245 MB / 1.0 GB")
        const hasByteProgress = await card.locator(SELECTORS.trackByteProgress).first()
            .waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false);
        if (hasByteProgress) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/06-byte-progress.png`, fullPage: true });
        }

        // 07: MATCHING state — wait for candidate rows (yellow text only appears when matchCandidates exist)
        const hasMatchCandidate = await card.locator('.text-yellow-300').first()
            .waitFor({ state: 'visible', timeout: 60000 }).then(() => true).catch(() => false);
        if (hasMatchCandidate) {
            await page.screenshot({ path: `${SCREENSHOT_DIR}/07-matching-state.png`, fullPage: true });

            // 08: Closer look — wait for more votes to accumulate
            await page.waitForTimeout(2000);
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

        // 11: Processing state — simulation may race through RIPPING/MATCHING
        // straight to COMPLETE on fast CI, so accept any post-identifying state
        await expect(
            card.getByText(/RIPPING|MATCHING|MATCHED|COMPLETE/).first()
        ).toBeVisible({ timeout: 15000 });
        await page.waitForTimeout(1000);
        await page.screenshot({ path: `${SCREENSHOT_DIR}/11-movie-processing.png`, fullPage: true });

        // 12: COMPLETE
        await expect(
            card.getByText('COMPLETE')
        ).toBeVisible({ timeout: 30000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/12-movie-completed.png`, fullPage: true });
    });

    test('Failed/error card state', async ({ page }) => {
        test.setTimeout(60_000);

        await resetAllJobs();

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Switch to ALL filter so the failed card stays visible
        await page.locator(SELECTORS.filterAll).click();

        const { job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
        });

        const card = page.locator(SELECTORS.discCard).first();
        await expect(card).toBeVisible({ timeout: 10000 });

        // Wait for ripping to start so cancel visibly interrupts
        await expect(card.getByText('RIPPING').first()).toBeVisible({ timeout: 15000 });

        // Cancel via API
        await page.request.post(`http://localhost:8001/api/jobs/${job_id}/cancel`);

        // 17: Error/failed card with ERROR badge
        await expect(page.locator(SELECTORS.stateFailed).first()).toBeVisible({ timeout: 10000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/17-error-failed-card.png`, fullPage: true });
    });

    test('Name Prompt Modal - generic label disc', async ({ page }) => {
        test.setTimeout(30_000);

        await resetAllJobs();

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // Generic label disc triggers NamePromptModal (no detected_title + force_review_needed)
        await simulateInsertDisc(GENERIC_LABEL_DISC);

        // 18: Modal open in movie mode (default)
        // toBeVisible resolves as soon as the Framer Motion element enters the DOM (opacity 0).
        // A 250ms pause lets the spring animation advance past the transparent initial frame
        // without exceeding the backend's short review_needed window (~500ms before auto-advance).
        await expect(page.getByText('Identify Disc')).toBeVisible({ timeout: 10000 });
        await page.waitForTimeout(250);
        await page.screenshot({ path: `${SCREENSHOT_DIR}/18-name-prompt-modal.png`, fullPage: true });

        // 19: Switch to TV Show mode
        await page.locator('button:has-text("TV Show")').click();
        await page.waitForTimeout(300); // let the season field animate in
        await page.screenshot({ path: `${SCREENSHOT_DIR}/19-name-prompt-tv-mode.png`, fullPage: true });

        // Close without submitting
        await page.locator('button:has-text("Cancel")').click();
        await expect(page.getByText('Identify Disc')).not.toBeVisible({ timeout: 5000 });
    });

    test('Settings wizard - all 4 steps', async ({ page }) => {
        test.setTimeout(30_000);

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });

        // If onboarding wizard auto-appeared (setup_complete=false), use it.
        // Otherwise open settings manually via the gear button.
        const modalAlreadyVisible = await page.locator('.modal-overlay').isVisible();
        if (!modalAlreadyVisible) {
            await page.locator('button[title="Settings"]').click();
        }

        await expect(page.locator('.modal-overlay')).toBeVisible({ timeout: 5000 });

        // Wait for config to finish loading
        await expect(page.locator('.wizard-loading')).not.toBeVisible({ timeout: 10000 });

        // Determine navigation mode: onboarding uses Next→, settings uses clickable tabs
        const isOnboardingMode = await page.locator('.btn-primary:has-text("Next")').isVisible();

        // 20: Step 1 — Library Paths
        await expect(page.locator('.step-title').first()).toContainText('Library Paths', { timeout: 5000 });
        await page.screenshot({ path: `${SCREENSHOT_DIR}/20-settings-step1-paths.png`, fullPage: true });

        // Advance to step 2
        if (isOnboardingMode) {
            await page.locator('.btn-primary').click();
        } else {
            await page.locator('[aria-label*="Step 2:"]').click();
        }
        await expect(page.locator('.step-title').first()).toContainText('Tools', { timeout: 5000 });

        // 21: Step 2 — Tools & License
        await page.screenshot({ path: `${SCREENSHOT_DIR}/21-settings-step2-tools.png`, fullPage: true });

        // Advance to step 3
        if (isOnboardingMode) {
            await page.locator('.btn-primary').click();
        } else {
            await page.locator('[aria-label*="Step 3:"]').click();
        }
        await expect(page.locator('.step-title').first()).toContainText('TMDB', { timeout: 5000 });

        // 22: Step 3 — TMDB Read Access Token
        await page.screenshot({ path: `${SCREENSHOT_DIR}/22-settings-step3-tmdb.png`, fullPage: true });

        // Advance to step 4
        if (isOnboardingMode) {
            await page.locator('.btn-primary').click();
        } else {
            await page.locator('[aria-label*="Step 4:"]').click();
        }
        await expect(page.locator('.step-title').first()).toContainText('Preferences', { timeout: 5000 });

        // 23: Step 4 — Preferences
        await page.screenshot({ path: `${SCREENSHOT_DIR}/23-settings-step4-prefs.png`, fullPage: true });

        // Close the wizard
        await page.locator('.modal-close').click();
        await expect(page.locator('.modal-overlay')).not.toBeVisible({ timeout: 5000 });
    });

    test('History page with completed jobs and detail panel', async ({ page }) => {
        test.setTimeout(90_000);

        await resetAllJobs();

        await page.goto('/');
        await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
        await page.locator(SELECTORS.filterAll).click();

        // Run a fast movie so history has at least one completed entry
        await simulateInsertDisc({
            ...MOVIE_DISC,
            rip_speed_multiplier: 50,
        });
        await expect(page.locator(SELECTORS.stateCompleted).first()).toBeVisible({ timeout: 30000 });

        // Navigate to history page
        await page.goto('/history');
        await page.waitForLoadState('networkidle');

        // Wait for the history table to load
        await expect(page.locator('table')).toBeVisible({ timeout: 10000 });
        await expect(page.locator('tbody tr').first()).toBeVisible({ timeout: 10000 });

        // 24: History page with at least one completed job row
        await page.screenshot({ path: `${SCREENSHOT_DIR}/24-history-page.png`, fullPage: true });

        // Click the first row to open the job detail slide-out panel
        await page.locator('tbody tr').first().click();

        // Wait for detail panel to load (it fetches /api/jobs/:id/detail)
        await page.waitForTimeout(2000);

        // 25: History detail panel open
        await page.screenshot({ path: `${SCREENSHOT_DIR}/25-history-detail-panel.png`, fullPage: true });
    });

    test('Review queue page', async ({ page }) => {
        test.setTimeout(30_000);

        await resetAllJobs();

        // Insert a disc and navigate immediately to its review page.
        // ReviewQueue renders job + title data regardless of current job state,
        // giving a representative screenshot of the review interface.
        const { job_id } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 50,
            simulate_ripping: true,
        });

        await page.goto(`/review/${job_id}`);
        await page.waitForLoadState('networkidle');

        // Wait for either job content or the back-navigation button
        await page.waitForTimeout(2000);

        // 26: Review queue page
        await page.screenshot({ path: `${SCREENSHOT_DIR}/26-review-page.png`, fullPage: true });
    });
});

import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

/**
 * Visual regression baselines. SKIPPED until baselines are seeded *on
 * the CI runner platform* (Linux). Generating baselines on a developer
 * Windows box produces `*-chromium-win32.png` files that CI ignores;
 * CI looks for `*-chromium-linux.png`.
 *
 * To enable from a Linux machine (or via the CI runner):
 *   1. Start backend (DEBUG=true uv run uvicorn app.main:app --port 8000)
 *   2. Start frontend (npm run dev)
 *   3. npx playwright test visual-regression --update-snapshots
 *   4. Commit the generated PNGs in visual-regression.spec.ts-snapshots/
 *   5. Change `test.describe.skip` below to `test.describe`
 *
 * From a Windows dev box: push a temporary commit with the describe
 * enabled, let CI generate the Linux PNGs in the playwright-report
 * artifact, download and commit them, then enable the describe.
 */
test.describe.skip('Visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
    // Disable animations for stable snapshots
    await page.addStyleTag({
      content: `*, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important; transition-duration: 0s !important; transition-delay: 0s !important; }`,
    });
  });

  test('dashboard idle state', async ({ page }) => {
    await expect(page).toHaveScreenshot('dashboard-idle.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  test('dashboard with one TV disc inserted', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 100, // pause progress for stable snapshot
    });
    await page.waitForSelector(SELECTORS.discCard, { timeout: 5000 });
    // Allow card animation to settle
    await page.waitForTimeout(500);
    await expect(page).toHaveScreenshot('dashboard-tv-disc.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });

  test('history page empty state', async ({ page }) => {
    await page.goto('/history');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveScreenshot('history-empty.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });
});

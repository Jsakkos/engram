import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

/**
 * Visual regression baselines.
 *
 * Baseline PNGs live in `visual-regression.spec.ts-snapshots/` and are
 * chromium-on-Linux only — font rendering varies across platforms, and
 * CI runs on Linux. Do not commit Windows or macOS baselines.
 *
 * To update after intentional UI changes (run from a Linux machine or
 * the CI runner):
 *   1. npx playwright test visual-regression --update-snapshots
 *   2. git add e2e/visual-regression.spec.ts-snapshots/
 *
 * Playwright auto-spawns its own backend (port 8001) + frontend (5174)
 * via webServer in playwright.config.ts — no manual server start needed.
 */
test.describe('Visual regression', () => {
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

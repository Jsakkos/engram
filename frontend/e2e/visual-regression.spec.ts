import { test, expect } from '@playwright/test';
import { resetAllJobs } from './fixtures/api-helpers';
import { SELECTORS } from './fixtures/selectors';

/**
 * Visual regression baselines.
 *
 * Baseline PNGs live in `visual-regression.spec.ts-snapshots/` and are
 * chromium-on-Linux only — font rendering varies across platforms, and
 * CI runs on Linux. Do not commit Windows or macOS baselines.
 *
 * Scope: static pages only. Pages with WebSocket-driven content
 * (e.g. an active disc card during ripping) are unstable for snapshot
 * comparison even with animations disabled — re-renders fight the
 * "two consecutive stable screenshots" requirement. Test those flows
 * via the regular E2E suite instead.
 *
 * Tolerances are loose (10% pixel ratio) because subpixel font rendering
 * differs between Linux runners (a local Proxmox VM vs GitHub's
 * ubuntu-latest, for instance). The goal is to catch *gross* regressions
 * (broken theme, missing elements, wrong page rendered), not pixel-
 * perfect diffs. For pixel-perfect comparison use a dedicated service
 * (Percy, Chromatic, Argos).
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
      maxDiffPixelRatio: 0.1,
    });
  });

  test('history page empty state', async ({ page }) => {
    await page.goto('/history');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveScreenshot('history-empty.png', {
      maxDiffPixelRatio: 0.1,
    });
  });
});

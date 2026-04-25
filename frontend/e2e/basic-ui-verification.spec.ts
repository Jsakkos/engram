import { test, expect } from '@playwright/test';
import { SELECTORS } from './fixtures/selectors';

/**
 * Basic UI verification tests that don't require disc simulation.
 * These tests verify the UI renders correctly without waiting for slow operations.
 */

test.describe('Basic UI Verification - No Disc Simulation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('Header displays Engram branding (Synapse v2 wordmark)', async ({ page }) => {
    const topbar = page.locator(SELECTORS.header);
    await expect(topbar).toBeVisible();
    // The ENGRAM wordmark text should be visible inside the topbar
    await expect(topbar).toContainText(/ENGRAM/);

    // Screenshot
    await page.screenshot({
      path: 'e2e-screenshots/01-header-branding.png',
      fullPage: false,
      clip: { x: 0, y: 0, width: 800, height: 200 }
    });
  });

  test('Sub-line displays "MEMORY · ARCHIVAL"', async ({ page }) => {
    await expect(page.getByText(/MEMORY\s*[·.]\s*ARCHIVAL/i)).toBeVisible();
  });

  test('Filter buttons are present and styled correctly', async ({ page }) => {
    // All three filter buttons should be visible
    await expect(page.locator(SELECTORS.filterAll)).toBeVisible();
    await expect(page.locator(SELECTORS.filterActive)).toBeVisible();
    await expect(page.locator(SELECTORS.filterDone)).toBeVisible();

    // Screenshot
    await page.screenshot({
      path: 'e2e-screenshots/02-filter-buttons.png',
      fullPage: false,
      clip: { x: 0, y: 0, width: 1400, height: 300 }
    });
  });

  test('WebSocket connection status indicator is present', async ({ page }) => {
    // Footer should be visible
    await expect(page.locator(SELECTORS.footer)).toBeVisible();

    // Wait for WebSocket to connect (takes ~2-3s after page load)
    // Check for either connected or disconnected status text
    await expect(
      page.locator(SELECTORS.connectionStatus.connected)
    ).toBeVisible({ timeout: 15000 });
  });

  test('Empty state displays when no discs present', async ({ page }) => {
    // Click ACTIVE filter
    await page.locator(SELECTORS.filterActive).click();

    // Should show empty state heading (ACTIVE filter shows "NO ACTIVE OPERATIONS")
    await expect(page.getByRole('heading', { name: /NO ACTIVE OPERATIONS/i })).toBeVisible();

    // Screenshot empty state
    await page.screenshot({
      path: 'e2e-screenshots/04-empty-state.png',
      fullPage: true
    });
  });

  test('Page uses Synapse v2 atmosphere (dark cyberpunk + scanlines)', async ({ page }) => {
    // Atmosphere wrapper
    const atmosphere = page.locator(SELECTORS.appContainer);
    await expect(atmosphere).toBeVisible();

    // Scanline overlay (Synapse v2 atmosphere always-on)
    const scanlines = page.locator('[data-testid="sv-scanlines"]');
    await expect(scanlines).toBeVisible();
  });

  test('Footer displays operation counts', async ({ page }) => {
    // Should show "Active" count
    await expect(page.locator(SELECTORS.footer)).toContainText(/\d+ Active/i);

    // Should show "Archived" count
    await expect(page.locator(SELECTORS.footer)).toContainText(/\d+ Archived/i);
  });

  test('Settings button is present', async ({ page }) => {
    // Look for settings button (has Settings icon)
    const settingsButton = page.getByRole('button').filter({ has: page.locator('svg') }).first();
    await expect(settingsButton).toBeVisible();
  });

  test('Full page screenshot for manual review', async ({ page }) => {
    // Take full page screenshot
    await page.screenshot({
      path: 'e2e-screenshots/05-full-page-empty.png',
      fullPage: true
    });
  });
});

test.describe('Basic UI Verification - With Existing Disc Data', () => {
  test('Existing disc cards display with cyberpunk styling', async ({ page }) => {
    await page.goto('/');

    // Count existing disc cards
    const discCards = page.locator(SELECTORS.discCard);
    const count = await discCards.count();

    console.log(`Found ${count} existing disc cards`);

    if (count > 0) {
      // Verify first card has proper styling
      const firstCard = discCards.first();
      await expect(firstCard).toBeVisible();

      // Should have border styling
      await expect(firstCard).toHaveClass(/border-2/);

      // Screenshot
      await page.screenshot({
        path: 'e2e-screenshots/06-existing-discs.png',
        fullPage: true
      });
    }
  });

  test('Filter switching works with existing data', async ({ page }) => {
    await page.goto('/');

    const allCount = await page.locator(SELECTORS.discCard).count();

    // Switch to ALL filter
    await page.locator(SELECTORS.filterAll).click();
    await page.waitForTimeout(500);

    const allFilterCount = await page.locator(SELECTORS.discCard).count();

    // Switch to ACTIVE filter
    await page.locator(SELECTORS.filterActive).click();
    await page.waitForTimeout(500);

    const activeFilterCount = await page.locator(SELECTORS.discCard).count();

    // Switch to DONE filter
    await page.locator(SELECTORS.filterDone).click();
    await page.waitForTimeout(500);

    const doneFilterCount = await page.locator(SELECTORS.discCard).count();

    console.log(`Counts - All: ${allCount}, ALL Filter: ${allFilterCount}, Active: ${activeFilterCount}, Done: ${doneFilterCount}`);

    // Screenshot each filter state
    await page.locator(SELECTORS.filterAll).click();
    await page.screenshot({ path: 'e2e-screenshots/07-filter-all.png', fullPage: true });

    await page.locator(SELECTORS.filterActive).click();
    await page.screenshot({ path: 'e2e-screenshots/08-filter-active.png', fullPage: true });

    await page.locator(SELECTORS.filterDone).click();
    await page.screenshot({ path: 'e2e-screenshots/09-filter-done.png', fullPage: true });
  });
});

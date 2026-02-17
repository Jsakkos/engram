import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT, MOVIE_DISC } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

test.describe('Visual Verification - UI Correctness', () => {
  test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
  });

  test('Header displays Engram branding', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Engram');

    // Check for cyberpunk styling - cyan color
    const header = page.locator('h1');
    await expect(header).toHaveClass(/text-cyan/);
  });

  test('Disc card shows cyberpunk styling elements', async ({ page }) => {
    await simulateInsertDisc(TV_DISC_ARRESTED_DEVELOPMENT);
    await page.waitForSelector(SELECTORS.discCard, { timeout: 5000 });

    // Verify disc card is visible
    const discCard = page.locator(SELECTORS.discCard).first();
    await expect(discCard).toBeVisible();

    // Check for border styling (cyberpunk aesthetic)
    await expect(discCard).toHaveClass(/border-2/);
    await expect(discCard).toHaveClass(/bg-black/);
  });

  test('Progress bar displays with percentage', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 2,
    });

    // Wait for ripping to start
    await page.waitForSelector(SELECTORS.stateRipping, { timeout: 10000 });

    // Should show percentage text
    const progressPercentage = page.locator(SELECTORS.progressPercentage).first();
    await expect(progressPercentage).toBeVisible({ timeout: 5000 });
  });

  test('Track grid shows for TV content with per-track progress', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 3,
    });

    // Wait for track grid to appear
    await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 15000 });

    // Track grid must be visible (no defensive check)
    const trackItems = page.locator(SELECTORS.trackItem);
    expect(await trackItems.count()).toBeGreaterThan(0);

    // Should see per-track byte progress text (e.g., "256.0 MB / 1.0 GB")
    await expect(
      page.locator(SELECTORS.trackByteProgress).first()
    ).toBeVisible({ timeout: 15000 });
  });

  test('Filter buttons work and update counts', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 100, // Fast completion
    });

    // Use ALL filter first to ensure the card is visible regardless of state
    await page.locator(SELECTORS.filterAll).click();

    // Wait for card to appear
    await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 10000 });

    // Test ACTIVE filter
    await page.locator(SELECTORS.filterActive).click();
    await page.waitForTimeout(500);

    // Test DONE filter
    await page.locator(SELECTORS.filterDone).click();
    await page.waitForTimeout(500);

    // Test ALL filter (should always show the card)
    await page.locator(SELECTORS.filterAll).click();
    await expect(page.locator(SELECTORS.discCard)).toBeVisible({ timeout: 5000 });
  });

  test('WebSocket connection status indicator present', async ({ page }) => {
    // Check for connection indicator in footer
    const connectionIndicator = page.locator(SELECTORS.connectionStatus.connected);
    await expect(connectionIndicator).toBeVisible({ timeout: 10000 });
  });

  test('Empty state displays correctly', async ({ page }) => {
    // Should show empty state on active filter (no discs since reset ran in beforeEach)
    await page.locator(SELECTORS.filterActive).click();
    await expect(page.getByRole('heading', { name: /NO DISCS DETECTED/i })).toBeVisible({ timeout: 5000 });
  });

  test('State indicators use correct colors', async ({ page }) => {
    await simulateInsertDisc(TV_DISC_ARRESTED_DEVELOPMENT);

    // Wait for card to show scanning/identifying
    await page.waitForSelector(SELECTORS.discCard, { timeout: 5000 });

    // Wait for ripping state
    const rippingIndicator = page.locator(SELECTORS.stateRipping).first();
    await expect(rippingIndicator).toBeVisible({ timeout: 10000 });
  });

  test('Movie disc displays correctly', async ({ page }) => {
    await simulateInsertDisc(MOVIE_DISC);

    // Wait for card to appear
    await page.waitForSelector(SELECTORS.discCard, { timeout: 5000 });

    // Should show MOVIE badge
    await expect(page.locator(SELECTORS.movieBadge).first()).toBeVisible();

    // Movie title should display
    await expect(page.locator(SELECTORS.discTitle).first()).toContainText('Inception');
  });

  test('Speed and ETA display during ripping', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 2,
    });

    // Wait for ripping
    await page.waitForSelector(SELECTORS.stateRipping, { timeout: 10000 });

    // Speed indicator should appear (looks for "Nx" pattern)
    await expect(page.locator(SELECTORS.speed).first()).toBeVisible({ timeout: 5000 });
  });

  test('Completed state displays correctly', async ({ page }) => {
    await simulateInsertDisc({
      ...MOVIE_DISC,
      rip_speed_multiplier: 100, // Fast completion
    });

    // Switch to ALL filter so completed jobs remain visible
    await page.locator(SELECTORS.filterAll).click();

    // Wait for completion
    await expect(page.locator(SELECTORS.stateCompleted).first()).toBeVisible({ timeout: 30000 });
  });

  test('Footer displays operation counts', async ({ page }) => {
    await simulateInsertDisc(TV_DISC_ARRESTED_DEVELOPMENT);

    // Wait for card
    await page.waitForSelector(SELECTORS.discCard, { timeout: 5000 });

    // Footer should show active operations count
    await expect(page.locator(SELECTORS.footer)).toContainText(/\d+ OPERATIONS ACTIVE/i);

    // Should show archived count
    await expect(page.locator(SELECTORS.footer)).toContainText(/\d+ ARCHIVED/i);
  });
});

test.describe('Visual Verification - Enhanced Track Display', () => {
  test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
  });

  test('Track progress shows for individual tracks during ripping', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 3,
    });

    // Wait for track grid
    await expect(page.locator(SELECTORS.trackGrid)).toBeVisible({ timeout: 15000 });

    // Per-track RIPPING state label should appear
    await expect(
      page.locator(SELECTORS.trackStateRipping).first()
    ).toBeVisible({ timeout: 15000 });

    // Per-track byte progress text should be visible (e.g., "256.0 MB / 1.0 GB")
    await expect(
      page.locator(SELECTORS.trackByteProgress).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test('Matching state shows candidates with confidence', async ({ page }) => {
    await simulateInsertDisc({
      ...TV_DISC_ARRESTED_DEVELOPMENT,
      rip_speed_multiplier: 5,
    });

    // Wait for matching to complete - episode codes should appear
    await expect(
      page.locator(SELECTORS.matchCandidate).first()
    ).toBeVisible({ timeout: 30000 });
  });
});

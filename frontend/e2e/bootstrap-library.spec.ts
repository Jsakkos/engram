import { test, expect, type Page } from '@playwright/test';

/**
 * E2E for the Bootstrap-Library wizard (BootstrapLibraryFlow).
 *
 * The wizard seeds the fingerprint network from an existing TV library. Its two
 * backend endpoints are localhost-gated and require a real on-disk library to
 * scan, which isn't available in CI — so we route-mock both:
 *
 *   POST /api/fingerprint/bootstrap/scan   → a deterministic 3-show scan result
 *   POST /api/fingerprint/bootstrap/accept → echoes back queued counts
 *
 * Everything else (config load, WebSocket) hits the real E2E backend. The test
 * drives the full scan → review → accept flow and asserts the single accept
 * batch the frontend submits, then captures screenshots at each step.
 */

const SCREENSHOT_DIR = 'e2e-screenshots/bootstrap-library';

// Deterministic scan payload: two resolved shows (accepted by default) + one
// unresolved show (needs a TMDB ID) + one unparseable file.
//   resolved episodes:   2 (Breaking Bad) + 3 (Arrested Development) = 5
//   unresolved episodes: 2 (Mystery Show) — counted only after a TMDB ID is entered
const SCAN_RESULT = {
    shows: [
        {
            folder_name: 'Breaking Bad',
            tmdb_id: 1396,
            tmdb_name: 'Breaking Bad',
            tmdb_year: 2008,
            resolved: true,
            episode_count: 2,
            episodes: [
                { file: 'Breaking Bad/Season 01/Breaking Bad - S01E01.mkv', season: 1, episode: 1 },
                { file: 'Breaking Bad/Season 01/Breaking Bad - S01E02.mkv', season: 1, episode: 2 },
            ],
        },
        {
            folder_name: 'Arrested Development',
            tmdb_id: 4589,
            tmdb_name: 'Arrested Development',
            tmdb_year: 2003,
            resolved: true,
            episode_count: 3,
            episodes: [
                { file: 'Arrested Development/Season 01/Arrested Development - S01E01.mkv', season: 1, episode: 1 },
                { file: 'Arrested Development/Season 01/Arrested Development - S01E02.mkv', season: 1, episode: 2 },
                { file: 'Arrested Development/Season 01/Arrested Development - S01E03.mkv', season: 1, episode: 3 },
            ],
        },
        {
            folder_name: 'Mystery Show',
            tmdb_id: null,
            tmdb_name: null,
            tmdb_year: null,
            resolved: false,
            episode_count: 2,
            episodes: [
                { file: 'Mystery Show/Season 01/Mystery Show - S01E01.mkv', season: 1, episode: 1 },
                { file: 'Mystery Show/Season 01/Mystery Show - S01E02.mkv', season: 1, episode: 2 },
            ],
        },
    ],
    unparseable: [{ file: 'home_videos/birthday_2019.mkv' }],
    summary: { total_files: 8, parsed: 7, shows: 3, unparseable: 1 },
};

const MYSTERY_TMDB_ID = 603; // entered by the user for the unresolved show

interface AcceptBody {
    items: Array<{ file: string; tmdb_id: number; season: number; episode: number }>;
}

/** Open the Settings modal and land on the Preferences tab (step 4). */
async function openSettingsPreferences(page: Page) {
    await page.locator('[data-testid="sv-settings-btn"]').click();
    await expect(page.getByText('Preferences')).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: /Step 4: Preferences/i }).click();
    await expect(page.getByText('Configure additional options for your workflow')).toBeVisible({ timeout: 3000 });
}

test.describe('Bootstrap library wizard', () => {
    test('scan → review → accept queues the selected episodes', async ({ page }) => {
        // Project-convention capture size.
        await page.setViewportSize({ width: 2560, height: 1440 });

        const acceptBodies: AcceptBody[] = [];

        // Mock only the two bootstrap endpoints; everything else hits the real backend.
        await page.route('**/api/fingerprint/bootstrap/scan', async (route) => {
            expect(route.request().method()).toBe('POST');
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(SCAN_RESULT),
            });
        });
        await page.route('**/api/fingerprint/bootstrap/accept', async (route) => {
            const body = route.request().postDataJSON() as AcceptBody;
            acceptBodies.push(body);
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ queued: body.items.length, failed: 0 }),
            });
        });

        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });

        // ── Open the wizard from Preferences ─────────────────────────────────
        await openSettingsPreferences(page);

        // Contributions are opt-in (checked by default); the bootstrap button is
        // gated behind it. Ensure it's on, then open the wizard.
        const contributeToggle = page.getByRole('checkbox', { name: /Contribute audio fingerprints/i });
        await expect(contributeToggle).toBeVisible();
        if (!(await contributeToggle.isChecked())) {
            await contributeToggle.check();
        }

        await page.getByRole('button', { name: /Contribute from existing library/i }).click();

        const wizard = page.getByRole('dialog', { name: /Bootstrap library fingerprints/i });
        await expect(wizard).toBeVisible();
        await expect(wizard.getByText('Library Directory')).toBeVisible();
        await page.screenshot({ path: `${SCREENSHOT_DIR}/01-directory.png`, fullPage: true, animations: 'disabled' });

        // ── Scan ─────────────────────────────────────────────────────────────
        await page.getByLabel(/TV Library Path/i).fill('D:\\TV Shows');
        await page.getByRole('button', { name: 'Scan Directory' }).click();

        // ── Review ───────────────────────────────────────────────────────────
        // Resolved shows render and are accepted by default.
        await expect(wizard.getByText('Breaking Bad', { exact: true })).toBeVisible({ timeout: 5000 });
        await expect(wizard.getByText('Arrested Development', { exact: true })).toBeVisible();
        await expect(wizard.getByText(/2 resolved shows/i)).toBeVisible();
        // The unresolved show needs a TMDB ID.
        await expect(wizard.getByText('Mystery Show', { exact: true })).toBeVisible();
        await expect(wizard.getByText(/need TMDB IDs/i)).toBeVisible();
        // Summary stats.
        await expect(wizard.getByText('Files found')).toBeVisible();

        // Footer reflects the two auto-accepted resolved shows (5 episodes).
        await expect(wizard.getByText(/2 shows accepted/i)).toBeVisible();
        await expect(wizard.getByText(/5 episodes to queue/i)).toBeVisible();
        await page.screenshot({ path: `${SCREENSHOT_DIR}/02-review.png`, fullPage: true, animations: 'disabled' });

        // Entering a valid TMDB ID for the unresolved show auto-accepts it,
        // adding its 2 episodes → 3 shows / 7 episodes.
        await page.getByRole('textbox', { name: /TMDB ID for Mystery Show/i }).fill(String(MYSTERY_TMDB_ID));
        await expect(wizard.getByText(/3 shows accepted/i)).toBeVisible();
        await expect(wizard.getByText(/7 episodes to queue/i)).toBeVisible();

        // ── Accept / Queue ───────────────────────────────────────────────────
        await page.getByRole('button', { name: /Confirm/i }).click();

        await expect(wizard.getByText('Episodes queued')).toBeVisible({ timeout: 5000 });
        await expect(wizard.getByText('7', { exact: true })).toBeVisible();
        // Scope to the wizard — the dashboard's "DONE [0]" filter also matches "Done".
        await expect(wizard.getByRole('button', { name: 'Done' })).toBeVisible();
        await page.screenshot({ path: `${SCREENSHOT_DIR}/03-fingerprint-complete.png`, fullPage: true, animations: 'disabled' });

        // ── Assert the single accept batch the frontend submitted ────────────
        expect(acceptBodies).toHaveLength(1);
        const items = acceptBodies[0].items;
        expect(items).toHaveLength(7);
        // Resolved show items carry the scan's TMDB ID...
        expect(items).toContainEqual({
            file: 'Breaking Bad/Season 01/Breaking Bad - S01E01.mkv',
            tmdb_id: 1396,
            season: 1,
            episode: 1,
        });
        // ...and the unresolved show carries the user-entered ID.
        expect(items).toContainEqual({
            file: 'Mystery Show/Season 01/Mystery Show - S01E01.mkv',
            tmdb_id: MYSTERY_TMDB_ID,
            season: 1,
            episode: 1,
        });

        // Done closes the wizard.
        await wizard.getByRole('button', { name: 'Done' }).click();
        await expect(wizard).not.toBeVisible();
    });
});

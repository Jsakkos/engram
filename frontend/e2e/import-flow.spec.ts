import { test, expect } from '@playwright/test';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * E2E for manual media import: the top-bar IMPORT button opens a two-pane modal
 * that browses the server filesystem, previews a Show / Season / Disc tree
 * (rolling the Disc folder up into its season), and starts one import job per
 * season.
 *
 * The seeded tree lives in the OS temp dir on the same host the E2E backend runs
 * on, so the backend's browse/preview/start endpoints can read it directly. The
 * import endpoints do not require DEBUG, but the E2E backend runs with DEBUG=true
 * so reset-all-jobs is available for test isolation.
 */

function seedShowTree(): string {
    const root = mkdtempSync(join(tmpdir(), 'engram-import-'));
    const disc = join(root, 'Demo Show', 'Season 1', 'Disc 1');
    mkdirSync(disc, { recursive: true });
    writeFileSync(join(disc, 't00.mkv'), Buffer.alloc(1024));
    writeFileSync(join(disc, 't01.mkv'), Buffer.alloc(1024));
    return root;
}

test.describe('Manual media import', () => {
    test.beforeEach(async ({ request }) => {
        await request.delete('/api/simulate/reset-all-jobs');
    });

    test('browse, preview, and start an import', async ({ page, request }) => {
        const root = seedShowTree();

        // Point the import default at the seeded root so the modal opens there.
        await request.put('/api/config', { data: { import_watch_path: root } });

        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });

        // Open the import modal from the top bar.
        await page.getByTestId('sv-import-btn').click();
        await expect(page.getByText('IMPORT MEDIA')).toBeVisible();

        // The modal opens browsing the seeded root; click the show folder. This
        // navigates into it AND previews it (the right pane shows the per-season
        // breakdown, with the nested Disc folder rolled into Season 1).
        await page.getByText('Demo Show', { exact: true }).first().click();
        await expect(page.getByText(/SEASON 1/)).toBeVisible({ timeout: 5000 });

        // Start the import; the modal closes.
        await page.getByTestId('import-start-btn').click();
        await expect(page.getByTestId('import-start-btn')).toBeHidden({ timeout: 5000 });

        // One import job for the show should now exist on the backend
        // (filter-independent: assert on the API rather than a dashboard card,
        // since a fake-MKV job may reach a terminal state quickly).
        await expect
            .poll(
                async () => {
                    const res = await request.get('/api/jobs');
                    const jobs = (await res.json()) as Array<{
                        drive_id: string;
                        detected_title: string | null;
                    }>;
                    return jobs.some(
                        (j) =>
                            j.drive_id === 'import' &&
                            (j.detected_title || '').includes('Demo Show'),
                    );
                },
                { timeout: 15000 },
            )
            .toBe(true);
    });
});

import { test, expect } from '@playwright/test';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resetAllJobs } from './fixtures/api-helpers';

/**
 * E2E for the optional disc-backup phase.
 *
 * With backup_before_rip on, a disc is copied whole to a separate folder after
 * identification and extraction reads from that copy, so the disc leaves the
 * drive at the end of BACKING_UP rather than at the end of RIPPING. The
 * simulated backup parks in the phase instead of auto-advancing, which is what
 * lets a test observe it at all: a real copy is tens of gigabytes.
 *
 * The second test covers the other half of the feature: an existing backup on
 * disk is importable. The seeded tree only needs the BDMV marker directory,
 * because detection keys on structure rather than on readable media.
 */

/** Seeded trees are torn down in afterAll so runs do not litter the OS temp dir. */
const seededRoots: string[] = [];

/** A minimal MakeMKV-shaped Blu-ray backup: the marker plus one tiny stream. */
function seedDiscBackup(): string {
    const root = mkdtempSync(join(tmpdir(), 'engram-backup-'));
    seededRoots.push(root);
    const stream = join(root, 'Inception (2010)', 'BDMV', 'STREAM');
    mkdirSync(stream, { recursive: true });
    writeFileSync(join(stream, '00001.m2ts'), Buffer.alloc(1024));
    return root;
}

test.describe('Disc backup before rip', () => {
    // backup_before_rip and import_watch_path live in the shared app_config
    // table and outlive the process, so both are restored: otherwise a later
    // spec inherits a backup setting it never asked for, or a watch path
    // pointing at a directory afterAll just deleted.
    let originalBackupBeforeRip = false;
    let originalBackupPath = '';
    let originalWatchPath = '';

    test.beforeAll(async ({ request }) => {
        const cfg = await (await request.get('/api/config')).json();
        originalBackupBeforeRip = cfg.backup_before_rip ?? false;
        originalBackupPath = cfg.backup_path ?? '';
        originalWatchPath = cfg.import_watch_path ?? '';
    });

    test.afterAll(async ({ request }) => {
        await request.put('/api/config', {
            data: {
                backup_before_rip: originalBackupBeforeRip,
                backup_path: originalBackupPath,
                import_watch_path: originalWatchPath,
            },
        });
        for (const root of seededRoots) rmSync(root, { recursive: true, force: true });
        seededRoots.length = 0;
    });

    test.beforeEach(async () => {
        await resetAllJobs().catch(() => {});
    });

    test('a simulated disc shows BACKING UP, then RIPPING', async ({ page, request }) => {
        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 15000 });

        const insert = await request.post('/api/simulate/insert-disc', {
            data: {
                volume_label: 'INCEPTION_2010',
                content_type: 'movie',
                simulate_backup: true,
                simulate_ripping: false,
            },
        });
        expect(insert.ok()).toBe(true);
        const jobId = (await insert.json()).job_id;

        // The phase is visible on the card, both as the state badge and as the
        // copy-specific panel that replaced the orphaned ISO block.
        await expect(page.locator('text=INCEPTION_2010').first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByText('BACKING UP').first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByText(/BACKING UP DISC/i).first()).toBeVisible({ timeout: 10000 });

        // Advancing completes the copy and hands off to extraction. This is the
        // transition the whole feature turns on, so assert the UI follows it
        // rather than only the API.
        await request.post(`/api/simulate/advance-job/${jobId}`);
        await expect(page.getByText('RIPPING').first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByText(/BACKING UP DISC/i)).toHaveCount(0);
    });

    test('an existing disc backup is importable', async ({ page, request }) => {
        const root = seedDiscBackup();
        await request.put('/api/config', { data: { import_watch_path: root } });

        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 15000 });

        await page.getByTestId('sv-import-btn').click();
        await expect(page.getByText('IMPORT MEDIA')).toBeVisible();

        // The backup folder is labelled as a disc backup rather than counted as
        // a folder of media, which is the distinction that stops a user
        // expecting a quick file move.
        await expect(page.getByText(/disc backup/i).first()).toBeVisible({ timeout: 10000 });

        await page.getByText('Inception (2010)', { exact: true }).first().click();
        await expect(page.getByText(/scanned and extracted/i)).toBeVisible({ timeout: 10000 });

        await page.getByTestId('import-start-btn').click();
        await expect(page.getByTestId('import-start-btn')).toBeHidden({ timeout: 10000 });

        // Assert on the API rather than a dashboard card: a fake backup has no
        // readable media, so the job may reach a terminal state quickly and
        // drop out of the default dashboard filter.
        await expect
            .poll(
                async () => {
                    const jobs = (await (await request.get('/api/jobs')).json()) as Array<{
                        drive_id: string;
                        source_spec: string | null;
                    }>;
                    return jobs.some(
                        (j) =>
                            j.drive_id === 'import' &&
                            (j.source_spec || '').startsWith('file:'),
                    );
                },
                { timeout: 20000 },
            )
            .toBe(true);
    });
});

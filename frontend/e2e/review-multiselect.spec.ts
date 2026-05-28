import { test, expect } from '@playwright/test';

/**
 * Review-tab multiselect bulk actions.
 *
 * The review page only needs four read endpoints to render, so we route-mock
 * them to put a TV disc with several unclassified extras into review
 * deterministically — simulation can't reliably produce that state. We then
 * drive the checkbox multiselect + "Mark as Extra" bulk action and assert the
 * single batch request the frontend sends on Save.
 */

const JOB_ID = 1;

const JOB = {
    id: JOB_ID,
    drive_id: 'E:',
    volume_label: 'TEST_SHOW_S1D1',
    content_type: 'tv',
    state: 'review_needed',
    detected_title: 'Test Show',
    detected_season: 1,
    progress_percent: 0,
};

const TITLES = Array.from({ length: 5 }, (_, i) => ({
    id: 100 + i,
    job_id: JOB_ID,
    title_index: i,
    duration_seconds: 180 + i * 30,
    file_size_bytes: 300 * 1024 * 1024,
    chapter_count: 2,
    is_selected: true,
    matched_episode: null,
    match_confidence: 0,
    state: 'matched',
}));

test('bulk "Mark as Extra" sends one batch request for every checked title', async ({ page }) => {
    let batchBody: { decisions?: Array<{ title_id: number; episode_code?: string }> } | null = null;

    await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        const method = route.request().method();

        if (url.includes('/review/batch') && method === 'POST') {
            batchBody = route.request().postDataJSON();
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ status: 'reviewed', job_id: JOB_ID, count: batchBody?.decisions?.length ?? 0 }),
            });
        }
        if (url.includes('/season-roster')) {
            return route.fulfill({ status: 404, body: '' });
        }
        if (url.endsWith('/api/config')) {
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ ai_episode_matching_enabled: false }),
            });
        }
        if (/\/api\/jobs\/\d+\/titles$/.test(url)) {
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(TITLES),
            });
        }
        if (/\/api\/jobs\/\d+$/.test(url)) {
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(JOB),
            });
        }
        return route.continue();
    });

    await page.goto(`/review/${JOB_ID}`);

    // The review list renders five title checkboxes.
    const checkboxes = page.getByRole('checkbox', { name: /Select title \d+ for bulk actions/ });
    await expect(checkboxes).toHaveCount(5);

    // Check the three "extra"-looking titles (indices 2,3,4).
    await checkboxes.nth(2).check();
    await checkboxes.nth(3).check();
    await checkboxes.nth(4).check();

    // The inline bulk bar appears with the running count.
    await expect(page.getByText('3 selected')).toBeVisible();

    // Apply the bulk action.
    await page.getByRole('button', { name: 'Mark as Extra' }).click();

    // Rows reflect the staged "extra" assignment, and the selection clears.
    await expect(page.getByText('extra', { exact: true })).toHaveCount(3);
    await expect(page.getByText('3 selected')).toHaveCount(0);

    // Commit via the header Save button (one batch request, three extras).
    await page.getByRole('button', { name: /^Save 3/ }).click();

    await expect.poll(() => batchBody?.decisions?.length ?? 0).toBe(3);
    expect(batchBody!.decisions!.every((d) => d.episode_code === 'extra')).toBe(true);
    expect(new Set(batchBody!.decisions!.map((d) => d.title_id))).toEqual(
        new Set([102, 103, 104]),
    );

    // On success the app navigates back to the dashboard.
    await expect(page).toHaveURL(/\/$/);
});

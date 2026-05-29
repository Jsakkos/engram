import { test, expect } from '@playwright/test';

/**
 * Episode-ordering selector (#200).
 *
 * The selector only appears when a show has a divergent alternative ordering,
 * which depends on TMDB episode-group data the simulation can't produce. So we
 * route-mock the four read endpoints (job, titles, config, season-roster) to
 * report a Firefly-like divergence, then assert the selector renders, that
 * picking "DVD Order" PUTs the per-show preference, and that the roster reloads
 * with the new ordering active.
 */

const JOB_ID = 1;
const TMDB_ID = 1437; // Firefly

const JOB = {
    id: JOB_ID,
    drive_id: 'E:',
    volume_label: 'FIREFLY_S1D1',
    content_type: 'tv',
    state: 'review_needed',
    detected_title: 'Firefly',
    detected_season: 1,
    tmdb_id: TMDB_ID,
    progress_percent: 0,
};

const TITLES = [
    {
        id: 100,
        job_id: JOB_ID,
        title_index: 0,
        duration_seconds: 3000,
        file_size_bytes: 300 * 1024 * 1024,
        chapter_count: 2,
        is_selected: true,
        matched_episode: 'S01E11', // "Serenity" — aired last, DVD first
        match_confidence: 0.8,
        state: 'matched',
    },
];

const ORDERING_OPTIONS = [
    { ordering: 'aired', label: 'Aired Order', tmdb_type: 1, diverges: false, projection: {} },
    {
        ordering: 'dvd',
        label: 'DVD Order',
        tmdb_type: 3,
        diverges: true,
        projection: { S01E11: 'S01E01' },
    },
];

function roster(currentOrdering: string) {
    return {
        available: true,
        season_number: 1,
        show_id: TMDB_ID,
        episodes: [
            { episode_code: 'S01E11', episode_number: 11, name: 'Serenity', status: 'assigned', assigned_title_ids: [100] },
        ],
        reason: null,
        ordering_available: true,
        ordering_diverges: true,
        current_ordering: currentOrdering,
        ordering_options: ORDERING_OPTIONS,
    };
}

test('the ordering selector appears on a divergent show and persists the choice', async ({ page }) => {
    let orderingPutBody: { ordering?: string } | null = null;
    let currentOrdering = 'aired';

    await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        const method = route.request().method();

        if (/\/api\/shows\/\d+\/ordering$/.test(url) && method === 'PUT') {
            orderingPutBody = route.request().postDataJSON();
            currentOrdering = orderingPutBody?.ordering ?? currentOrdering;
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ tmdb_id: TMDB_ID, ordering: currentOrdering, episode_group_id: 'grp_dvd' }),
            });
        }
        if (url.includes('/season-roster')) {
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(roster(currentOrdering)),
            });
        }
        if (url.endsWith('/api/config')) {
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ ai_episode_matching_enabled: false, episode_ordering_preference: 'aired' }),
            });
        }
        if (/\/api\/jobs\/\d+\/titles$/.test(url)) {
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TITLES) });
        }
        if (/\/api\/jobs\/\d+$/.test(url)) {
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(JOB) });
        }
        return route.continue();
    });

    await page.goto(`/review/${JOB_ID}`);

    // The selector is surfaced because the show diverges.
    await expect(page.getByText(/numbered differently across releases/i)).toBeVisible();
    const dvdButton = page.getByRole('button', { name: /DVD Order/ });
    await expect(dvdButton).toBeVisible();

    // Picking DVD Order persists the per-show preference.
    await dvdButton.click();
    await expect.poll(() => orderingPutBody?.ordering ?? null).toBe('dvd');

    // After the roster reloads, DVD Order is the active selection.
    await expect.poll(async () => {
        const cls = await page.getByRole('button', { name: /DVD Order/ }).getAttribute('style');
        return cls?.includes('font-weight: 700') ?? false;
    }).toBe(true);
});

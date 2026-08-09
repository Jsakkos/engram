import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

// The dedicated E2E backend, same port api-helpers uses.
const API = 'http://localhost:8001';

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.afterEach(async () => {
    // Leave the E2E backend clean so a leftover ripping job cannot bleed into the
    // next spec (the shared single-worker backend keeps jobs across tests).
    await resetAllJobs().catch(() => {});
});

test.describe('Track skipping — skip / un-skip a not-yet-ripped track', () => {
    test('a PENDING track can be skipped and un-skipped from the dashboard card', async ({ page }) => {
        // Insert an 8-track TV disc at the SLOWEST rip speed. The simulated rip
        // loop rips titles strictly in sequence (title[i] -> RIPPING, later
        // titles stay PENDING until the loop reaches them). At multiplier 1 each
        // track takes ~2s, so the later-indexed tracks sit PENDING for many
        // seconds — a wide, stable window to click SKIP before the loop arrives.
        // The SKIP control only renders for tracks in the PENDING state
        // (TrackGrid gates it there — a QUEUED track is already ripped to disk),
        // so we act on the LAST pending track, which the rip loop reaches last.
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
        });

        // Wait until the track grid is on screen and at least one SKIP control
        // has rendered (i.e. we've reached the RIPPING state with pending tracks).
        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });

        const skipButtons = page.getByTestId(/^skip-track-\d+$/);
        await expect(skipButtons.first()).toBeVisible({ timeout: 15000 });

        // Target the LAST skippable track — the rip loop reaches it last, so it
        // stays PENDING the longest and won't be flipped to RIPPING under us.
        const skipButton = skipButtons.last();

        // Resolve the concrete track id from the testid so we can assert against
        // this exact card (and find its matching UN-SKIP control) deterministically.
        const skipTestId = await skipButton.getAttribute('data-testid');
        expect(skipTestId).toMatch(/^skip-track-\d+$/);
        const trackId = skipTestId!.replace('skip-track-', '');

        // Click SKIP. The UI updates from the WebSocket title_update push that the
        // POST /skip-rip triggers. NOTE: after the skip the SKIP button is removed
        // and replaced by an UN-SKIP button, so the card can only be re-located via
        // the UN-SKIP control's id (a ":has(skip-track-N)" locator would go stale).
        await skipButton.click();

        // SKIPPED state: the SKIP control is replaced by an UN-SKIP control for the
        // same track, and the skipped-body text appears inside that track's card.
        const unskipButton = page.getByTestId(`unskip-track-${trackId}`);
        await expect(unskipButton).toBeVisible({ timeout: 10000 });
        await expect(page.getByTestId(`skip-track-${trackId}`)).toHaveCount(0);

        const skippedCard = page
            .locator(`${SELECTORS.trackItem}:has([data-testid="unskip-track-${trackId}"])`)
            .first();
        await expect(skippedCard.getByText('SKIPPED, WILL NOT RIP')).toBeVisible({ timeout: 10000 });

        // Un-skip: the SKIP control must return, the UN-SKIP control and the SKIPPED
        // text must clear.
        await unskipButton.click();

        await expect(page.getByTestId(`skip-track-${trackId}`)).toBeVisible({ timeout: 10000 });
        await expect(page.getByTestId(`unskip-track-${trackId}`)).toHaveCount(0);
        await expect(page.getByText('SKIPPED, WILL NOT RIP')).toHaveCount(0);
    });

    test('skipping later tracks leaves the in-flight rip running and the job finishing', async ({ page }) => {
        // Regression for the 0.28.2 report: skipping FUTURE tracks stopped the
        // track being ripped, and the disc either ejected "successfully" with
        // work outstanding or froze with no progress. Rip slowly enough that
        // several tracks are still PENDING when we click.
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 2,
        });

        await expect(page.locator(SELECTORS.trackGrid).first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByTestId(/^skip-track-\d+$/).first()).toBeVisible({ timeout: 15000 });

        // Exactly ONE skip goes through the button; the rest go through the API.
        //
        // While a rip is in flight the grid re-renders on every progress broadcast
        // and Framer Motion animates the cards, so a button is rarely "stable" in
        // Playwright's actionability sense, and each skip adds another churn burst.
        // Clicking three in a row reliably timed out on "element is not stable" ->
        // "element was detached from the DOM" in CI. Driving the extra skips over
        // HTTP removes that exposure without weakening what this test is for: the
        // button path is still exercised once, and the assertions below still cover
        // the full skip-everything-remaining scenario.
        //
        // Backend behaviour for multiple/trailing skips (including the title
        // boundary abort) is covered directly and deterministically by
        // backend/tests/unit/test_rip_skip_boundary_chain.py.
        const allIds = await page
            .getByTestId(/^skip-track-\d+$/)
            .evaluateAll((els) =>
                els.map((el) => el.getAttribute('data-testid')!.replace('skip-track-', '')),
            );
        // The rip loop reaches the highest-indexed tracks last, so they stay
        // PENDING longest and will not flip to RIPPING under us.
        const ids = allIds.slice(-3);
        expect(ids.length).toBe(3);

        const [clickedId, ...apiIds] = ids;
        await page.getByTestId(`skip-track-${clickedId}`).click();
        await expect(page.getByTestId(`unskip-track-${clickedId}`)).toBeVisible({ timeout: 10000 });

        const jobId = (await page.request.get(`${API}/api/jobs`).then((r) => r.json()))[0].id;
        for (const trackId of apiIds) {
            const res = await page.request.post(
                `${API}/api/jobs/${jobId}/titles/${trackId}/skip-rip`,
            );
            expect(res.ok()).toBe(true);
            await expect(page.getByTestId(`unskip-track-${trackId}`)).toBeVisible({ timeout: 10000 });
        }

        // The job must NOT fail. Before the fix, skipping every remaining track
        // ejected with work outstanding, and skipping some froze the job in
        // RIPPING until the watchdog force-advanced it to an error.
        await expect(page.locator(SELECTORS.stateFailed)).toHaveCount(0);

        // No track is left stuck mid-rip: the RIPPING indicator clears on its own.
        await expect(page.locator(SELECTORS.stateRipping)).toHaveCount(0, { timeout: 60000 });

        // Every skipped track still reads SKIPPED -- none was ripped after the fact.
        for (const id of ids) {
            const card = page
                .locator(`${SELECTORS.trackItem}:has([data-testid="unskip-track-${id}"])`)
                .first();
            await expect(card.getByText('SKIPPED, WILL NOT RIP')).toBeVisible({ timeout: 30000 });
        }
    });
});

import { test, expect } from '@playwright/test';
import { simulateInsertDisc, resetAllJobs } from './fixtures/api-helpers';
import { TV_DISC_ARRESTED_DEVELOPMENT } from './fixtures/disc-scenarios';
import { SELECTORS } from './fixtures/selectors';

const API = 'http://localhost:8001';

/** Deliberately not the E: the other scenarios use.
 *
 * Nothing stubs the hardware call in E2E, so this spec issues a REAL eject to
 * whatever drive the job names. On a developer machine with a disc in E:, that
 * would physically open the tray mid-rip of their own disc. A drive letter that
 * does not exist fails harmlessly and returns false, which is also the
 * ejected:false path worth exercising.
 */
const EJECT_TEST_DRIVE = 'Y:';

/** How many of a job's tracks were parked by an eject.
 *
 * A disc can reach review_needed from ordinary low-confidence matching, so
 * "the card says review" does NOT prove the eject did anything. Only the
 * rip_ejected marker does.
 */
async function ejectedTrackCount(jobId: number): Promise<number> {
    const response = await fetch(`${API}/api/jobs/${jobId}/detail`);
    const body = await response.json();
    const titles = body.titles ?? body.job?.titles ?? [];
    return titles.filter((t: { match_details?: string | null }) =>
        String(t.match_details ?? '').includes('rip_ejected'),
    ).length;
}

test.beforeEach(async ({ page }) => {
    await resetAllJobs().catch(() => {});
    await page.goto('/');
    await expect(page.locator(SELECTORS.connectionStatus.connected)).toBeVisible({ timeout: 10000 });
});

test.afterEach(async () => {
    // Leave the shared E2E backend clean so a leftover ripping job cannot bleed
    // into the next spec.
    await resetAllJobs().catch(() => {});
});

test.describe('Mid-rip disc eject', () => {
    test('ejecting mid-rip keeps the finished tracks and parks the rest in review', async ({
        page,
    }) => {
        // rip_speed_multiplier 1 is load-bearing, not a nicety: the scenario's
        // default of 50 finishes the whole disc faster than a single click, so
        // the eject would always land after the rip and the test would assert
        // nothing. At 1 each track takes roughly two seconds.
        const { job_id: jobId } = await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
            drive_id: EJECT_TEST_DRIVE,
        });

        const ejectButton = page.getByTestId('eject-button');
        await expect(ejectButton).toBeVisible({ timeout: 15000 });

        // Let at least one track finish so there is something to salvage.
        await page.waitForTimeout(3000);

        await ejectButton.click();

        // The modal must explain the salvage: that finished tracks survive is
        // the non-obvious part, and the reason this action is not Cancel.
        await expect(page.getByText(/the rest go to review/i)).toBeVisible();

        // Scoped to the dialog: the trigger shares the "Eject disc" accessible
        // name, which is correct for a confirm flow but ambiguous unscoped.
        const dialog = page.getByRole('dialog');
        await dialog.getByRole('button', { name: 'Eject disc' }).click();

        // The job holds tracks for the user, so it must land in review, and
        // must never read as failed: an eject is not a cancel.
        await expect(page.locator(SELECTORS.stateIndicator)).toContainText(/review/i, {
            timeout: 45000,
        });
        await expect(page.locator(SELECTORS.stateIndicator)).not.toContainText(/error|failed/i);

        // Guard against a vacuous pass. Low-confidence matching alone can land
        // a disc in review, so the state text above proves nothing on its own.
        // Only the rip_ejected marker proves the eject actually cut the rip
        // short and salvaged the rest.
        await expect
            .poll(() => ejectedTrackCount(jobId), { timeout: 15000 })
            .toBeGreaterThan(0);
    });

    test('the ejected tracks explain how to recover them', async ({ page }) => {
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
            drive_id: EJECT_TEST_DRIVE,
        });

        const ejectButton = page.getByTestId('eject-button');
        await expect(ejectButton).toBeVisible({ timeout: 15000 });
        await page.waitForTimeout(3000);
        await ejectButton.click();
        await page.getByRole('dialog').getByRole('button', { name: 'Eject disc' }).click();

        await expect(page.locator(SELECTORS.stateIndicator)).toContainText(/review/i, {
            timeout: 45000,
        });

        await page.goto('/review/1');

        // The backend's EJECTED_RIP_MESSAGE promises a Re-rip control, so the
        // review queue must actually offer one. It only renders when
        // rip_ejected is registered in the frontend's rip-failure code set, so
        // this also guards that registration.
        await expect(page.getByText(/ejected the disc/i).first()).toBeVisible({ timeout: 15000 });
        await expect(page.getByText(/reinsert the disc/i).first()).toBeVisible();
    });

    test('the eject control retires once the drive is free', async ({ page }) => {
        // A movie disc completes without parking anything in review, so the
        // card passes through matching and organizing with the disc already
        // released. The control must not linger into those states.
        await simulateInsertDisc({
            ...TV_DISC_ARRESTED_DEVELOPMENT,
            rip_speed_multiplier: 1,
            drive_id: EJECT_TEST_DRIVE,
        });

        await expect(page.getByTestId('eject-button')).toBeVisible({ timeout: 15000 });

        // Left alone, the rip finishes and the job moves past ripping.
        await expect(page.getByTestId('eject-button')).toBeHidden({ timeout: 90000 });
    });
});

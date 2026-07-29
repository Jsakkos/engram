/**
 * Playwright global setup — runs once before all tests.
 *
 * On a fresh database (e.g., CI), setup_complete is false and the ConfigWizard
 * modal blocks the entire UI. This marks setup as complete so tests can proceed.
 *
 * It also writes the storage state that playwright.config.ts loads into every
 * browser context, pre-seeding the "what's new" dedupe key. See seedWhatsNewKey().
 */

const API_BASE = 'http://localhost:8001';
const VITE_ORIGIN = 'http://localhost:5174';
const WHATS_NEW_KEY = 'engram:lastSeenVersion';
export const STORAGE_STATE_FILE = 'e2e-storage-state.json';

async function globalSetup() {
    // Wait for backend to be ready (webServer config starts it, but globalSetup
    // runs after webServer is up, so this is just a safety check)
    for (let i = 0; i < 30; i++) {
        try {
            const res = await fetch(`${API_BASE}/health`);
            if (res.ok) break;
        } catch {
            // Backend not ready yet
        }
        await new Promise((r) => setTimeout(r, 1000));
    }

    // Mark setup as complete and configure temp paths for CI.
    // Without valid paths, the organizer fails with "Permission denied"
    // when it resolves empty strings to Path(".") on Linux.
    const os = await import('os');
    const path = await import('path');
    const tmpBase = path.join(os.tmpdir(), 'engram-e2e');

    const res = await fetch(`${API_BASE}/api/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            setup_complete: true,
            staging_path: path.join(tmpBase, 'staging'),
            library_movies_path: path.join(tmpBase, 'movies'),
            library_tv_path: path.join(tmpBase, 'tv'),
        }),
    });

    if (!res.ok) {
        console.warn(`Failed to mark setup_complete: ${res.status} ${await res.text()}`);
    }

    await seedWhatsNewKey();
}

/**
 * Pre-seed the "what's new" modal's dedupe key into every browser context.
 *
 * The modal opens once per version, keyed on localStorage. An absent key plus a
 * completed setup means "an existing install that predates the key", which is a
 * real upgrade and correctly shows the modal. Every Playwright context starts
 * with empty localStorage and globalSetup marks setup complete, so that is
 * exactly the state each test would boot into: the modal opens over the
 * dashboard and its full-screen backdrop swallows every click. That is what it
 * is for, but it makes the app untestable.
 *
 * Seeding the key with the version actually running marks the notes as already
 * seen. The version is read from the API rather than hardcoded so it cannot rot
 * at the next release; a hardcoded stale value would be worse than no seed at
 * all, since a key that merely *differs* from the running version also opens
 * the modal.
 */
async function seedWhatsNewKey() {
    const fs = await import('fs');
    const path = await import('path');
    const url = await import('url');

    let currentVersion = '';
    try {
        const statusRes = await fetch(`${API_BASE}/api/updates/status`);
        if (statusRes.ok) {
            const reported = (await statusRes.json()).current_version;
            // Validate before this reaches the file. The value is a version
            // string from our own localhost backend, but it is still a response
            // body flowing into a write, so constrain it to the shape a version
            // can actually take rather than trusting it. This also turns a
            // malformed response into a loud warning instead of a junk seed.
            if (typeof reported === 'string' && /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/.test(reported)) {
                currentVersion = reported;
            } else if (reported !== undefined) {
                console.warn(`Ignoring malformed current_version: ${JSON.stringify(reported)}`);
            }
        }
    } catch {
        // Handled by the empty-version warning below.
    }

    if (!currentVersion) {
        console.warn(
            'Could not read a usable current_version from /api/updates/status. The ' +
                "what's-new modal may open over the UI and block clicks.",
        );
    }

    const stateFile = path.resolve(
        path.dirname(url.fileURLToPath(import.meta.url)),
        '..',
        STORAGE_STATE_FILE,
    );

    fs.writeFileSync(
        stateFile,
        JSON.stringify({
            cookies: [],
            origins: [
                {
                    origin: VITE_ORIGIN,
                    localStorage: [{ name: WHATS_NEW_KEY, value: currentVersion }],
                },
            ],
        }),
    );
}

export default globalSetup;

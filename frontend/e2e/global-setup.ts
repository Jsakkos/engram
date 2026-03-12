/**
 * Playwright global setup — runs once before all tests.
 *
 * On a fresh database (e.g., CI), setup_complete is false and the ConfigWizard
 * modal blocks the entire UI. This marks setup as complete so tests can proceed.
 */

const API_BASE = 'http://localhost:8000';

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
}

export default globalSetup;

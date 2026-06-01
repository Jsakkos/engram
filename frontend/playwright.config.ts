import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

// E2E tests use a dedicated backend (port 8001) and Vite server (port 5174)
// with a separate database so that reset-all-jobs and other destructive test
// operations never touch the dev database (engram.db).
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const E2E_BACKEND_PORT = 8001;
const E2E_VITE_PORT = 5174;
const E2E_DB_PATH = path.resolve(__dirname, '../backend/engram_e2e.db');
const E2E_DATABASE_URL = `sqlite+aiosqlite:///${E2E_DB_PATH}`;
const E2E_BACKEND_URL = `http://localhost:${E2E_BACKEND_PORT}`;
const E2E_VITE_URL = `http://localhost:${E2E_VITE_PORT}`;

// Specs the Firefox + WebKit projects run — the CSS/UI-correctness suite.
// These assert rendering (branding, atmosphere, styling, colors, empty states,
// and the /review page), which is where cross-browser bugs actually surface.
const CROSS_BROWSER_SPECS = [
    /basic-ui-verification\.spec\.ts/,
    /visual-verification\.spec\.ts/,
    /review-flow\.spec\.ts/,
];

export default defineConfig({
    globalSetup: './e2e/global-setup.ts',
    testDir: './e2e',
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: 1,
    reporter: 'html',
    use: {
        baseURL: E2E_VITE_URL,
        trace: 'on-first-retry',
        // Settle Framer springs, CSS keyframes, and the rip canvas for stable
        // screenshots. The app honors prefers-reduced-motion (theme.css media
        // query + Framer useReducedMotion + SvRipAnimation canvas hook).
        reducedMotion: 'reduce',
    },
    // Chromium runs the full suite. Firefox + WebKit exist to catch
    // cross-browser *rendering* bugs (the Safari mix-blend-mode / backdrop-filter
    // blackout that motivated them), so they run only the CSS/UI-correctness
    // specs — including review-flow, which renders the /review page that was the
    // original bug. The simulation-heavy disc-flow specs exercise
    // browser-independent backend orchestration (state machines, name-prompt
    // round-trips, multi-drive) and are timing-fragile against the shared
    // single-worker E2E backend, so running them on three engines adds flakiness
    // without CSS-detection value — they stay Chromium-only.
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
        {
            name: 'firefox',
            use: { ...devices['Desktop Firefox'] },
            testMatch: CROSS_BROWSER_SPECS,
        },
        {
            name: 'webkit',
            use: { ...devices['Desktop Safari'] },
            testMatch: CROSS_BROWSER_SPECS,
        },
    ],
    webServer: [
        {
            command: `cd ../backend && uv run uvicorn app.main:app --port ${E2E_BACKEND_PORT}`,
            url: `${E2E_BACKEND_URL}/health`,
            reuseExistingServer: false,
            timeout: 30000,
            env: {
                ...process.env,
                DEBUG: 'true',
                // DEBUG enables the /api/simulate/* endpoints, but we explicitly
                // keep SQLAlchemy echo OFF: echoing every statement during a
                // simulated rip floods stdout and stalls the single-worker event
                // loop, which caused flaky visibility-assertion timeouts. This is
                // already the default (Settings.db_echo=False) — set here too so
                // the intent is obvious at the E2E backend launch site.
                DB_ECHO: 'false',
                DATABASE_URL: E2E_DATABASE_URL,
            },
        },
        {
            command: 'npm run dev',
            url: E2E_VITE_URL,
            reuseExistingServer: false,
            timeout: 15000,
            env: {
                ...process.env,
                VITE_PORT: String(E2E_VITE_PORT),
                VITE_BACKEND_PORT: String(E2E_BACKEND_PORT),
            },
        },
    ],
});

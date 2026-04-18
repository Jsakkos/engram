import { defineConfig, devices } from '@playwright/test';
import path from 'path';

// E2E tests use a dedicated backend (port 8001) and Vite server (port 5174)
// with a separate database so that reset-all-jobs and other destructive test
// operations never touch the dev database (engram.db).
const E2E_BACKEND_PORT = 8001;
const E2E_VITE_PORT = 5174;
const E2E_DB_PATH = path.resolve(__dirname, '../backend/engram_e2e.db');
const E2E_DATABASE_URL = `sqlite+aiosqlite:///${E2E_DB_PATH}`;
const E2E_BACKEND_URL = `http://localhost:${E2E_BACKEND_PORT}`;
const E2E_VITE_URL = `http://localhost:${E2E_VITE_PORT}`;

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
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
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

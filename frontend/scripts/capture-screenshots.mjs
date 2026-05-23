#!/usr/bin/env node
// Regenerate the committed UI screenshots used by README.md and the docs.
//
// 1. Runs the Playwright screenshot-workflow spec, which writes every major UI
//    state to the gitignored frontend/e2e-screenshots/workflow/ directory.
// 2. Copies a curated subset into the tracked docs/screenshots/ directory.
//
// Run locally (Windows dev machine) so the committed PNGs stay visually
// consistent — CI runs on Ubuntu where font rendering differs.
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";

const frontendDir = resolve(import.meta.dirname, "..");
const srcDir = join(frontendDir, "e2e-screenshots", "workflow");
const destDir = resolve(frontendDir, "..", "docs", "screenshots");

// workflow output (without .png) -> committed filename(s) in docs/screenshots.
// 01-empty-state is an alias of the same empty-dashboard frame, referenced from
// docs/guide/dashboard.md.
const CURATED = {
    "01-initial-state": ["01-initial-state.png", "01-empty-state.png"],
    "02-card-appeared": ["02-card-appeared.png"],
    "03-ripping-state": ["03-ripping-state.png"],
    "05-per-track-ripping": ["05-per-track-ripping.png"],
    "08-match-candidates": ["08-match-candidates.png"],
    "09-completed": ["09-completed.png"],
    "10-movie-card": ["10-movie-card.png"],
    "12-movie-completed": ["12-movie-completed.png"],
    "18-name-prompt-modal": ["18-name-prompt-modal.png"],
    "20-settings-step1-paths": ["20-settings-step1-paths.png"],
    "22-settings-step3-tmdb": ["22-settings-step3-tmdb.png"],
    "24-history-page": ["24-history-page.png"],
    "26-review-page": ["26-review-page.png"],
};

console.log("Running screenshot-workflow spec...\n");
// `playwright` (not `npx playwright`) so it resolves the local install — npm
// puts node_modules/.bin on PATH for run-scripts, matching the test:e2e script.
const run = spawnSync("playwright test e2e/screenshot-workflow.spec.ts", {
    cwd: frontendDir,
    stdio: "inherit",
    shell: true,
});
if (run.status !== 0) {
    // spawnSync sets run.error (and leaves status null) when the child fails to
    // launch — e.g. the playwright binary isn't on PATH.
    if (run.error) console.error("Failed to launch playwright:", run.error.message);
    console.error("\nPlaywright run failed; screenshots not copied.");
    process.exit(run.status ?? 1);
}

mkdirSync(destDir, { recursive: true });

const missing = [];
let copied = 0;
for (const [src, dests] of Object.entries(CURATED)) {
    const srcPath = join(srcDir, `${src}.png`);
    if (!existsSync(srcPath)) {
        missing.push(`${src}.png`);
        continue;
    }
    for (const dest of dests) {
        copyFileSync(srcPath, join(destDir, dest));
        console.log(`  ${src}.png -> docs/screenshots/${dest}`);
        copied += 1;
    }
}

if (missing.length) {
    console.error(`\nExpected workflow outputs were missing: ${missing.join(", ")}`);
    console.error(`Look in ${srcDir} — did the spec capture them?`);
    process.exit(1);
}

console.log(`\nCopied ${copied} screenshot(s) into docs/screenshots/.`);

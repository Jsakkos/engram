#!/usr/bin/env node
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { resolve, join } from "node:path";

const BUDGETS_KB = {
    "js-gzip-total": 600,
    "css-gzip-total": 60,
    "single-chunk-gzip": 350,
};

const distDir = resolve(import.meta.dirname, "../dist/assets");
let entries;
try {
    entries = readdirSync(distDir, { withFileTypes: true });
} catch {
    console.error(`Could not read ${distDir}. Did "npm run build" run first?`);
    process.exit(1);
}

const sizes = { js: [], css: [] };
for (const entry of entries) {
    // Use dirent metadata to avoid a TOCTOU race between stat and read
    if (!entry.isFile()) continue;
    const { name } = entry;
    if (!name.endsWith(".js") && !name.endsWith(".css")) continue;
    const buf = readFileSync(join(distDir, name));
    const gzKb = gzipSync(buf).length / 1024;
    if (name.endsWith(".js")) sizes.js.push({ name, gzKb });
    else sizes.css.push({ name, gzKb });
}

const totalJs = sizes.js.reduce((s, x) => s + x.gzKb, 0);
const totalCss = sizes.css.reduce((s, x) => s + x.gzKb, 0);
const maxChunk = sizes.js.reduce((m, x) => Math.max(m, x.gzKb), 0);

console.log("Bundle sizes (gzip):");
for (const { name, gzKb } of [...sizes.js, ...sizes.css].sort((a, b) => b.gzKb - a.gzKb)) {
    console.log(`  ${name.padEnd(50)} ${gzKb.toFixed(1)} KB`);
}
console.log("");
console.log(`Total JS  (gzip): ${totalJs.toFixed(1)} KB  / budget ${BUDGETS_KB["js-gzip-total"]} KB`);
console.log(`Total CSS (gzip): ${totalCss.toFixed(1)} KB  / budget ${BUDGETS_KB["css-gzip-total"]} KB`);
console.log(`Largest JS chunk: ${maxChunk.toFixed(1)} KB  / budget ${BUDGETS_KB["single-chunk-gzip"]} KB`);

const failures = [];
if (totalJs > BUDGETS_KB["js-gzip-total"]) failures.push(`JS total ${totalJs.toFixed(1)} KB > budget ${BUDGETS_KB["js-gzip-total"]} KB`);
if (totalCss > BUDGETS_KB["css-gzip-total"]) failures.push(`CSS total ${totalCss.toFixed(1)} KB > budget ${BUDGETS_KB["css-gzip-total"]} KB`);
if (maxChunk > BUDGETS_KB["single-chunk-gzip"]) failures.push(`Largest chunk ${maxChunk.toFixed(1)} KB > budget ${BUDGETS_KB["single-chunk-gzip"]} KB`);

if (failures.length) {
    console.error("\nBundle size budget exceeded:");
    for (const f of failures) console.error(`  - ${f}`);
    console.error("\nTo adjust budgets, edit BUDGETS_KB in frontend/scripts/check-bundle-size.mjs.");
    process.exit(1);
}
console.log("\nBundle size OK.");

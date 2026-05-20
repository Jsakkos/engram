/**
 * Emit standalone SVG sources for the Engram brand mark and app icon.
 *
 * Why a script, not a React render? The downstream rasterizer needs plain
 * SVG strings on disk to feed `sharp`. We could mount a React tree, but
 * that pulls in JSX transforms, jsdom, and a renderer just to stringify
 * geometry that is fully knowable at build time. Inlining the path data
 * keeps this script a single Node module with zero runtime dependencies.
 *
 * Outputs under `frontend/public/brand/sources/`:
 *   - mark.svg              full primary mark (arcs + read-line + node)
 *   - mark-mono.svg         monogram only (≤32px favicon use)
 *   - app-icon-dark.svg     1024-square dark edition with squircle chrome
 *   - app-icon-light.svg    paper edition
 *
 * Geometry is in lockstep with frontend/src/app/components/synapse/
 * (SvMark.tsx, MarkMono.tsx, AppIcon.tsx). When the React component
 * changes, mirror the change here.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = join(__dirname, "..", "public", "brand", "sources");

const CYAN = "#5eead4";
const MAGENTA = "#ff3d7f";
const BG0 = "#05070c";
const PAPER = "#F3EEE4";
const PAPER_INK = "#15161A";

mkdirSync(OUTPUT_DIR, { recursive: true });

/** Three open arcs + read-line + node. Standalone (no <svg> chrome). */
function markGeometry({ color, accent, includeReadLine }) {
  const arcs = [
    { d: "M 32 8 A 24 24 0 1 0 32 56", opacity: 1 },
    { d: "M 32 16 A 16 16 0 1 0 32 48", opacity: 0.78 },
    { d: "M 32 24 A 8 8 0 1 0 32 40", opacity: 0.55 },
  ];
  const arcPaths = arcs
    .map(
      (a) =>
        `<path d="${a.d}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" opacity="${a.opacity}"/>`
    )
    .join("\n  ");

  if (!includeReadLine) return arcPaths;

  return (
    arcPaths +
    `\n  <line x1="32" y1="32" x2="56" y2="32" stroke="${accent}" stroke-width="2.5" stroke-linecap="round"/>` +
    `\n  <circle cx="56" cy="32" r="3.5" fill="${accent}"/>` +
    `\n  <circle cx="56" cy="32" r="6.5" fill="${accent}" opacity="0.18"/>`
  );
}

const markSvg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="512" height="512">
  ${markGeometry({ color: CYAN, accent: MAGENTA, includeReadLine: true })}
</svg>
`;

const markMonoSvg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="512" height="512">
  ${markGeometry({ color: CYAN, includeReadLine: false }).replace(/stroke-width="2.5"/g, 'stroke-width="3"')}
</svg>
`;

/**
 * App icon — a 1024-square squircle filled with the mark + chrome.
 * SVG renders the squircle via `<clipPath>` + a rounded rectangle.
 * Apple HIG: corner radius is `size * 0.2237`. At 1024px that's 229.
 */
function appIconSvg({ dark }) {
  const SIZE = 1024;
  const radius = Math.round(SIZE * 0.2237);
  const inset = SIZE * 0.18;
  const markSize = SIZE - inset * 2;

  const background = dark
    ? `<defs>
    <radialGradient id="bg" cx="30%" cy="20%" r="80%">
      <stop offset="0%" stop-color="#102031"/>
      <stop offset="60%" stop-color="${BG0}"/>
      <stop offset="100%" stop-color="#02030a"/>
    </radialGradient>
    <clipPath id="squircle">
      <rect x="0" y="0" width="${SIZE}" height="${SIZE}" rx="${radius}" ry="${radius}"/>
    </clipPath>
  </defs>
  <rect x="0" y="0" width="${SIZE}" height="${SIZE}" rx="${radius}" ry="${radius}" fill="url(#bg)"/>`
    : `<defs>
    <clipPath id="squircle">
      <rect x="0" y="0" width="${SIZE}" height="${SIZE}" rx="${radius}" ry="${radius}"/>
    </clipPath>
  </defs>
  <rect x="0" y="0" width="${SIZE}" height="${SIZE}" rx="${radius}" ry="${radius}" fill="${PAPER}"/>`;

  // Ring grid (dark only), drawn into the icon viewBox.
  const ringGrid = dark
    ? `\n  <g clip-path="url(#squircle)">
    ${[50, 40, 30, 20]
      .map(
        (r, i) =>
          `<circle cx="${SIZE / 2}" cy="${SIZE / 2}" r="${(r * SIZE) / 128}" fill="none" stroke="${CYAN}" stroke-width="${(0.4 * SIZE) / 128}" opacity="${(0.04 + i * 0.02).toFixed(2)}"/>`
      )
      .join("\n    ")}
  </g>`
    : "";

  // Mark centered inside the inset area. Re-uses the 64-unit viewBox by
  // placing it inside an <svg> with explicit width/height.
  const markColor = dark ? CYAN : PAPER_INK;
  const accentColor = dark ? MAGENTA : PAPER_INK;
  const includeNode = true;

  const arcs = [
    { d: "M 32 8 A 24 24 0 1 0 32 56", opacity: 1 },
    { d: "M 32 16 A 16 16 0 1 0 32 48", opacity: 0.78 },
    { d: "M 32 24 A 8 8 0 1 0 32 40", opacity: 0.55 },
  ]
    .map(
      (a) =>
        `<path d="${a.d}" fill="none" stroke="${markColor}" stroke-width="2.5" stroke-linecap="round" opacity="${a.opacity}"/>`
    )
    .join("\n      ");

  const readLine = includeNode
    ? `\n      <line x1="32" y1="32" x2="56" y2="32" stroke="${accentColor}" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="56" cy="32" r="3.5" fill="${accentColor}"/>
      <circle cx="56" cy="32" r="6.5" fill="${accentColor}" opacity="0.18"/>`
    : "";

  const markBlock = `<svg x="${inset}" y="${inset}" width="${markSize}" height="${markSize}" viewBox="0 0 64 64">
      ${arcs}${readLine}
    </svg>`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}" width="${SIZE}" height="${SIZE}">
  ${background}${ringGrid}
  <g clip-path="url(#squircle)">
    ${markBlock}
  </g>
</svg>
`;
}

writeFileSync(join(OUTPUT_DIR, "mark.svg"), markSvg);
writeFileSync(join(OUTPUT_DIR, "mark-mono.svg"), markMonoSvg);
writeFileSync(join(OUTPUT_DIR, "app-icon-dark.svg"), appIconSvg({ dark: true }));
writeFileSync(join(OUTPUT_DIR, "app-icon-light.svg"), appIconSvg({ dark: false }));

console.log(`✓ Wrote SVG sources to ${OUTPUT_DIR}`);

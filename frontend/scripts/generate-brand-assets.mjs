/**
 * Rasterize the SVG sources emitted by `render-svg-sources.mjs` into the
 * full set of brand assets: favicons (PNG + multi-resolution .ico), the
 * Windows .ico, the macOS .icns, and a folder of loose PNGs for Linux.
 *
 * Cross-platform: uses `sharp` for SVG → PNG and `png2icons` for both
 * .ico and .icns. No `iconutil` (mac-only) or `ImageMagick` (system pkg)
 * required — pure JS.
 *
 * Idempotent: every output is overwritten on each run. Safe to commit the
 * outputs and re-run the script when the mark geometry changes.
 */

import { mkdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";
import png2icons from "png2icons";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BRAND_DIR = join(__dirname, "..", "public", "brand");
const SOURCES = join(BRAND_DIR, "sources");
const FAVICONS = join(BRAND_DIR, "favicons");
const ICONS_WIN = join(BRAND_DIR, "app-icons", "windows");
const ICONS_MAC = join(BRAND_DIR, "app-icons", "macos");
const ICONS_MAC_ISET = join(ICONS_MAC, "iconset");
const ICONS_LINUX = join(BRAND_DIR, "app-icons", "linux");

async function rasterize(svgPath, size) {
  const svg = readFileSync(svgPath);
  return sharp(svg).resize(size, size).png().toBuffer();
}

/** Pack a set of PNG buffers into a .ico or .icns. */
function pack({ buffers, format }) {
  // png2icons.createICO/createICNS take a single PNG buffer at the highest
  // resolution and internally downscale to the required sizes.
  const largest = buffers[buffers.length - 1];
  const interp = png2icons.BICUBIC;
  if (format === "ico") {
    return png2icons.createICO(largest, interp, 0, false, true);
  }
  if (format === "icns") {
    return png2icons.createICNS(largest, interp, 0);
  }
  throw new Error(`Unknown format: ${format}`);
}

async function main() {
  for (const dir of [FAVICONS, ICONS_WIN, ICONS_MAC, ICONS_MAC_ISET, ICONS_LINUX]) {
    mkdirSync(dir, { recursive: true });
  }

  // ── Favicons ──────────────────────────────────────────────────────────
  //
  // 16/24 px use the monogram (per the handoff — the read-line node does
  // not render cleanly that small). 32/48/64 use the full mark.
  console.log("▸ Rendering favicons…");

  const markFullSvg = join(SOURCES, "mark.svg");
  const markMonoSvg = join(SOURCES, "mark-mono.svg");

  const FAVICON_SIZES = [
    { px: 16, source: markMonoSvg },
    { px: 24, source: markMonoSvg },
    { px: 32, source: markFullSvg },
    { px: 48, source: markFullSvg },
    { px: 64, source: markFullSvg },
  ];

  for (const { px, source } of FAVICON_SIZES) {
    const buf = await rasterize(source, px);
    writeFileSync(join(FAVICONS, `favicon-${px}.png`), buf);
    console.log(`  · favicon-${px}.png`);
  }

  // favicon.svg — copy the full mark SVG (browsers that prefer SVG use this)
  copyFileSync(markFullSvg, join(FAVICONS, "favicon.svg"));
  console.log("  · favicon.svg");

  // favicon.ico — multi-resolution (16/32/48). png2icons packs a single
  // large PNG and internally generates the sub-sizes.
  const icoBuf = await rasterize(markFullSvg, 256).then((largest) =>
    png2icons.createICO(largest, png2icons.BICUBIC, 0, false, true)
  );
  if (!icoBuf) throw new Error("png2icons.createICO returned null");
  writeFileSync(join(FAVICONS, "favicon.ico"), icoBuf);
  console.log("  · favicon.ico");

  // ── Windows app icon ──────────────────────────────────────────────────
  console.log("▸ Rendering Windows .ico…");

  const appIconDarkSvg = join(SOURCES, "app-icon-dark.svg");
  const WIN_SIZES = [16, 24, 32, 48, 64, 128, 256];
  const winBuffers = await Promise.all(WIN_SIZES.map((s) => rasterize(appIconDarkSvg, s)));
  const winIco = pack({ buffers: winBuffers, format: "ico" });
  if (!winIco) throw new Error("png2icons.createICO (windows) returned null");
  writeFileSync(join(ICONS_WIN, "engram.ico"), winIco);
  console.log(`  · engram.ico (${WIN_SIZES.join(", ")})`);

  // ── macOS app icon ────────────────────────────────────────────────────
  console.log("▸ Rendering macOS .icns + iconset…");

  const MAC_SIZES = [16, 32, 64, 128, 256, 512, 1024];
  const macBuffers = await Promise.all(MAC_SIZES.map((s) => rasterize(appIconDarkSvg, s)));

  for (const [i, size] of MAC_SIZES.entries()) {
    // macOS iconset naming convention: icon_<size>x<size>.png + @2x variants.
    // The non-@2x version is the "1x" rendering at that nominal size.
    writeFileSync(join(ICONS_MAC_ISET, `icon_${size}x${size}.png`), macBuffers[i]);
  }

  const icnsBuf = pack({ buffers: macBuffers, format: "icns" });
  if (!icnsBuf) throw new Error("png2icons.createICNS returned null");
  writeFileSync(join(ICONS_MAC, "engram.icns"), icnsBuf);
  console.log(`  · engram.icns (${MAC_SIZES.join(", ")})`);
  console.log(`  · iconset/ (${MAC_SIZES.length} loose PNGs)`);

  // ── Linux PNGs ────────────────────────────────────────────────────────
  console.log("▸ Rendering Linux PNGs…");

  const LINUX_SIZES = [32, 48, 64, 128, 256];
  for (const size of LINUX_SIZES) {
    const buf = await rasterize(appIconDarkSvg, size);
    writeFileSync(join(ICONS_LINUX, `engram-${size}.png`), buf);
    console.log(`  · engram-${size}.png`);
  }

  // ── Manifest ──────────────────────────────────────────────────────────
  // Only the array-valued keys hold file paths; `generated` is a scalar
  // metadata field. Counting via an explicit filter (rather than
  // `Object.values(...).flat().length - 1`) means new scalar keys won't
  // silently break the count.
  const fileGroups = {
    favicons: FAVICON_SIZES.map(({ px }) => `favicons/favicon-${px}.png`).concat([
      "favicons/favicon.svg",
      "favicons/favicon.ico",
    ]),
    windows: ["app-icons/windows/engram.ico"],
    macos: ["app-icons/macos/engram.icns"].concat(
      MAC_SIZES.map((s) => `app-icons/macos/iconset/icon_${s}x${s}.png`)
    ),
    linux: LINUX_SIZES.map((s) => `app-icons/linux/engram-${s}.png`),
  };
  const manifest = { generated: new Date().toISOString(), ...fileGroups };
  // Trailing newline keeps the pre-commit end-of-file-fixer happy.
  writeFileSync(
    join(BRAND_DIR, "manifest.json"),
    JSON.stringify(manifest, null, 2) + "\n",
  );

  const totalAssets = Object.values(fileGroups).reduce((n, files) => n + files.length, 0);
  console.log(`\n✓ Wrote ${totalAssets} brand assets to ${BRAND_DIR}`);
}

main().catch((err) => {
  // Surface the failure with a non-zero exit so CI / npm-script callers
  // see it. Top-level await would do this implicitly via the unhandled-
  // rejection path, but explicit is safer across Node versions.
  console.error(err);
  process.exit(1);
});

# Handoff: Engram Brand System

## Overview
Engram is a media-archive desktop application — it watches an optical drive,
rips discs, matches the resulting titles to TMDB metadata, and files them
into a library. This handoff packages the **brand system** that supports that
product: the primary mark, wordmark, lockups, color palette, type pairing,
icon set, and applications (app icon, favicon, splash, dock states, terminal
banner).

The visual direction is **"Synapse v2"** — a cyberpunk / Blade Runner 2049
aesthetic. Near-black surfaces with cyan + magenta accents, light scanlines
and grain, technical mono typography, and surveillance-equipment chrome
(corner ticks, telemetry labels). The brand reads as **equipment**, not as a
SaaS product.

## About the Design Files
The files in this bundle are **design references created in HTML/JSX** —
prototypes showing intended look and behavior, **not production code to copy
directly**. They use a small custom React component library (`DesignCanvas`,
`BPanel`, `BLabel`, `BAtmosphere`, etc.) purely for presentation.

The task is to **recreate this brand system in the target codebase's
environment**. Engram is intended to ship as an Electron / Tauri-style
desktop app, so a React + TypeScript + CSS-in-JS or CSS-Modules implementation
is the most likely target — but adapt to whatever stack the application
codebase already uses. The icons should be re-exported as standalone React
components (or SVG sprites) and the mark/lockups should be reproducible at
any size from SVG sources.

## Fidelity
**High-fidelity.** Every color, font, weight, tracking, stroke width, and
geometric construction in the HTML is the final intended value. The
construction artboard documents the exact mark geometry on a 64-unit grid.
Reproduce pixel-perfectly.

---

## Brand Concept

An **engram** is a memory trace — the physical/biochemical mark a memory
leaves in neural tissue. The product's job is to take ephemeral disc media
and convert it into permanent, archived memory. The brand mark encodes this
literally:

- **Three concentric open arcs** = the rings of a disc / the rings of
  hippocampal replay / a stylized "E"
- **A horizontal read-line crossing into a node** = the trace being
  written, the data being captured

The brand voice is **clinical, technical, slightly menacing** — like the
UI on a piece of equipment you'd find in a forensic lab. Labels are
uppercase mono with wide tracking. Numbers and telemetry are first-class
typographic elements.

---

## Design Tokens

### Color

| Token       | Hex       | Role                         |
| ----------- | --------- | ---------------------------- |
| `bg0`       | `#05070C` | Base surface (near-black)    |
| `bg1`       | `#0A0E18` | Raised surface               |
| `bg2`       | `#121827` | Panel surface                |
| `bg3`       | `#1A2234` | Hover / pressed              |
| `ink`       | `#E6ECF5` | Primary type                 |
| `inkDim`    | `#8893A8` | Secondary type / labels      |
| `inkFaint`  | `#4A5369` | Tertiary type                |
| `inkGhost`  | `#2A3147` | Disabled                     |
| **`cyan`**  | `#5EEAD4` | **Primary brand accent**     |
| `cyanHi`    | `#9FF8E8` | Cyan highlight (glow)        |
| `cyanDim`   | `#2DD4BF` | Cyan pressed                 |
| **`magenta`** | `#FF3D7F` | **Active state / ripping** |
| `magentaHi` | `#FF7AA5` | Magenta highlight            |
| `yellow`    | `#FDE047` | Scanning                     |
| `amber`     | `#FCD34D` | Matching / warn              |
| `green`     | `#86EFAC` | Complete                     |
| `red`       | `#FF5555` | Error                        |
| `paper`     | `#F3EEE4` | Light edition background     |
| `inkOnPaper`| `#15161A` | Light edition ink            |

**Line tokens** (rgba expressions of cyan, used for borders):

| Token      | Value                          |
| ---------- | ------------------------------ |
| `line`     | `rgba(94, 234, 212, 0.14)`     |
| `lineMid`  | `rgba(94, 234, 212, 0.24)`     |
| `lineHi`   | `rgba(94, 234, 212, 0.42)`     |

### Typography

Two families only.

**Display + body — Chakra Petch** (Google Fonts)
- Weights used: 400, 500, 600, 700
- The wordmark "ENGRAM" is 700, uppercase, with `0.14em` letter-spacing at
  display sizes; track wider (`0.20em`–`0.26em`) at smaller sizes
- Body running text: 500, normal case
- Section headings: 700, normal case, `0.04em` letter-spacing

**Mono — JetBrains Mono** (Google Fonts)
- Weights used: 300, 400, 500, 600, 700
- All caps labels: 500, `0.20em` letter-spacing
- Telemetry / numbers: 500–600
- Code / paths: 400, normal tracking

**Type scale (px):**
- Hero wordmark: 96–112
- Section heading: 22–28
- Body: 14
- Mono label: 10–11
- Mono micro: 9

### Spacing & geometry

- All chrome uses **1px borders** (never thicker) at brand color tokens
- **Corner ticks** are 8px L-shapes anchored to all four corners of any
  bordered panel — they sit -1px outside the border so they read as crisp
  hairlines. Stroke 1.5px, color `lineHi` (or accent color for accented panels)
- Mark stroke: **2.5px** on a 64-unit viewbox (scales with the SVG)
- Icon stroke: **1.5px** on a 24-unit viewbox, `round` caps and joins
- App icon corner radius: **22.37% of side length** (Apple HIG squircle)

### Atmosphere effects

These are applied to full-bleed surfaces (splash, hero, dashboard). They
are subtle — do not over-apply.

1. **Ambient haze**: two radial gradients
   - `radial-gradient(ellipse at 15% 20%, rgba(94,234,212,0.10), transparent 55%)`
   - `radial-gradient(ellipse at 85% 90%, rgba(255,61,127,0.07), transparent 50%)`
2. **Scanlines**: `repeating-linear-gradient(0deg, rgba(94,234,212,0.05) 0 1px, transparent 1px 3px)` at `opacity: 0.30`
3. **Vignette**: `radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.5) 100%)`
4. **Grain** (optional, very subtle): SVG `<feTurbulence baseFrequency="0.85">` at `opacity: 0.08, mix-blend-mode: overlay`

---

## The Mark — geometric construction

The mark is drawn in a `0 0 64 64` SVG viewBox. Center is `(32, 32)`.

### Primary mark (full)
```
Three open arcs, all opening to the right (sweep-flag 0, large-arc 1):
  Outer:  M 32 8  A 24 24 0 1 0 32 56   stroke 2.5  opacity 1.00
  Mid:    M 32 16 A 16 16 0 1 0 32 48   stroke 2.5  opacity 0.78
  Inner:  M 32 24 A  8  8 0 1 0 32 40   stroke 2.5  opacity 0.55
  → all use the primary color (cyan by default)

Read-line + node (magenta):
  line:   x1=32 y1=32  x2=56 y2=32      stroke 2.5
  node:   cx=56 cy=32  r=3.5             fill (magenta)
  glow:   cx=56 cy=32  r=6.5             fill (magenta) opacity 0.18

Optional background glow (only at large sizes):
  radial-gradient cx=50% cy=50% r=55%, stopColor primary 0.22→0
```

### Monogram (no dendrite)
Same three arcs, **no** read-line or node. Used for favicons ≤ 32px where the
dendrite would not render. Stroke can be bumped to **3px** to compensate for
small render sizes.

### Clear space
The minimum required margin around any lockup is `X`, where **X = the
cap-height of the wordmark** at the rendered size. Apply X on all four sides.

### Minimum sizes
| Asset             | Minimum size |
| ----------------- | ------------ |
| Horizontal lockup | 24 px tall   |
| Wordmark alone    | 12 px tall   |
| Mark alone        | 16 px        |
| Monogram          | 12 px        |

---

## Lockups

Four authorized lockups, in priority order:

### 01 — Horizontal (primary)
- Mark + wordmark, baseline-aligned
- Gap between mark and wordmark = `size * 0.34` (where `size` is the
  wordmark font size)
- Mark height = `wordmark font size * 1.18`

### 02 — Stacked
- Mark above wordmark, both centered
- Vertical gap between mark and wordmark = `size * 0.30`
- Mark height = `wordmark font size * 1.6` (larger because it's the focal element)

### 03 — Horizontal + descriptor
- Same as 01, but the wordmark has a mono descriptor line beneath it
- Default descriptor: `MEDIA ARCHIVE` (JetBrains Mono, 500, `0.34em`
  tracking, size = wordmark size × 0.20, color `inkDim`)
- Vertical gap between wordmark and descriptor = `size * 0.10`

### 04 — Mark only
- For app icons, favicons, dock badges, and standalone branding moments
- Never reproduce the wordmark in isolation without geometric reason

---

## App Icon

A rounded square ("squircle") containing the primary mark.

**Geometry:**
- Corner radius: `Math.round(size * 0.2237)` (Apple HIG squircle)
- Mark inset: `size * 0.18` on all sides (so the mark fills ~64% of the icon)
- Mark fills the inset area; uses full primary + secondary colors

**Dark edition (default):**
- Background: `radial-gradient(ellipse at 30% 20%, #102031, #05070c 60%, #02030a)`
- Inset 1px border: `rgba(94, 234, 212, 0.18)`
- Outer shadow: `0 (size*0.04)px (size*0.12)px rgba(0,0,0,0.5)`
- Subtle ring grid behind the mark (concentric circles at r=50,40,30,20 in
  the 128-unit viewBox, stroke cyan at `opacity: 0.04 + i*0.02`)
- Faint scanlines at `opacity: 0.18`
- Corner ticks (same as panel chrome) at `0.42` opacity cyan, inset 6%, size 5%
- Tiny `v1` version stamp at bottom-right (only for icons ≥ 96px)

**Light edition:**
- Background: `#F3EEE4`
- Inset 1px border: `rgba(0,0,0,0.06)`
- Outer shadow: `0 (size*0.04)px (size*0.12)px rgba(0,0,0,0.18)`
- No glow, no scanlines, no ring grid
- Mark drawn monochromatically in `#15161A`

**Export sizes (PNG):**
- macOS .icns: 1024, 512, 256, 128, 64, 32, 16
- Windows .ico: 256, 128, 64, 48, 32, 24, 16
- For sizes ≤ 16, replace the full mark with the **monogram** (no dendrite)

---

## Favicon

| Size  | Treatment                            |
| ----- | ------------------------------------ |
| 64px  | Full mark with glow                  |
| 48px  | Full mark with glow                  |
| 32px  | Full mark, glow on                   |
| 24px  | Monogram only (drops the dendrite)   |
| 16px  | Monogram only, stroke 3px            |

The favicon sits directly on the page background (no rounded square container).

---

## Iconography

30 icons, all drawn on a **24 × 24 viewBox** with **1.5px stroke**, round
caps, round joins, no fill (except deliberately "lit" elements like dots
or solid arrows). Color inherits from `currentColor` and is overridden per
context.

### Status icons (8) — color matches semantic palette
| Name      | Color (default) | Meaning                            |
| --------- | --------------- | ---------------------------------- |
| idle      | `inkDim`        | No active job                      |
| scan      | `yellow`        | Scanning disc                      |
| ripping   | `magenta`       | Currently ripping a track          |
| matching  | `amber`         | Identifying titles via TMDB        |
| complete  | `green`         | Successfully archived              |
| paused    | `cyan`          | User-paused                        |
| queued    | `inkDim`        | Waiting (dashed circle)            |
| error     | `red`           | Failed                             |

### Media-type icons (8) — `cyan` by default
`disc, blu-ray (with "BD" text), dvd (with "DVD" text), tv, movie, episode, drive, library`

### Action + navigation icons (14) — `cyan` on hover, `inkDim` at rest
`dashboard, history, review, settings, search, filter, play, pause, cancel, retry, eject, more, confidence, bytes`

### Implementation notes
- Export each icon as a standalone React component (e.g.
  `<IconRipping size={20} />`) — see `brand/icons.jsx` for the
  exact path data
- Wrap with a consistent `<Icon>` base that accepts `size`, `color`,
  `title`, and applies the standard `stroke="1.5"` etc.
- Optional `glow` prop adds `filter: drop-shadow(0 0 6px <color>aa)` —
  used in active states

---

## Applications

### Splash screen
- Full viewport `#05070c` background with full atmosphere stack
- Center: animated mark at **140px**, rotating slowly (`14s` linear infinite),
  node pulsing (opacity 1→0.3→1 over `1.2s`), with a 6.5px → 10px → 6.5px
  radius pulse on `1.6s`
- Below: wordmark at 48–56px
- Below that: mono label `INITIALIZING...` in cyan, `0.32em` tracking, with
  the `...` blinking at 1s
- Bottom-left / bottom-right corners: small mono captions
  (`ENGRAM · MEDIA ARCHIVE` / `v1.0.0 · BUILD YYYY.MM`)

### Dock notification states
Five states applied via a small badge in the top-right of the app icon:

| State       | Badge                                                          |
| ----------- | -------------------------------------------------------------- |
| `idle`      | None                                                           |
| `active`    | 18px magenta dot, glowing, blinking (1s)                       |
| `count:N`   | Min 24px magenta pill with bone numeral (mono, 700)            |
| `complete`  | 24px green dot with bone ✓                                     |
| `error`     | 24px red dot with bone !                                       |

All badges have a 2px border in the surface color (`#05070c`) so they
"cut out" cleanly against the dock.

### Terminal banner
ASCII-art rendering of "ENGRAM" using box-drawing block characters
(`▓ ▒ ░ █`). Rendered in JetBrains Mono, weight 600, color cyan
(`#5EEAD4`) with a text-shadow of `0 0 6px #5EEAD488` for the phosphor
glow. Used in CLI startup and the about screen.

---

## Screenshots

Reference PNGs of every artboard in the brand sheet live in `screenshots/`.
The HTML files in this bundle are the authoritative source; the PNGs exist
as a quick visual index when reading the README offline.

### Identity
- `screenshots/01-hero.png` — Cover lockup with descriptor and palette swatches
- `screenshots/02-mark-hero.png` — Primary mark, large
- `screenshots/03-wordmark.png` — Wordmark scale ladder
- `screenshots/04-lockups.png` — Horizontal / stacked / with-descriptor / mark-only
- `screenshots/05-paper-edition.png` — Light edition on paper background

### System
- `screenshots/06-construction.png` — Mark geometry on the 64-unit grid
- `screenshots/07-clear-space.png` — Clear-space rule + minimum sizes
- `screenshots/08-color.png` — 10 color tokens with hex + role
- `screenshots/09-type.png` — Chakra Petch + JetBrains Mono pairing

### Applications
- `screenshots/10-app-icons.png` — macOS app icon family
- `screenshots/11-favicon.png` — Favicon scale + browser tab mockup
- `screenshots/12-splash.png` — Application splash screen
- `screenshots/13-dock.png` — Dock notification states
- `screenshots/14-terminal.png` — ASCII CLI banner

### Iconography
- `screenshots/15-icons-status.png` — 8 status icons + badge usage
- `screenshots/16-icons-media.png` — 8 media-type icons + type badges
- `screenshots/17-icons-action.png` — 14 action + nav icons + tabbar mock
- `screenshots/18-in-use.png` — Full job card showing all systems together

---

## Files

| File                                | Purpose                                    |
| ----------------------------------- | ------------------------------------------ |
| `Engram Brand.html`                 | Entry point — open in a browser to view    |
| `design-canvas.jsx`                 | The pan/zoom canvas wrapping all artboards |
| `brand/tokens.jsx`                  | Color, type, line tokens; atmosphere wrapper |
| `brand/marks.jsx`                   | Mark, Wordmark, Lockups, AppIcon, MarkAnimated |
| `brand/icons.jsx`                   | All 30 system icons + Ico base             |
| `brand/brand-identity.jsx`          | Artboards 01–05 (identity) + 06–09 (system) |
| `brand/brand-applications.jsx`     | Artboards 10–14 (app icon, favicon, splash, dock, terminal) |
| `brand/brand-icons.jsx`             | Artboards 15–18 (icon system + in-use)     |
| `brand/brand-app.jsx`               | Wires every artboard onto the DesignCanvas |

To preview locally: open `Engram Brand.html` in a modern browser. Each
artboard is independently focusable from the canvas — click the expand
button on any card.

---

## Implementation checklist

1. **Set up brand tokens** in your codebase (CSS variables, theme object,
   or design-token JSON) using the table above
2. **Embed the wordmark + mark fonts**: Chakra Petch (400, 500, 600, 700)
   and JetBrains Mono (300, 400, 500, 600, 700) — self-host or load from
   Google Fonts depending on your shipping constraints
3. **Build `<Mark>`, `<MarkMono>`, `<Wordmark>`, and the four `<Lockup*>`
   components** as standalone SVG React components — see `brand/marks.jsx`
   for exact path data
4. **Export the icon set** as React components or as an SVG sprite — see
   `brand/icons.jsx`
5. **Generate app icon raster assets** (PNG at every required size) from
   the dark and light SVG sources. Use macOS' `iconutil` to produce `.icns`
   and ImageMagick/`png2ico` to produce `.ico`
6. **Build the splash + dock badge components** for the Electron / Tauri
   shell using the animation specs above
7. **Build a reusable `<Panel>` primitive** with corner ticks and the
   line-token border — most product UI builds on top of this

---

## Notes for the implementer

- **Resist over-glow.** The atmospheric effects (scanlines, vignette,
  grain) are deliberately subtle. Aggressive scanlines + heavy bloom on
  every surface will tip from "equipment" into "fake retro." Use the
  atmosphere stack on splash, hero / dashboard background, and major
  modal backdrops — not on every panel.
- **Mono is for labels, not body.** Don't render running paragraphs in
  JetBrains Mono. Mono is reserved for telemetry, paths, labels, code,
  and numeric callouts.
- **The wordmark stays uppercase, always.** Title case is reserved for
  body / headings.
- **Cyan is brand. Magenta is action.** Magenta only appears on actively
  ripping things, on the primary CTA in any flow, and on the read-line
  node of the mark. Don't decorate static chrome with magenta.

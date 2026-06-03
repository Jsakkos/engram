# Brand System — Developer Reference

The canonical visual spec is [`docs/design_handoff_brand/README.md`](../design_handoff_brand/README.md).
That document is the authority. This page is the **developer-facing
implementation guide**: where each primitive lives, how to consume the
design tokens, and the small rules that keep the brand from drifting.

## Quick map

| You want to render… | Use… | Lives in… |
| --- | --- | --- |
| The full mark (large hero / dashboard) | `<SvMark size={n} />` | [`SvMark.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/SvMark.tsx) |
| The monogram (no read-line, ≤32px) | `<MarkMono size={n} />` | [`MarkMono.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/MarkMono.tsx) |
| An animated mark (splash, reconnect) | `<MarkAnimated size={n} />` | [`MarkAnimated.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/MarkAnimated.tsx) |
| The wordmark "ENGRAM" | `<Wordmark size={n} />` | [`Wordmark.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/Wordmark.tsx) |
| One of four lockups (mark + wordmark) | `<LockupHorizontal>` / `<LockupStacked>` / `<LockupWithDescriptor>` / `<LockupMarkOnly>` | [`Lockup.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/Lockup.tsx) |
| A platform-style app icon | `<AppIcon size={128} edition="dark" />` | [`AppIcon.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/AppIcon.tsx) |
| Full-viewport splash | `<Splash label="INITIALIZING" />` | [`Splash.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/Splash.tsx) |
| A bordered panel with corner ticks | `<SvPanel>` (auto-wraps `<SvCorners>`) | [`SvPanel.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/SvPanel.tsx) |
| A status icon (idle / scan / ripping / …) | `<IcoIdle />` … `<IcoError />` | [`icons/status.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/status.tsx) |
| A media-type icon (disc / movie / TV / …) | `<IcoDisc />` … `<IcoLibrary />` | [`icons/media.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/media.tsx) |
| An action / nav icon (play / settings / …) | `<IcoPlay />` … `<IcoBytes />` | [`icons/action.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/action.tsx) |

Re-exports are aggregated in
[`frontend/src/app/components/synapse/index.ts`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/index.ts)
and [`frontend/src/app/components/icons/index.ts`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/index.ts) — import from there.

## Design tokens

There are **two places** the tokens live, kept in lockstep:

1. **TypeScript** — [`frontend/src/app/components/synapse/tokens.ts`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/tokens.ts)
   exports `sv`, a plain object with color/typography constants. Use it
   for inline `style={{ … }}`, SVG `fill="…"`, or wherever a CSS variable
   doesn't reach (motion props, conditional gradients, etc.).

2. **CSS custom properties** — [`frontend/src/styles/theme.css`](https://github.com/Jsakkos/engram/blob/main/frontend/src/styles/theme.css)
   `@theme inline { … }` block defines `--color-sv-cyan`, `--color-sv-magenta`,
   `--color-sv-line-mid`, and so on. Use these from `.css` files and
   external stylesheets like [`ConfigWizard.css`](https://github.com/Jsakkos/engram/blob/main/frontend/src/components/ConfigWizard.css).

**Never hardcode `#5eead4` or `#ff3d7f` in new code** — they always come
from one of the two surfaces above. If you find yourself wanting a new
hex, add it to both files in the same commit.

## The hard rules (from the handoff)

These are the rules you'll forget and accidentally break. Memorize them.

- **Cyan is brand. Magenta is action.** Magenta only appears on actively
  ripping things, on the primary CTA in any flow, and on the read-line
  node of the mark. Don't decorate static chrome with magenta. (When
  refactoring [`ConfigWizard.css`](https://github.com/Jsakkos/engram/blob/main/frontend/src/components/ConfigWizard.css),
  this is why the form `<label>` color was moved from magenta to cyan-dim.)
- **Mono is for labels, not body.** Don't render running paragraphs in
  JetBrains Mono. Mono is for telemetry, paths, labels, code, and
  numeric callouts. Body text uses Chakra Petch (`sv.sans`/`sv.display`).
- **The wordmark stays uppercase, always.** Title case is reserved for
  body and headings.
- **Resist over-glow.** The atmosphere stack (scanlines, vignette, grain)
  is applied at the app root via `<SvAtmosphere>`. Do not re-apply
  scanlines or heavy bloom on every panel. Reserve it for splash, hero /
  dashboard background, and major modal backdrops.
- **Sharp 90° corners everywhere.** No `border-radius`. The "squircle"
  rounding is only for `<AppIcon>` (Apple HIG `size * 0.2237`).

## Icons: when to use the Engram set vs. Lucide

The 30-icon Engram set covers **brand-meaningful** glyphs: state
indicators, media types, primary navigation, action callouts. The
existing codebase still uses [`lucide-react`](https://lucide.dev) for
**utility primitives** that don't carry brand meaning — chevrons, the
plus sign, save, trash, info, X-for-close (not the brand "cancel"
glyph), and so on.

Rule of thumb:

| Glyph reads as… | Use |
| --- | --- |
| A state, a media type, a primary action, or a nav entry | `Ico*` from [`icons/`](https://github.com/Jsakkos/engram/tree/main/frontend/src/app/components/icons/) |
| A pure UI affordance (chevron, plus, save, trash, info) | Lucide |

If you can describe the icon in the brand handoff's vocabulary
("ripping", "matching", "library", "search"), it's brand-set. If you'd
describe it as a UI primitive ("collapse this section", "delete row"),
it's Lucide.

## Adding a new icon

1. Confirm it's not already in the 30-icon set —
   [`icons/index.ts`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/index.ts) is the full inventory.
2. If it's a true brand glyph, draft it on a 24×24 grid with 1.5px stroke,
   round caps + joins, no fill (except deliberately "lit" elements).
3. Add the path data to the appropriate file ([`status.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/status.tsx)
   / [`media.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/media.tsx) /
   [`action.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/action.tsx)) wrapped in `<Ico>`.
4. Re-export from [`icons/index.ts`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/icons/index.ts).
5. If the new icon is brand-significant enough to ship as an external
   asset, add it to the canonical handoff
   [`docs/design_handoff_brand/brand/icons.jsx`](../design_handoff_brand/brand/icons.jsx) so
   future implementations stay in sync.

## Generating raster assets

The pipeline is:

```bash
cd frontend
npm run brand:export
```

This runs two scripts in sequence:

1. [`scripts/render-svg-sources.mjs`](https://github.com/Jsakkos/engram/blob/main/frontend/scripts/render-svg-sources.mjs)
   emits canonical SVG sources to `frontend/public/brand/sources/`:
   `mark.svg`, `mark-mono.svg`, `app-icon-dark.svg`, `app-icon-light.svg`.
2. [`scripts/generate-brand-assets.mjs`](https://github.com/Jsakkos/engram/blob/main/frontend/scripts/generate-brand-assets.mjs)
   rasterizes with `sharp` and packs `.ico` and `.icns` with `png2icons`.

Outputs land under `frontend/public/brand/` in a structured layout:

```
brand/
  sources/        SVG sources (one per artwork)
  favicons/       16/24/32/48/64 PNG + .svg + multi-resolution .ico
  app-icons/
    windows/      engram.ico (16…256)
    macos/        engram.icns (16…1024) + iconset/ loose PNGs
    linux/        engram-32.png … engram-256.png
  manifest.json   index of every emitted artifact + timestamp
```

The pipeline is **cross-platform** — `png2icons` is pure JS and writes
both `.ico` and `.icns` on Windows/macOS/Linux dev machines. No
`iconutil` or ImageMagick required.

When the mark geometry changes (`SvMark.tsx`), mirror the change in
[`render-svg-sources.mjs`](https://github.com/Jsakkos/engram/blob/main/frontend/scripts/render-svg-sources.mjs) and
re-run `npm run brand:export`. Commit the regenerated assets.

For PyInstaller-frozen Windows builds: point `--icon` at
`frontend/public/brand/app-icons/windows/engram.ico` in the build spec.

## Splash and pre-React paint

There are two splashes, by design:

- **HTML pre-React splash** in [`frontend/index.html`](https://github.com/Jsakkos/engram/blob/main/frontend/index.html).
  Renders before the bundle parses — uses only inline CSS + an inline
  SVG of the mark, no external fonts (system fallback until Chakra Petch
  loads). [`main.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/main.tsx) removes
  `<html class="pre-splash">` once React mounts; the splash fades over
  240ms via a CSS transition and stays in the DOM with `pointer-events:
  none`.
- **React `<Splash />`** in [`Splash.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/app/components/synapse/Splash.tsx).
  Use this when you need to surface a "connecting" or "reconnecting"
  state in the running app — the WebSocket-disconnected case is the
  canonical example.

Keep them visually consistent (same mark, same wordmark, same
"INITIALIZING…" / "RECONNECTING…" label).

## Out of scope (deliberately)

The handoff documents two artifacts that are **not** implemented because
no native desktop shell exists yet:

- **Dock notification badges** (5 states: idle / active / count / complete / error).
  Revisit when Engram ships an Electron / Tauri / Wails shell.
- **Terminal banner** (ASCII-art "ENGRAM" with phosphor glow). The team
  decided this isn't needed for the current CLI / about-screen story.

If either of those becomes useful later, the handoff documents the
exact look and behavior — start there.

## Accepted deviations from the original plan

Two items in the rollout plan ([`.claude/plans/take-a-look-at-deep-clarke.md`](https://github.com/Jsakkos/engram/blob/main/.claude/plans/take-a-look-at-deep-clarke.md))
were scoped down during implementation. Both are deliberate and documented
here so future maintainers don't re-litigate the choice.

### 1. `lucide-react` is kept as a dependency for utility primitives

The plan said *"drop `lucide-react` from package.json once grep comes back
empty."* This was scoped down because the brand handoff's 30-icon set is
explicitly a **brand icon set** — it covers status, media type, primary
nav, and primary actions. It does **not** cover utility primitives like
chevrons (`<ChevronLeft>`, `<ChevronRight>`, `<ChevronDown>` for
collapsibles and pagination), the plus sign (`<Plus>` for "Add"
affordances), `<Save>`, `<Trash2>`, `<Info>`, `<Bug>`, `<Database>`,
`<Clock>`, `<Vote>`, `<Loader2>`, `<X>`-for-close (distinct from the
brand's circled-X `IcoCancel`), etc.

Adding those to the brand set would have:
1. **Exceeded the brand spec** — drawing ~8–10 new icons that aren't in
   the canonical 30. The handoff would then need a v2 update.
2. **Diluted the brand meaning** — having `IcoChevronDown` next to
   `IcoRipping` blurs the "brand icons signal state and action" rule.

The current rule (see [§ Icons: when to use the Engram set vs. Lucide](#icons-when-to-use-the-engram-set-vs-lucide))
is: brand-meaningful glyphs come from `Ico*`; UI primitives stay on
Lucide. If the brand spec is ever extended to cover utility primitives,
revisit and complete the retirement.

### 2. `ConfigWizard.css` survives in tokenized form

The plan said *"delete `ConfigWizard.css` entirely"* and rewrite
[`ConfigWizard.tsx`](https://github.com/Jsakkos/engram/blob/main/frontend/src/components/ConfigWizard.tsx)
to consume `<SvPanel>` / `<SvBadge>` / `<SvLabel>` etc.

The actual refactor stopped one step short of deletion: the `.css`
file (~669 lines, now ~600 after cleanup) was retained but rewritten to
consume CSS custom properties from [`theme.css`](https://github.com/Jsakkos/engram/blob/main/frontend/src/styles/theme.css)
(`--color-sv-cyan`, `--color-sv-line-mid`, etc.) instead of hardcoded
hexes. The visible-from-outside chrome (modal overlay, modal border,
button styling, form labels, scanlines) was brought into brand
alignment — duplicate scanline overlays were removed (handoff: *"resist
over-glow"*), labels moved from magenta to cyan-dim (handoff: *"magenta
is action"*), and the modal frame matches `<SvPanel>` chrome.

Fully deleting the `.css` would have required rewriting nearly all
983 lines of TSX as inline `style` props or extracted Sv primitives —
high-effort, low-marginal-value once the colors and chrome are aligned.
The remaining `.css` is purely layout (`form-group`, `wizard-step`,
spacing) and not brand-bearing. Revisit if/when the ConfigWizard gets a
structural overhaul.

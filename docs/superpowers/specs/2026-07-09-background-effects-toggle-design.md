# Background Effects Toggle — Design

**Date:** 2026-07-09
**Status:** Approved for planning

## Problem

`SvRipAnimation` (`frontend/src/app/components/synapse/SvRipAnimation.tsx`) renders a
full-viewport `<canvas>` "falling code" background whenever a disc is ripping. Per its own
doc comment, this is a ~8k-cell field redrawn every tick via `requestAnimationFrame` at
20fps. On weak hardware — an ARM64 device was the reported case — this is a continuous
CPU/GPU cost with no way to turn it off short of the OS-level `prefers-reduced-motion`
setting (which the component already respects, but which many users won't touch just for
one app, and which also silences other, cheaper motion they may want to keep).

This design adds an explicit, app-level toggle scoped to just this animation.

## Non-goals

- **Not** touching `SvAtmosphere`'s static layers (haze gradient, scanline overlay, SVG
  `feTurbulence` grain filter, vignette). These aren't animated (no RAF loop) and the design
  handoff calls them always-on with no settings toggle in production. Confirmed with the user
  as out of scope — the rip animation's continuous redraw is the actual resource cost.
- **Not** a general "reduce all motion" switch. Framer Motion transitions elsewhere (card
  hover, page transitions) are cheap, one-shot, and stay on. `prefers-reduced-motion`
  remains the mechanism for users who want everything quieted.
- **Not** persisted server-side. See "Storage" below for why.
- **Not** live-synced across open tabs. A second tab picks up a changed preference on its
  next reload.

## Storage: browser localStorage, not backend AppConfig

The constraint this preference addresses is the rendering cost on the *browser* showing the
dashboard, not anything about the backend host. Those can be different machines (e.g. a
backend running on a NAS, viewed from a weak tablet in one browser and a powerful desktop in
another). A server-side `AppConfig` field would apply the same setting to every viewer
regardless of their own hardware, which is the wrong scope. `localStorage`, keyed per
browser/device, is correct here — and it also sidesteps the three-way sync backend settings
require (`ConfigUpdate` / `ConfigResponse` / `ConfigWizard` state), since this field never
touches the backend at all.

## Components

### `useBackgroundEffectsEnabled` hook

New file: `frontend/src/app/hooks/useBackgroundEffectsEnabled.ts`.

```ts
export function useBackgroundEffectsEnabled(): [boolean, (v: boolean) => void]
```

- Storage key: `engram:backgroundEffectsEnabled` (`"true"` / `"false"`), following the
  `engram:` prefix convention already used by `useUpdateSuccessToast`'s
  `engram:lastSuccessToastVersion`.
- Read/write wrapped in try/catch, degrading to in-memory-only state if `localStorage` is
  unavailable (private browsing, storage quota, etc.) — same defensive pattern as
  `useUpdateSuccessToast`.
- **Default when no stored value exists:** seeded from
  `window.matchMedia("(prefers-reduced-motion: reduce)").matches` — `false` (effects off) if
  the OS already asks for reduced motion, `true` (effects on, i.e. unchanged current
  behavior) otherwise. This seed is read once at mount; it does not reactively track the
  media query afterward (unlike `useMediaQuery`), since once a value is stored, the OS
  setting no longer applies — the explicit user choice always wins from then on.
- No `storage` event listener — deliberately simple, per the non-goals above.

### Wiring into `App.tsx`

One-line change at the existing call site, [App.tsx:286](../../../frontend/src/app/App.tsx#L286):

```diff
- <SvAtmosphere ripActive={discsData.some((d) => d.state === "ripping")}>
+ <SvAtmosphere ripActive={backgroundEffectsEnabled && discsData.some((d) => d.state === "ripping")}>
```

No changes inside `SvAtmosphere.tsx` or `SvRipAnimation.tsx`. When `ripActive` is `false`,
`SvAtmosphere` never mounts `<SvRipAnimation>` at all (see its existing
`{ripActive && <SvRipAnimation key="rip" />}` in the `AnimatePresence` block) — so the
canvas element and its RAF loop simply never exist. This is the same mechanism the
component already uses to skip rendering when no disc is ripping; we're just adding a
second condition to the same boolean.

### `BackgroundEffectsSetting` component

New file: `frontend/src/components/BackgroundEffectsSetting.tsx`, self-contained like
`GpuAccelerationSetting.tsx` — it owns `useBackgroundEffectsEnabled()` directly rather than
routing through `ConfigWizard`'s `config` state / `handleInputChange`, because this value
never touches the backend.

Rendered inside a new `<details className="wizard-group">` group titled **"Display"** in the
Preferences step of `ConfigWizard.tsx` (step 5), alongside the existing "Matching &
ordering" and "Maintenance & watchdog" groups. Markup follows the existing checkbox-group
pattern (`form-group checkbox-group` / `checkbox-label` / `checkbox-text` /
`checkbox-hint`) for visual consistency:

- **Label:** "Background Animation"
- **Hint:** explains this is the falling-code effect shown while ripping, and that turning
  it off helps on low-power devices. Mentions it's independent of the OS's reduced-motion
  setting.

## Data flow

```
useBackgroundEffectsEnabled()  (localStorage, read once + seeded from prefers-reduced-motion)
        │
        ├─→ App.tsx: backgroundEffectsEnabled && ripActive → SvAtmosphere → SvRipAnimation
        │
        └─→ BackgroundEffectsSetting (in ConfigWizard Preferences → Display)
                → setEnabled(checked) → writes localStorage → next App.tsx render picks it up
```

`App.tsx` and `BackgroundEffectsSetting` both call the hook independently (both are mounted
under the same React tree while the settings modal is open over the dashboard), so toggling
the checkbox takes effect immediately without a page reload — React state, not just storage,
drives the re-render.

## Error handling

- `localStorage` unavailable or throwing: hook degrades to in-memory state (default applies
  every session, toggle still works for that session, nothing crashes). Matches existing
  precedent.
- `window.matchMedia` unavailable (SSR/jsdom): default seed falls back to `true` (effects
  on), matching `useMediaQuery`'s documented fallback convention for inverted queries.

## Testing

- **Unit (Vitest):** `useBackgroundEffectsEnabled` — default seeding from
  `prefers-reduced-motion` both ways, persistence round-trip, `localStorage`-unavailable
  fallback.
- **Unit (Vitest):** `App.tsx` — with the hook mocked/stubbed off, a ripping job present does
  *not* render `[data-testid="sv-rip-animation"]`; with it on (default), it does. Existing
  `App.test.tsx` already stubs `matchMedia` for this component, so this extends existing
  coverage rather than adding new scaffolding.
- **Unit (Vitest):** `ConfigWizard` — toggling the new checkbox calls `setEnabled` with the
  right value; verify it does *not* trigger the generic config save network call.
- **Manual:** open dashboard, simulate a ripping disc, confirm animation renders; toggle off
  in Settings → Preferences → Display, confirm it stops immediately without reload; reload
  the page, confirm the setting persisted.

# UI/UX Review — Full Surface Audit

**Date:** 2026-06-10
**Method:** Live walkthrough of v0.18.0 (worktree dev build, backend `--port 8100` DEBUG=true, simulated jobs in all states) + code cross-check against the Synapse v2 brand handoff (`docs/development/brand.md`). Screenshots in `assets/2026-06-10-uiux-*.png`.

Scope: design-system consistency, element usability, polish, onboarding wizard, settings structure, screen real estate, transitions/animations.

---

## Critical findings (fix first)

### C1. Synapse CSS tokens never reach the DOM — 51 `var(--color-sv-*)` references silently broken

`theme.css` declares the entire Synapse token set inside `@theme inline { … }`. In Tailwind v4, `inline` substitutes values into generated utilities **without emitting the custom properties to `:root`**. Verified at runtime: `getComputedStyle(document.documentElement).getPropertyValue('--color-sv-bg1')` → empty.

Every `var(--color-sv-*)` in hand-written CSS resolves to nothing — 48 references in `ConfigWizard.css`, 3 in `ConfigWizard.tsx`. CSS treats undefined `var()` as invalid-at-computed-value-time, so:

- `background: var(--color-sv-bg1)` → **transparent** (cause of C2)
- `border: 1px solid var(--color-sv-line-hi)` → border-color falls back to **`currentColor`** — the settings modal frame renders ink-white instead of the brand cyan line (verified: computed border `rgb(230,236,245)`)

**Fix:** emit the sv tokens as real custom properties (move them to a plain `:root {}` block or non-inline `@theme`), keeping the `@theme inline` aliases if utilities need them. One commit fixes all 51 references.

### C2. Dropdown menus are transparent — options unreadable over page text

`assets/2026-06-10-uiux-05-dropdown-transparent-BUG.png`

The exact bug hypothesized in the review request. `EngramSelect` (Radix Select, used for Conflict Resolution / Episode Ordering / Cleanup Policy / Watchdog in wizard+settings) styles its popover with `.sv-select-content { background: var(--color-sv-bg1) }` — which is broken per C1. Computed listbox background: `rgba(0,0,0,0)`; options add only a 9% cyan tint. Underlapping wizard text bleeds straight through the open menu.

Note: the Review Inspector's episode picker and History filters use **native `<select>`** (OS-rendered, opaque) so they're unaffected — but that's two different select systems (see M6).

### C3. Escape / backdrop-click on the "Identify Disc" modal cancels the job

`App.tsx` wires `NamePromptModal.onCancel` → `cancelJob(...)`, and the modal fires `onCancel` from the **Escape key**, the **backdrop click**, and the Cancel button alike. Reproduced live: a `review_needed` job went to `failed` ("Cancelled by user") from a single Escape press. A misclick outside the modal destroys the job the same way.

Compare `ReIdentifyModal`, where `onCancel` just closes (`setReIdentifyTarget(null)`). Dismissal should park the job (it remains in review/awaiting-name and the card still offers recovery); job cancellation should be an explicit, confirmed action only.

### C4. React duplicate-key bug in top nav

`SvTopBar.tsx:89` keys nav tabs by `item.to`. With zero pending reviews, the REVIEW tab's `to` is `/` — colliding with DASHBOARD. Console fires "two children with the same key" on every render. UX side-effect: clicking REVIEW with no reviews silently lands on the Dashboard. Key by `item.label`; give the empty-review state a real destination (or disable the tab with a tooltip).

---

## Major findings

### M1. Settings is the Setup Wizard wearing a different button

The gear opens the same 5-step linear wizard, still titled "SETUP WIZARD", starting at step 1 (Paths). Step headers are clickable in settings mode (good), but nothing tells the user that; finding one preference means knowing which step + collapsible hides it. The ASR badge makes this concrete (M2). Recommend: retitle to "Settings", show a section list (sidebar nav) instead of a stepper when `isConfigured`, and deep-link sections.

### M2. "GPU available →" badge drops users at Library Paths

`AsrStatusBadge` tooltip says "Click to enable GPU acceleration in Settings" — the click opens settings at **step 1, Paths**. The GPU toggle lives at step 5 → "Matching & ordering" → GPU Acceleration. Dead-end affordance; needs a deep-link to the GPU section.

### M3. TMDB warning repeated up to N+1 times on one screen

The global banner ("TMDB not configured — classification is running in heuristic-only mode. [Configure token]") AND an identical per-card alert on **every** job card render simultaneously — 4 copies with 3 cards visible. Suppress the per-card alert when the global banner is visible, or show per-card only where classification actually degraded the outcome. (Also: the banner's "Configure token" button has a stray trailing period outside the button text.)

### M4. Screen real estate at desktop widths is heavily underused; ≤960px breaks

- 1920×1080, expanded view: one narrow centered card column + side rail ≈ 60% of width; the rest is atmosphere background (`assets/...-07-dashboard-all.png`).
- Review page is worse: title list + small inspector hug center-left; the right half of the viewport is empty (`assets/...-08-review-queue.png`). The Inspector — the page's main workspace — gets the least room.
- Compact view drops the side rail but keeps the same narrow column (`assets/...-11-compact-view.png`).
- At 960px (half a 1920 monitor, a realistic snapped window) the side rail does NOT collapse and crushes cards — the "Dark Knight 2008" title truncates to "D…" (`assets/...-13-responsive-960-broken.png`).
- 1366×768 is fine.

Recommend a wider max-width with the saved space given to the track grid / inspector, and a side-rail collapse breakpoint (~1100px).

### M5. Compact view leaks raw enums and lacks the obvious action

Rows show `review_needed`, `tv`, `ripping` verbatim (expanded cards format these as "REVIEW NEEDED" etc.). A review-needed row's only action is **Cancel** — no "Review" link; rows aren't clickable. Progress/ETA show "—" where the expanded card shows a progress bar.

### M6. Two select systems, three modal systems

- Selects: Radix `EngramSelect` (wizard/settings) vs native `<select>` (Review Inspector, History filters). Different chrome, different keyboard behavior, and only one of them is broken (C2) — which makes the inconsistency visible.
- Modals: NamePromptModal/ReIdentifyModal (inline-styled framer-motion, Escape closes) vs ConfigWizard (CSS-file styled, **Escape does nothing**) vs HistoryPage slide-out (own pattern, deep-linkable — the best of the three). Escape behavior differing per modal is the usability cost; C3 makes one of them destructive.

### M7. Leftover debug logging floods the console

1,328 `console.log` calls in one short session (`🔄 fetchJobsAndTitles merge…`, `📡 WebSocket title_update…`) from `useJobManagement.ts`. Ships to end users in frozen builds; also suggests the full job refetch runs per WS event (80 `fetchJobsAndTitles called` in minutes) — worth a perf look.

---

## Polish list

| # | Where | Issue |
|---|-------|-------|
| P1 | Wizard, all steps | Body paragraphs render in JetBrains Mono — brand rule is "mono is for labels, not body" (body = Chakra Petch). Verified via computed styles. The Import Watch Folder step-1 help is a dense mono essay; collapse it behind a "Supported layouts" disclosure. |
| P2 | Wizard step 1 | NEXT sits bottom-left on step 1 (no BACK to push it right), bottom-right on steps 2+. Pin primary action right. |
| P3 | Wizard step 2 | Raw internals leak: "MakeMKV (version probe timed out)", full FFmpeg banner with copyright line. Truncate to clean version or "Detected ✓". Same strings leak into the Bug Report modal. |
| P4 | Wizard step 3 | "Test Token" is text-sized and barely reads as a button. Links (TMDB API Settings, opensubtitles.com) have no visible link styling. TMDB token input is `type=text` while OpenSubtitles key is `type=password` — pick one secret treatment. |
| P5 | Wizard step 4 | Copy says "Nothing is on by default except local fingerprint extraction" while "Contribute audio fingerprints" is checked by default (gated by the not-yet-accepted disclosure — the checkbox + "Disclosure not accepted" badge send mixed signals). |
| P6 | Collapsible groups | ~~`▸` arrow doesn't rotate when expanded~~ **Withdrawn on implementation**: `.wizard-group[open] .wizard-group-chevron` already rotates 90° via CSS transform — the finding came from the accessibility snapshot's literal `▸` text, which doesn't reflect visual rotation. |
| P7 | Footer status bar | Fake hardcoded telemetry ("UNIT 07 · BUFFER NOMINAL · THERMAL NOMINAL · CPU IDLE · GPU IDLE", `SvStatusBar.tsx:54`). "CPU IDLE" during a 100%-CPU ASR run is actively misleading; wire to real signals or drop. |
| P8 | StateIndicator | ORGANIZING state reuses the "Matching" icon (img alt "Matching" next to "ORGANIZING" text). |
| P9 | History | "Cancelled by user" listed under "Common errors"; stat tiles count active jobs while the table shows only terminal ones. Subtitle status "partial / 0 of 0" in job detail should read "none needed". |
| P10 | Brand drift | Magenta used decoratively: MOVIE type tags + TV-seasons distribution bar (History), media-type toggle selection (modals). Handoff: magenta = ripping, primary CTA, read-line node only. Cancel buttons in NamePrompt/ReIdentify modals are magenta-outlined and visually louder than the primary action. |
| P11 | Contrast | 128 usages of `ink-faint` (#4a5369, ≈2.5:1 on bg1) and `ink-ghost` across 30 files — below WCAG AA even for large text. Fine for pure decoration; audit the cases carrying real info (helper text, timestamps, footer). |
| P12 | First-run | With a disc in the drive, the pipeline auto-starts a real rip into the **default, unconfirmed** staging path while the setup wizard is still on screen. Gate auto-rip on `is_configured` or prompt. |
| P13 | Identify Disc modal | Auto-opens over the dashboard the moment a label-unreadable job appears, stealing focus from whatever the user was doing (combined with C3, an absent-minded Escape then kills the job). Consider a non-modal affordance (card CTA + nav badge already exist). |

## What's working well

- **Onboarding**: auto-detection of MakeMKV/FFmpeg with re-scan, pre-filled sane paths, v3-vs-v4 TMDB token education, soft-gate "Continue without TMDB" confirmation, privacy-honest Data Sharing step, Configuration Summary at the end. Structure is genuinely good — the issues are surface-level (C2, P1–P5).
- **History page**: stat tiles, common-error rollup, filterable table, deep-linkable slide-out detail (timeline, classification + review reason, per-track list, paths) — best-in-app information design.
- **Live dashboard**: card → track-grid → side-rail activity log during an active rip/match feels alive and informative; WS-driven updates are instant; per-track provenance chips are a nice touch.
- **Motion hygiene**: `MotionConfig reducedMotion="user"` at the root + `prefers-reduced-motion` CSS + per-component `useReducedMotion` — animations are subtle (no layout thrash observed) and properly degradable. No changes needed.
- **Bug report modal**: sanitized env/paths/log-tail + "Open GitHub Issue" — excellent support loop.

## Suggested order of attack

1. C1 (token emission) — unblocks C2 and un-breaks all ConfigWizard chrome in one commit.
2. C3 + P13 (non-destructive dismissal) — data-loss-grade usability.
3. C4 (nav key + empty-review destination).
4. M3 (banner dedup), M5 (compact view), P2/P3/P6/P8 — cheap wins.
5. M1 + M2 (settings restructure + deep-links) — the one structural project.
6. M4 (layout width / breakpoint) alongside M1.

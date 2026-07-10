# Background Effects Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings toggle that disables the resource-hungry falling-code rip animation (`SvRipAnimation`), so low-power devices (the reported case: ARM64) can turn it off independently of the OS's reduced-motion setting.

**Architecture:** A new localStorage-backed React hook (`useBackgroundEffectsEnabled`) is the single source of truth. `App.tsx` reads it to gate whether `SvRipAnimation` ever mounts (one boolean `&&`'d onto the existing `ripActive` expression — no changes inside `SvAtmosphere`/`SvRipAnimation` themselves). A new self-contained settings component (`BackgroundEffectsSetting`, mirroring the existing `GpuAccelerationSetting` pattern) reads/writes the same hook and is dropped into a new "Display" group in `ConfigWizard`'s Preferences step. The preference never touches the backend — it describes the browser rendering the dashboard, not the backend host, so it deliberately bypasses `AppConfig`/`ConfigUpdate`/`ConfigResponse`.

**Tech Stack:** React 18 + TypeScript, Vitest + React Testing Library (`frontend/src/**/__tests__` and colocated `*.test.tsx`), existing `ConfigWizard.tsx` checkbox-group markup/CSS (no new CSS).

**Reference:** Full design rationale in [docs/superpowers/specs/2026-07-09-background-effects-toggle-design.md](../specs/2026-07-09-background-effects-toggle-design.md).

---

### Task 1: `useBackgroundEffectsEnabled` hook

**Files:**
- Create: `frontend/src/app/hooks/useBackgroundEffectsEnabled.ts`
- Test: `frontend/src/app/hooks/__tests__/useBackgroundEffectsEnabled.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/app/hooks/__tests__/useBackgroundEffectsEnabled.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackgroundEffectsEnabled } from "../useBackgroundEffectsEnabled";

function stubMatchMedia(reducedMotion: boolean) {
    vi.stubGlobal(
        "matchMedia",
        vi.fn().mockImplementation((query: string) => ({
            matches: reducedMotion,
            media: query,
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(),
        })),
    );
}

describe("useBackgroundEffectsEnabled", () => {
    beforeEach(() => {
        localStorage.clear();
        stubMatchMedia(false);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it("defaults to enabled when there is no stored preference and the OS has no reduced-motion request", () => {
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("defaults to disabled when the OS requests reduced motion and there is no stored preference", () => {
        stubMatchMedia(true);
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(false);
    });

    it("persists an explicit choice to localStorage and a fresh mount reflects it", () => {
        const { result, unmount } = renderHook(() => useBackgroundEffectsEnabled());
        act(() => result.current[1](false));
        expect(result.current[0]).toBe(false);
        expect(localStorage.getItem("engram:backgroundEffectsEnabled")).toBe("false");
        unmount();

        const { result: second } = renderHook(() => useBackgroundEffectsEnabled());
        expect(second.current[0]).toBe(false);
    });

    it("an explicit stored choice overrides the reduced-motion default", () => {
        localStorage.setItem("engram:backgroundEffectsEnabled", "true");
        stubMatchMedia(true); // OS says reduce motion, but the user already chose "on"
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("falls back to enabled when matchMedia is unavailable (matches useMediaQuery's documented fallback)", () => {
        vi.stubGlobal("matchMedia", undefined);
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("degrades to in-memory state when localStorage throws", () => {
        const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        const setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("blocked");
        });

        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true); // falls back to the reduced-motion-based default

        expect(() => act(() => result.current[1](false))).not.toThrow();
        expect(result.current[0]).toBe(false); // in-memory state still updates

        getItemSpy.mockRestore();
        setItemSpy.mockRestore();
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useBackgroundEffectsEnabled.test.ts`
Expected: FAIL — `Cannot find module '../useBackgroundEffectsEnabled'` (the hook doesn't exist yet).

- [ ] **Step 3: Write the hook**

Create `frontend/src/app/hooks/useBackgroundEffectsEnabled.ts`:

```ts
import { useCallback, useState } from "react";

const STORAGE_KEY = "engram:backgroundEffectsEnabled";

function readStoredPreference(): boolean | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "true") return true;
    if (raw === "false") return false;
    return null;
  } catch {
    return null;
  }
}

function prefersReducedMotion(): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch {
    return false;
  }
}

/**
 * Whether ambient background effects (currently: the falling-code rip
 * animation in SvRipAnimation) should render. Persisted per-browser in
 * localStorage — this describes the device viewing the dashboard, not the
 * backend host, so it deliberately does not go through AppConfig.
 *
 * With no stored preference yet, seeds from `prefers-reduced-motion` (off if
 * the OS already asks for reduced motion). Once the user makes an explicit
 * choice, that choice always wins over the OS setting.
 */
export function useBackgroundEffectsEnabled(): [boolean, (enabled: boolean) => void] {
  const [enabled, setEnabledState] = useState<boolean>(() => {
    const stored = readStoredPreference();
    if (stored !== null) return stored;
    return !prefersReducedMotion();
  });

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next);
    try {
      localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // localStorage unavailable — preference stays in-memory for this session
    }
  }, []);

  return [enabled, setEnabled];
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useBackgroundEffectsEnabled.test.ts`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/hooks/useBackgroundEffectsEnabled.ts frontend/src/app/hooks/__tests__/useBackgroundEffectsEnabled.test.ts
git commit -m "feat: add useBackgroundEffectsEnabled hook"
```

---

### Task 2: Wire the preference into `App.tsx`

**Files:**
- Modify: `frontend/src/app/App.tsx:1-40` (imports), `frontend/src/app/App.tsx:258-259` (hook call), `frontend/src/app/App.tsx:286` (gate expression)
- Test: `frontend/src/app/App.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/app/App.test.tsx`, add `localStorage.clear();` as the first line of the existing top-level `beforeEach` (the one that stubs `matchMedia` and `fetch`, currently starting at line 78) so this task's tests don't leak state into others. Then add a new `describe` block anywhere after the existing ones (e.g. at the end of the file):

```tsx
describe('App — background effects preference', () => {
    it('renders the rip animation while ripping when the preference is on (default)', async () => {
        mockJobs([makeJob({ id: 1, state: 'ripping', volume_label: 'INCEPTION_2010', content_type: 'movie' })]);
        renderApp();

        expect(await screen.findByTestId('sv-rip-animation')).toBeInTheDocument();
    });

    it('does not render the rip animation while ripping when the preference is off', async () => {
        localStorage.setItem('engram:backgroundEffectsEnabled', 'false');
        mockJobs([makeJob({ id: 1, state: 'ripping', volume_label: 'INCEPTION_2010', content_type: 'movie' })]);
        renderApp();

        await screen.findByTestId('sv-atmosphere');
        expect(screen.queryByTestId('sv-rip-animation')).not.toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/app/App.test.tsx -t "background effects preference"`
Expected: FAIL on the second test — the animation renders regardless of the (not-yet-read) preference, so `sv-rip-animation` is still present when the preference is set to `'false'`.

- [ ] **Step 3: Wire the hook into `App.tsx`**

Add the import alongside the other hook imports (`frontend/src/app/App.tsx:9-11`):

```diff
 import { useMediaQuery } from "./hooks/useMediaQuery";
 import { useNotifications } from "./hooks/useNotifications";
 import { useUpdateSuccessToast } from "./hooks/useUpdateSuccessToast";
+import { useBackgroundEffectsEnabled } from "./hooks/useBackgroundEffectsEnabled";
```

Call the hook right after `railFits` (`frontend/src/app/App.tsx:258-259`):

```diff
   const railFits = useMediaQuery("(min-width: 1100px)");
   const showSideRail = filteredDiscs.length > 0 && viewMode === "expanded" && railFits;
+  const [backgroundEffectsEnabled] = useBackgroundEffectsEnabled();
```

Fold it into the existing `ripActive` expression (`frontend/src/app/App.tsx:286`):

```diff
-    <SvAtmosphere ripActive={discsData.some((d) => d.state === "ripping")}>
+    <SvAtmosphere ripActive={backgroundEffectsEnabled && discsData.some((d) => d.state === "ripping")}>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/app/App.test.tsx`
Expected: PASS (full file — confirms this change didn't regress the existing prompt-surfacing / walk-away tests in the same file)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/App.tsx frontend/src/app/App.test.tsx
git commit -m "feat: gate the rip animation on the background-effects preference"
```

---

### Task 3: Settings UI — `BackgroundEffectsSetting` in the Preferences step

**Files:**
- Create: `frontend/src/components/BackgroundEffectsSetting.tsx`
- Modify: `frontend/src/components/ConfigWizard.tsx:8` (import), `frontend/src/components/ConfigWizard.tsx:1716-1718` (new "Display" group)
- Test: `frontend/src/components/ConfigWizard.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/ConfigWizard.test.tsx`, add `type Mock` to the vitest import (line 3):

```diff
-import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
+import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
```

Add `localStorage.clear();` as the first line of the existing top-level `beforeEach` (starting at line 12) so this task's tests don't leak into others. Then add a new `describe` block at the end of the file:

```tsx
describe('ConfigWizard — background effects preference', () => {
    it('shows a Background Animation checkbox in Preferences, on by default, and persists a toggle to localStorage without an API call', async () => {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        const nav = await screen.findByRole('navigation', { name: /settings sections/i });
        fireEvent.click(within(nav).getByRole('button', { name: 'Preferences' }));

        const toggle = await screen.findByRole('checkbox', { name: /background animation/i });
        expect(toggle).toBeChecked();

        await waitFor(() => expect((fetch as unknown as Mock).mock.calls.length).toBeGreaterThan(0));
        const callsBeforeToggle = (fetch as unknown as Mock).mock.calls.length;

        fireEvent.click(toggle);

        expect(toggle).not.toBeChecked();
        expect(localStorage.getItem('engram:backgroundEffectsEnabled')).toBe('false');
        expect((fetch as unknown as Mock).mock.calls.length).toBe(callsBeforeToggle);
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ConfigWizard.test.tsx -t "background effects preference"`
Expected: FAIL — no element with role `checkbox` and accessible name matching `/background animation/i` exists yet.

- [ ] **Step 3: Create the settings component**

Create `frontend/src/components/BackgroundEffectsSetting.tsx`:

```tsx
import { useBackgroundEffectsEnabled } from '../app/hooks/useBackgroundEffectsEnabled';

/**
 * Self-contained toggle for the ambient falling-code rip animation
 * (SvRipAnimation). Reads/writes localStorage directly via the hook rather
 * than routing through ConfigWizard's `config` state — this preference
 * describes the browser rendering the dashboard, not the backend host, so it
 * never touches AppConfig or the generic config save.
 */
export default function BackgroundEffectsSetting() {
    const [enabled, setEnabled] = useBackgroundEffectsEnabled();

    return (
        <div className="form-group checkbox-group">
            <label className="checkbox-label">
                <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                />
                <span className="checkbox-text">
                    <strong>Background Animation</strong>
                    <span className="checkbox-hint">
                        The falling-code effect shown behind the dashboard while a disc is
                        ripping. Turn this off on low-power devices (e.g. ARM64 single-board
                        computers) to reduce CPU/GPU usage. Independent of your OS's
                        reduced-motion setting — takes effect immediately, no restart needed.
                    </span>
                </span>
            </label>
        </div>
    );
}
```

- [ ] **Step 4: Add it to the Preferences step in `ConfigWizard.tsx`**

Add the import near the other settings-component imports (`frontend/src/components/ConfigWizard.tsx:8`):

```diff
 import GpuAccelerationSetting from './GpuAccelerationSetting';
+import BackgroundEffectsSetting from './BackgroundEffectsSetting';
```

Insert a new "Display" group after the "Notifications" group closes and before the "Configuration Summary" block (`frontend/src/components/ConfigWizard.tsx:1716-1718`):

```diff
                             </div>
                         </details>

+                        {/* ── Display ──────────────────────────────────────────── */}
+                        <details className="wizard-group" open>
+                            <summary>
+                                <span className="wizard-group-chevron">▸</span>Display
+                            </summary>
+                            <div className="wizard-group-body">
+
+                        <BackgroundEffectsSetting />
+
+                            </div>
+                        </details>
+
                         <div className="config-summary">
```

(This group defaults `open`, matching the first Preferences group — "Matching & ordering" — rather than the closed default used by the later groups, since this is the exact control a low-power-device user opens Settings to find.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ConfigWizard.test.tsx`
Expected: PASS (full file — confirms no regression to the other Preferences-step tests, e.g. the GPU deep-link test)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BackgroundEffectsSetting.tsx frontend/src/components/ConfigWizard.tsx frontend/src/components/ConfigWizard.test.tsx
git commit -m "feat: add Background Animation toggle to Settings > Preferences > Display"
```

---

### Task 4: Manual verification in the browser

**Files:** none (verification only)

- [ ] **Step 1: Start the backend with simulation enabled**

From `backend/`:
```powershell
$env:DEBUG = "true"
uv run uvicorn app.main:app --port 8000
```

- [ ] **Step 2: Start the frontend**

From `frontend/`:
```powershell
npm run dev
```

- [ ] **Step 3: Simulate a ripping disc**

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"INCEPTION_2010","content_type":"movie","simulate_ripping":true}'
```

- [ ] **Step 4: Confirm the animation renders by default**

Open `http://localhost:5173` in a browser. Confirm the falling-code background animation is visible behind the dashboard while the simulated disc is ripping.

- [ ] **Step 5: Toggle it off and confirm it stops immediately**

Open Settings (gear icon) → Preferences → Display, and un-check "Background Animation". Confirm the animation disappears immediately, with the settings modal still open (no need to click Save, no page reload).

- [ ] **Step 6: Confirm persistence across a reload**

Reload the page. Confirm the animation stays off (re-check DevTools → Application → Local Storage → `engram:backgroundEffectsEnabled` is `"false"`).

- [ ] **Step 7: Clean up**

```bash
curl -X DELETE localhost:8000/api/simulate/reset-all-jobs
```

Stop the backend/frontend dev servers started in Steps 1–2 (see CLAUDE.md "Important Rules" — never leave `uvicorn`/`makemkvcon` processes orphaned).

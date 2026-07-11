import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "engram:backgroundEffectsEnabled";
// Fired on `window` whenever any instance of this hook changes the
// preference, so other already-mounted instances (e.g. App.tsx's gate and
// the settings checkbox) re-sync immediately instead of only picking up the
// change on next mount/reload. Plain `storage` events don't cover this: they
// only fire in *other* tabs, never in the tab that made the change.
const CHANGE_EVENT = "engram:background-effects-changed";

// Used only when localStorage itself is unavailable (private browsing, quota,
// etc.) — the CHANGE_EVENT handler re-derives its value from storage, so
// without this, an explicit choice would get overwritten back to the
// reduced-motion-based default the moment the event it triggers fires.
let memoryFallback: boolean | null = null;

function readStoredPreference(): boolean | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "true") return true;
    if (raw === "false") return false;
    return null;
  } catch {
    return memoryFallback;
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

function computeCurrentValue(): boolean {
  const stored = readStoredPreference();
  if (stored !== null) return stored;
  return !prefersReducedMotion();
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
 *
 * Multiple call sites (App.tsx's gate, the settings checkbox) each mount
 * their own instance of this hook. A `CHANGE_EVENT` broadcast keeps them in
 * sync within the tab, so toggling the setting takes effect immediately
 * rather than only after the next mount/reload.
 */
export function useBackgroundEffectsEnabled(): [boolean, (enabled: boolean) => void] {
  const [enabled, setEnabledState] = useState<boolean>(computeCurrentValue);

  useEffect(() => {
    const onChange = () => setEnabledState(computeCurrentValue());
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next);
    try {
      localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // localStorage unavailable — keep the choice in memory for this session
      memoryFallback = next;
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return [enabled, setEnabled];
}

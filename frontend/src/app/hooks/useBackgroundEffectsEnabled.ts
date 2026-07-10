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

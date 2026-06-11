import { useEffect, useState } from "react";

/**
 * Reactive viewport media-query hook for layout breakpoints expressed in
 * inline styles (where CSS @media can't reach), e.g. collapsing the
 * dashboard side rail below 1100px.
 *
 * When `matchMedia` is unavailable (SSR / jsdom unit tests), returns
 * `defaultValue`. This defaults to `true` so `min-width` callers fall back to
 * the canonical desktop layout — but a caller using an inverted query like
 * `(prefers-reduced-motion: reduce)` should pass `defaultValue={false}` so the
 * fallback doesn't assert the opposite of the sensible default.
 */
export function useMediaQuery(query: string, defaultValue = true): boolean {
  const [matches, setMatches] = useState<boolean>(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? defaultValue
      : window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    setMatches(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

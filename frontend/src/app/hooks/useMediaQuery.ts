import { useEffect, useState } from "react";

/**
 * Reactive viewport media-query hook for layout breakpoints expressed in
 * inline styles (where CSS @media can't reach), e.g. collapsing the
 * dashboard side rail below 1100px.
 *
 * Defaults to `true` when `matchMedia` is unavailable (jsdom unit tests),
 * so components fall back to the canonical desktop layout.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(
    () =>
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function" ||
      window.matchMedia(query).matches,
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

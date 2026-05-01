import { useEffect, useRef, useState } from "react";

interface Props {
  /** Target value. The display animates from current toward target. */
  target: number;
  /** Format function. Defaults to integer percent (`0.42` → `"42%"`). */
  fmt?: (v: number) => string;
  /** Animation step (lerp factor) per frame. Smaller = slower. Default 0.08. */
  ease?: number;
  /** Snap threshold — if |diff| ≤ this, jump to target. Default 0.001. */
  snap?: number;
  className?: string;
}

const defaultFmt = (v: number) => `${Math.round(v * 100)}%`;

/**
 * Smoothly animates a numeric display toward a moving target value.
 * Uses requestAnimationFrame for smooth interpolation; safe to feed
 * with rapidly-updating WebSocket state.
 */
export function SvAnimValue({
  target,
  fmt = defaultFmt,
  ease = 0.08,
  snap = 0.001,
  className,
}: Props) {
  const [display, setDisplay] = useState(target);
  const raf = useRef<number | null>(null);
  const current = useRef(target);
  const goal = useRef(target);

  useEffect(() => {
    goal.current = target;
    if (raf.current !== null) return;

    const tick = () => {
      const diff = goal.current - current.current;
      if (Math.abs(diff) <= snap) {
        current.current = goal.current;
        setDisplay(goal.current);
        raf.current = null;
        return;
      }
      current.current += diff * ease;
      setDisplay(current.current);
      raf.current = requestAnimationFrame(tick);
    };

    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current !== null) {
        cancelAnimationFrame(raf.current);
        raf.current = null;
      }
    };
  }, [target, ease, snap]);

  return (
    <span className={`sv-tnum ${className ?? ""}`} data-testid="sv-anim-value">
      {fmt(display)}
    </span>
  );
}

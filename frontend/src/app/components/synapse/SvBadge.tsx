import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";

export type SvBadgeState =
  | "ripping"
  | "matching"
  | "matched"
  | "complete"
  | "queued"
  | "error"
  | "live"
  | "scanning"
  | "warn"
  | "review"
  | "idle";

export type SvBadgeSize = "sm" | "md";

interface Props {
  /** State preset (drives color + pulse). Mutually exclusive with `tone`. */
  state?: SvBadgeState;
  /** Free-form color override (used for non-state tags like resolution, edition, source). */
  tone?: string;
  /** Whether the tone-tinted variant should pulse (only honored when `tone` is set). */
  pulse?: boolean;
  children: ReactNode;
  /** Show the leading colored dot. Default: true for `state`, false for `tone`. */
  dot?: boolean;
  /**
   * Visual scale.
   *  - `md` (default): 10px text, 3px×8px padding — load-bearing state pills.
   *  - `sm`:           9px text, 1px×6px padding — inline tags (resolution, edition, source).
   */
  size?: SvBadgeSize;
  className?: string;
  style?: CSSProperties;
  testid?: string;
}

interface Resolved {
  fg: string;
  bg: string;
  pulse: boolean;
}

const STATES: Record<SvBadgeState, Resolved> = {
  ripping:  { fg: sv.magenta, bg: "rgba(255, 61, 127, 0.10)",  pulse: true  },
  matching: { fg: sv.amber,   bg: "rgba(252, 211, 77, 0.10)",  pulse: true  },
  matched:  { fg: sv.cyan,    bg: "rgba(94, 234, 212, 0.10)",  pulse: false },
  complete: { fg: sv.green,   bg: "rgba(134, 239, 172, 0.10)", pulse: false },
  queued:   { fg: sv.inkDim,  bg: "rgba(136, 147, 168, 0.06)", pulse: false },
  error:    { fg: sv.red,     bg: "rgba(255, 85, 85, 0.10)",   pulse: false },
  live:     { fg: sv.green,   bg: "rgba(134, 239, 172, 0.10)", pulse: true  },
  scanning: { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: true  },
  warn:     { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: true  },
  review:   { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: false },
  idle:     { fg: sv.inkFaint, bg: "rgba(74, 83, 105, 0.06)",  pulse: false },
};

const SIZE: Record<SvBadgeSize, { padding: string; fontSize: number; gap: number; dotSize: number; letter: string }> = {
  md: { padding: "3px 8px", fontSize: 10, gap: 6, dotSize: 6, letter: "0.18em" },
  sm: { padding: "1px 6px", fontSize: 9,  gap: 4, dotSize: 4, letter: "0.16em" },
};

/**
 * Status pill — the load-bearing badge of the Synapse v2 UI.
 *
 * Two modes:
 *   - **state preset** (`state="ripping"`) — eight load-bearing job/title states
 *     plus a few aux states. Pulses when appropriate. Default for state pills.
 *   - **free-form tone** (`tone="#60a5fa"`) — tag-style usage for resolution,
 *     edition, or source labels where the color is data-driven and doesn't
 *     map to a state. Dot hidden by default in this mode.
 *
 * Two sizes:
 *   - `md` (default) — load-bearing state pills.
 *   - `sm` — inline tags (replaces TrackGrid's local MicroBadge).
 */
export function SvBadge({
  state,
  tone,
  pulse: tonePulse,
  children,
  dot,
  size = "md",
  className,
  style,
  testid = "sv-badge",
}: Props) {
  const resolved: Resolved = state
    ? STATES[state]
    : { fg: tone ?? sv.inkDim, bg: `${tone ?? sv.inkDim}22`, pulse: !!tonePulse };

  const showDot = dot ?? !!state; // default: yes for state preset, no for tone tag
  const s = SIZE[size];

  const wrap: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: s.gap,
    padding: s.padding,
    fontFamily: sv.mono,
    fontSize: s.fontSize,
    fontWeight: state ? 500 : 700, // tags are bolder than state pills
    letterSpacing: s.letter,
    textTransform: "uppercase",
    color: resolved.fg,
    background: resolved.bg,
    border: `1px solid ${resolved.fg}55`,
    ...style,
  };

  const dotStyle: CSSProperties = {
    width: s.dotSize,
    height: s.dotSize,
    borderRadius: "50%",
    background: resolved.fg,
    boxShadow: `0 0 6px ${resolved.fg}`,
    animation: resolved.pulse ? "svPulse 1.2s ease-in-out infinite" : undefined,
    flexShrink: 0,
  };

  return (
    <span
      className={className}
      style={wrap}
      data-testid={testid}
      data-state={state}
      data-size={size}
    >
      {showDot && <span style={dotStyle} />}
      <span>{children}</span>
    </span>
  );
}

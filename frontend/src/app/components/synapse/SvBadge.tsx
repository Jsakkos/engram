import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";

export type SvBadgeState =
  | "ripping"
  | "matching"
  | "complete"
  | "queued"
  | "error"
  | "live"
  | "scanning"
  | "warn"
  | "review"
  | "idle";

interface Props {
  state: SvBadgeState;
  children: ReactNode;
  /** Show the leading colored dot. Default: true. */
  dot?: boolean;
  className?: string;
  style?: CSSProperties;
}

interface StateStyle {
  fg: string;
  bg: string;
  pulse: boolean;
}

const STATES: Record<SvBadgeState, StateStyle> = {
  ripping:  { fg: sv.magenta, bg: "rgba(255, 61, 127, 0.10)",  pulse: true  },
  matching: { fg: sv.amber,   bg: "rgba(252, 211, 77, 0.10)",  pulse: true  },
  complete: { fg: sv.green,   bg: "rgba(134, 239, 172, 0.10)", pulse: false },
  queued:   { fg: sv.inkDim,  bg: "rgba(136, 147, 168, 0.06)", pulse: false },
  error:    { fg: sv.red,     bg: "rgba(255, 85, 85, 0.10)",   pulse: false },
  live:     { fg: sv.green,   bg: "rgba(134, 239, 172, 0.10)", pulse: true  },
  scanning: { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: true  },
  warn:     { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: true  },
  review:   { fg: sv.yellow,  bg: "rgba(253, 224, 71, 0.10)",  pulse: false },
  idle:     { fg: sv.inkFaint, bg: "rgba(74, 83, 105, 0.06)",  pulse: false },
};

/**
 * Status pill with optional pulsing dot. Maps the eight load-bearing
 * job/title states + a few aux states (live, idle) to a consistent
 * color + animation.
 */
export function SvBadge({ state, children, dot = true, className, style }: Props) {
  const s = STATES[state];

  const wrap: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "3px 8px",
    fontFamily: sv.mono,
    fontSize: 10,
    fontWeight: 500,
    letterSpacing: "0.18em",
    textTransform: "uppercase",
    color: s.fg,
    background: s.bg,
    border: `1px solid ${s.fg}55`,
    ...style,
  };

  const dotStyle: CSSProperties = {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: s.fg,
    boxShadow: `0 0 6px ${s.fg}`,
    animation: s.pulse ? "svPulse 1.2s ease-in-out infinite" : undefined,
    flexShrink: 0,
  };

  return (
    <span className={className} style={wrap} data-testid="sv-badge" data-state={state}>
      {dot && <span style={dotStyle} />}
      <span>{children}</span>
    </span>
  );
}

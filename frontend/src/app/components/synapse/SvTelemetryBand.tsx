import type { CSSProperties } from "react";
import { sv } from "./tokens";

interface Props {
  /** Strings to scroll. The list is duplicated internally to enable seamless looping. */
  items: string[];
  /** Scroll period in seconds (full cycle). Default 90. */
  speed?: number;
  /** Item color. Defaults to inkDim. */
  color?: string;
  /** Separator color (the `·` between items). Defaults to cyan. */
  separatorColor?: string;
  className?: string;
  style?: CSSProperties;
}

/**
 * Pure-CSS scrolling marquee with a side-mask gradient for fade-in/out.
 * Drives the bottom status bar. No React state — animation runs entirely
 * in CSS so it's free during heavy WebSocket update bursts.
 */
export function SvTelemetryBand({
  items,
  speed = 90,
  color = sv.inkDim,
  separatorColor = sv.cyan,
  className,
  style,
}: Props) {
  // Duplicate the list so the keyframes can translate -50% and seam-match.
  const doubled = [...items, ...items];

  const wrap: CSSProperties = {
    position: "relative",
    flex: 1,
    overflow: "hidden",
    height: 16,
    maskImage:
      "linear-gradient(90deg, transparent 0, #000 5%, #000 95%, transparent 100%)",
    WebkitMaskImage:
      "linear-gradient(90deg, transparent 0, #000 5%, #000 95%, transparent 100%)",
    ...style,
  };

  const track: CSSProperties = {
    display: "inline-flex",
    whiteSpace: "nowrap",
    animation: `svScroll ${speed}s linear infinite`,
    fontFamily: sv.mono,
    fontSize: 10,
    letterSpacing: "0.20em",
    color,
    textTransform: "uppercase",
  };

  const sep: CSSProperties = {
    color: separatorColor,
    margin: "0 14px",
  };

  return (
    <div className={className} style={wrap} data-testid="sv-telemetry-band">
      <div style={track}>
        {doubled.map((item, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "center" }}>
            <span>{item}</span>
            <span style={sep}>·</span>
          </span>
        ))}
      </div>
    </div>
  );
}

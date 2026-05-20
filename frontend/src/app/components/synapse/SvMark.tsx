import { useId } from "react";
import { sv } from "./tokens";

interface Props {
  size?: number;
  /** Primary arc color. Defaults to cyan. */
  color?: string;
  /** Secondary read-line / node color. Defaults to magenta. */
  accent?: string;
  /** Render the radial background glow. */
  glow?: boolean;
  /** Force a single-color mark (read-line uses primary too). */
  monochrome?: boolean;
}

/**
 * Engram primary mark — three open arcs facing right, with a horizontal
 * read-line terminating in a node. Encodes "memory trace being written":
 * the arcs are disc rings / hippocampal replay, the line is the trace.
 *
 * Geometry per docs/design_handoff_brand/brand/marks.jsx — a 64-unit
 * viewBox, arc radii 24/16/8 opening to the right, stroke 2.5px, opacities
 * 1.0 / 0.78 / 0.55, node at (56, 32) r=3.5 with r=6.5 glow at opacity 0.18.
 */
export function SvMark({
  size = 38,
  color = sv.cyan,
  accent = sv.magenta,
  glow = true,
  monochrome = false,
}: Props) {
  const uid = useId().replace(/:/g, "-");
  const stroke = monochrome ? color : accent;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      data-testid="sv-mark"
      style={{ display: "block", overflow: "visible" }}
    >
      <defs>
        <radialGradient id={`sv-mark-g-${uid}`} cx="0.5" cy="0.5" r="0.55">
          <stop offset="0%" stopColor={color} stopOpacity={glow ? 0.22 : 0} />
          <stop offset="70%" stopColor={color} stopOpacity="0" />
        </radialGradient>
      </defs>
      {glow && <circle cx="32" cy="32" r="30" fill={`url(#sv-mark-g-${uid})`} />}

      {/* Three concentric open arcs — break facing right */}
      <path
        d="M 32 8 A 24 24 0 1 0 32 56"
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <path
        d="M 32 16 A 16 16 0 1 0 32 48"
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        opacity="0.78"
      />
      <path
        d="M 32 24 A 8 8 0 1 0 32 40"
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        opacity="0.55"
      />

      {/* Read-line + node — the "trace" being written into the rings */}
      <line
        x1="32"
        y1="32"
        x2="56"
        y2="32"
        stroke={stroke}
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <circle cx="56" cy="32" r="3.5" fill={stroke} />
      {glow && <circle cx="56" cy="32" r="6.5" fill={stroke} opacity="0.18" />}

      <title>Engram</title>
    </svg>
  );
}

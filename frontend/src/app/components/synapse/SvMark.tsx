import { sv } from "./tokens";

interface Props {
  size?: number;
  /** Primary ring color. Defaults to cyan. */
  color?: string;
  /** Secondary axon color. Defaults to magenta. */
  accent?: string;
}

/**
 * Engram logo mark — concentric rings + an axon stroke to the corner.
 * Reads as a synapse / neural-node motif. Scales linearly via `size`.
 */
export function SvMark({ size = 38, color = sv.cyan, accent = sv.magenta }: Props) {
  const id = `sv-mark-glow-${size}`;
  const half = size / 2;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      data-testid="sv-mark"
      style={{ filter: `drop-shadow(0 0 8px ${color}66)` }}
    >
      <defs>
        <radialGradient id={id} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="60%" stopColor={color} stopOpacity="0.05" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="16" cy="16" r="15" fill={`url(#${id})`} />
      <circle cx="16" cy="16" r="14" fill="none" stroke={color} strokeOpacity="0.7" strokeWidth="1" />
      <circle cx="16" cy="16" r="10" fill="none" stroke={color} strokeOpacity="0.5" strokeWidth="1" />
      <circle cx="16" cy="16" r="6" fill="none" stroke={color} strokeOpacity="0.35" strokeWidth="1" />
      <circle cx="16" cy="16" r="2" fill={color} />
      {/* Axon — a short stroke from the center to the upper-right ring */}
      <line x1="16" y1="16" x2="26" y2="6" stroke={accent} strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="26" cy="6" r="1.5" fill={accent} />
      {/* Position-based size hint (unused visually, kept for accessibility) */}
      <title>Engram · {half * 2}px</title>
    </svg>
  );
}

import { useId } from "react";
import { sv } from "./tokens";

interface Props {
  size?: number;
  color?: string;
  paper?: boolean;
  glow?: boolean;
  /** Render a filled center node — the audio-fingerprint variant of the mark. */
  node?: boolean;
}

const PAPER_INK = "#15161A";

/**
 * Engram monogram — the three open arcs. Pass `node` for the audio-fingerprint
 * variant that adds a filled center circle. Use at small sizes (≤32px) where the
 * dendrite would not render cleanly. Stroke bumped to 3px (vs. 2.5px on the full
 * mark) to compensate for the smaller render area.
 */
export function MarkMono({ size = 32, color, paper = false, glow = false, node = false }: Props) {
  const uid = useId().replace(/:/g, "-");
  const p = color ?? (paper ? PAPER_INK : sv.cyan);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      data-testid="sv-mark-mono"
      style={{ display: "block" }}
    >
      <defs>
        <radialGradient id={`sv-mono-g-${uid}`} cx="0.5" cy="0.5" r="0.55">
          <stop offset="0%" stopColor={p} stopOpacity={glow ? 0.25 : 0} />
          <stop offset="70%" stopColor={p} stopOpacity="0" />
        </radialGradient>
      </defs>
      {glow && <circle cx="32" cy="32" r="30" fill={`url(#sv-mono-g-${uid})`} />}
      <path
        d="M 32 8 A 24 24 0 1 0 32 56"
        fill="none"
        stroke={p}
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M 32 16 A 16 16 0 1 0 32 48"
        fill="none"
        stroke={p}
        strokeWidth="3"
        strokeLinecap="round"
        opacity="0.78"
      />
      <path
        d="M 32 24 A 8 8 0 1 0 32 40"
        fill="none"
        stroke={p}
        strokeWidth="3"
        strokeLinecap="round"
        opacity="0.55"
      />
      {node && <circle cx="32" cy="32" r="5" fill={p} />}
      <title>Engram</title>
    </svg>
  );
}

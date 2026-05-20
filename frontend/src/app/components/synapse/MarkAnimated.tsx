import { sv } from "./tokens";

interface Props {
  size?: number;
  color?: string;
  accent?: string;
}

/**
 * Engram mark with animation — arcs slowly rotate (14s linear infinite),
 * read-line stays stationary, node pulses both in opacity (1.2s) and
 * glow radius (1.6s). Used for splash and connection-lost states.
 *
 * The keyframes `engSpin` live in theme.css and are shared with any other
 * component that needs slow rotation.
 */
export function MarkAnimated({
  size = 96,
  color = sv.cyan,
  accent = sv.magenta,
}: Props) {
  return (
    <div
      data-testid="sv-mark-animated"
      style={{ width: size, height: size, position: "relative" }}
    >
      {/* Layer 1: rotating arcs */}
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        style={{
          display: "block",
          position: "absolute",
          inset: 0,
          animation: "engSpin 14s linear infinite",
        }}
      >
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
      </svg>

      {/* Layer 2: stationary read-line + pulsing node */}
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        style={{ display: "block", position: "absolute", inset: 0 }}
      >
        <line
          x1="32"
          y1="32"
          x2="56"
          y2="32"
          stroke={accent}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <circle cx="56" cy="32" r="3.5" fill={accent}>
          <animate
            attributeName="opacity"
            values="1;0.3;1"
            dur="1.2s"
            repeatCount="indefinite"
          />
        </circle>
        <circle cx="56" cy="32" r="6.5" fill={accent} opacity="0.18">
          <animate
            attributeName="r"
            values="6.5;10;6.5"
            dur="1.6s"
            repeatCount="indefinite"
          />
        </circle>
      </svg>
    </div>
  );
}

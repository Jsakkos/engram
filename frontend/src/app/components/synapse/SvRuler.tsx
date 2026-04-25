import type { CSSProperties } from "react";
import { sv } from "./tokens";

interface Props {
  /** Number of tick segments. Defaults to 40. */
  ticks?: number;
  color?: string;
  className?: string;
  style?: CSSProperties;
}

/**
 * Horizontal divider — a 1px line crossed by major (every 5th) and minor ticks.
 * Used to separate stat groups in side rails and detail panels.
 */
export function SvRuler({ ticks = 40, color = sv.lineMid, className, style }: Props) {
  const segments = Array.from({ length: ticks });

  return (
    <div
      className={className}
      style={{
        position: "relative",
        width: "100%",
        height: 10,
        ...style,
      }}
      data-testid="sv-ruler"
    >
      <div
        style={{
          position: "absolute",
          top: 5,
          left: 0,
          right: 0,
          height: 1,
          background: color,
        }}
      />
      <div style={{ position: "absolute", inset: 0, display: "flex", justifyContent: "space-between" }}>
        {segments.map((_, i) => {
          const major = i % 5 === 0;
          return (
            <div
              key={i}
              style={{
                width: 1,
                height: major ? 8 : 4,
                marginTop: major ? 1 : 3,
                background: color,
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

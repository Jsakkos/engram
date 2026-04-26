import type { CSSProperties } from "react";
import { accentColor, sv, type SvAccent } from "./tokens";

interface Props {
  /** Series values. Normalized to [0, 1] using `max` (or the series max if omitted). */
  values: number[];
  /** Explicit max for normalization. Defaults to Math.max(...values, epsilon). */
  max?: number;
  /** Accent color. Defaults to cyan. */
  accent?: SvAccent;
  /** Total chart height in px. Defaults to 70 (matches handoff). */
  height?: number;
  /** Pixel gap between bars. Defaults to 4. */
  gap?: number;
  /** Add a glow on the last bar (live indicator). Default: true. */
  highlightLast?: boolean;
  /** Render an empty placeholder when values is empty. Default: true. */
  showEmpty?: boolean;
  className?: string;
  style?: CSSProperties;
  testid?: string;
}

/**
 * Vertical bar chart — sharp 90° corners, gradient fill (accent → accent33),
 * optional last-bar glow for "this is happening now" semantics.
 *
 * Used by:
 *   - Dashboard side rail (rolling 60s throughput sparkline)
 *   - History stats rail (14-day throughput aggregate)
 */
export function SvBarChart({
  values,
  max,
  accent = "cyan",
  height = 70,
  gap = 4,
  highlightLast = true,
  showEmpty = true,
  className,
  style,
  testid = "sv-bar-chart",
}: Props) {
  const color = accentColor[accent];
  const observedMax = Math.max(...values, 0);
  const safeMax = max ?? (observedMax > 0 ? observedMax : 1e-6);

  const wrap: CSSProperties = {
    display: "flex",
    alignItems: "flex-end",
    gap,
    height,
    width: "100%",
    ...style,
  };

  // Treat empty array OR all-zero series as empty — both render as no
  // visible bars, so show the "no data" placeholder for either.
  const isEmpty = values.length === 0 || (max === undefined && observedMax === 0);

  if (isEmpty) {
    if (!showEmpty) return null;
    return (
      <div
        className={className}
        style={{ ...wrap, alignItems: "center", justifyContent: "center" }}
        data-testid={testid}
        data-empty="true"
      >
        <span
          style={{
            fontFamily: sv.mono,
            fontSize: 9,
            letterSpacing: "0.22em",
            color: sv.inkFaint,
            textTransform: "uppercase",
          }}
        >
          › no data
        </span>
      </div>
    );
  }

  return (
    <div className={className} style={wrap} data-testid={testid} data-count={values.length}>
      {values.map((raw, i) => {
        const v = Number.isFinite(raw) ? Math.max(0, raw) / safeMax : 0;
        const clamped = Math.min(1, v);
        const isLast = i === values.length - 1;
        return (
          <div
            key={i}
            data-testid={`${testid}-bar`}
            data-index={i}
            data-value={clamped}
            style={{
              flex: 1,
              minWidth: 2,
              height: `${clamped * 100}%`,
              background: `linear-gradient(180deg, ${color}, ${color}33)`,
              boxShadow: highlightLast && isLast ? `0 0 8px ${color}` : "none",
              transition: "height 0.3s ease",
            }}
          />
        );
      })}
    </div>
  );
}

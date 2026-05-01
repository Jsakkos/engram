import type { CSSProperties } from "react";
import { sv } from "./tokens";

interface Props {
  /** Fill ratio in [0, 1]. Values outside the range are clamped. */
  value: number;
  /** Bar height in px. Defaults to 3. */
  height?: number;
  /** Primary fill color (gradient start). Defaults to cyan. */
  color?: string;
  /** Secondary fill color (gradient end). Defaults to a brighter cyan. */
  secondary?: string;
  /** Track (background) color. Defaults to inkGhost. */
  track?: string;
  /** Add an outer glow on the fill. Default: true. */
  glow?: boolean;
  /** Add the chunk-tick overlay (10% repeating stripes). Default: true. */
  chunked?: boolean;
  className?: string;
  style?: CSSProperties;
  testid?: string;
}

/**
 * Horizontal progress bar — gradient fill with optional chunked tick overlay
 * and outer glow. Used everywhere from per-track byte progress to job-level
 * aggregate progress.
 */
export function SvBar({
  value,
  height = 3,
  color = sv.cyan,
  secondary,
  track = sv.inkGhost,
  glow = true,
  chunked = true,
  className,
  style,
  testid = "sv-bar",
}: Props) {
  const clamped = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
  const grad = secondary ?? color;

  const wrap: CSSProperties = {
    position: "relative",
    width: "100%",
    height,
    background: track,
    overflow: "hidden",
    ...style,
  };

  const fill: CSSProperties = {
    position: "absolute",
    inset: "0 auto 0 0",
    width: `${clamped * 100}%`,
    background: `linear-gradient(90deg, ${color}, ${grad})`,
    boxShadow: glow ? `0 0 10px ${color}` : undefined,
    transition: "width 0.3s ease",
  };

  const ticks: CSSProperties = chunked
    ? {
        position: "absolute",
        inset: 0,
        background: `repeating-linear-gradient(90deg, transparent 0 9%, ${sv.bg0}40 9% 10%)`,
        pointerEvents: "none",
      }
    : { display: "none" };

  return (
    <div className={className} style={wrap} data-testid={testid} data-value={clamped}>
      <div style={fill} />
      <div style={ticks} />
    </div>
  );
}

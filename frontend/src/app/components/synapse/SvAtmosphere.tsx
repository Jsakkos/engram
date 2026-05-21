import type { CSSProperties, ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import { sv } from "./tokens";
import { SvRipAnimation } from "./SvRipAnimation";

interface Props {
  children: ReactNode;
  /** Toggle the scanline overlay. Default: true. */
  scanlines?: boolean;
  /** When true, render the ambient falling-code layer behind all content. */
  ripActive?: boolean;
  className?: string;
  style?: CSSProperties;
}

/**
 * Full-screen atmospheric wrapper — the canvas every Synapse v2 screen
 * lives on. Layers:
 *   1. Solid bg0 base
 *   2. Cyan haze (top-left) + magenta haze (bottom-right) radial gradients
 *   3. Scanlines (1px cyan, repeating, 35% opacity)
 *   4. SVG grain (turbulence, 8% opacity, overlay blend)
 *   5. Vignette (corners darken to 50% black)
 *
 * Always-on per the design handoff — no settings toggle in production.
 * Children sit on z-index 1 so they're above all atmosphere layers.
 */
export function SvAtmosphere({
  children,
  scanlines = true,
  ripActive = false,
  className,
  style,
}: Props) {
  const root: CSSProperties = {
    position: "relative",
    minHeight: "100vh",
    background: sv.bg0,
    color: sv.ink,
    fontFamily: sv.sans,
    overflow: "hidden",
    ...style,
  };

  const haze: CSSProperties = {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    background: `
      radial-gradient(ellipse 60% 50% at 0% 0%, ${sv.cyan}19 0%, transparent 50%),
      radial-gradient(ellipse 60% 50% at 100% 100%, ${sv.magenta}13 0%, transparent 50%)
    `,
    zIndex: 0,
  };

  const scanlineLayer: CSSProperties = {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    background: `repeating-linear-gradient(0deg, ${sv.cyan}0d 0 1px, transparent 1px 3px)`,
    opacity: 0.35,
    zIndex: 0,
    mixBlendMode: "screen",
  };

  const vignette: CSSProperties = {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    background: "radial-gradient(ellipse 100% 80% at 50% 50%, transparent 50%, rgba(0,0,0,0.5) 100%)",
    zIndex: 0,
  };

  const grain: CSSProperties = {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    opacity: 0.08,
    mixBlendMode: "overlay",
    zIndex: 0,
  };

  const content: CSSProperties = {
    position: "relative",
    zIndex: 1,
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
  };

  return (
    <div className={className} style={root} data-testid="sv-atmosphere">
      <div style={haze} />
      {scanlines && <div style={scanlineLayer} data-testid="sv-scanlines" />}
      {/* Inline SVG grain — feTurbulence, blends overlay */}
      <svg style={grain} aria-hidden="true">
        <filter id="sv-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix values="0 0 0 0 1   0 0 0 0 1   0 0 0 0 1   0 0 0 0.4 0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#sv-grain)" />
      </svg>
      <div style={vignette} />
      {/* Ambient falling-code layer — sits above the atmosphere gradients
          (z-index 0) but below the content (z-index 1). */}
      <AnimatePresence>
        {ripActive && <SvRipAnimation key="rip" />}
      </AnimatePresence>
      <div style={content}>{children}</div>
    </div>
  );
}

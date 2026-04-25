import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";

interface Props {
  children: ReactNode;
  /** Toggle the scanline overlay. Default: true. */
  scanlines?: boolean;
  /** Toggle the distant skyline silhouette. Default: true. */
  skyline?: boolean;
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
 *   6. Skyline silhouette (bottom 180px, SVG, with window-light flickers)
 *
 * Always-on per the design handoff — no settings toggle in production.
 * Children sit on z-index 1 so they're above all atmosphere layers.
 */
export function SvAtmosphere({
  children,
  scanlines = true,
  skyline = true,
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

  const skylineLayer: CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    height: 180,
    pointerEvents: "none",
    opacity: 0.55,
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
      {skyline && (
        <svg
          style={skylineLayer}
          viewBox="0 0 1280 180"
          preserveAspectRatio="xMidYEnd slice"
          aria-hidden="true"
          data-testid="sv-skyline"
        >
          {/* A simple Tokyo-style silhouette — back row of varied building heights,
              front row stepped, with cyan + magenta window-light flickers. */}
          <defs>
            <linearGradient id="sv-skyline-fade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={sv.bg1} stopOpacity="0" />
              <stop offset="60%" stopColor={sv.bg1} stopOpacity="0.7" />
              <stop offset="100%" stopColor={sv.bg0} stopOpacity="1" />
            </linearGradient>
          </defs>
          {/* Back row (taller, fainter) */}
          <path
            d="M0,180 L0,90 L40,90 L40,70 L80,70 L80,100 L130,100 L130,55 L170,55 L170,80 L220,80 L220,40 L260,40 L260,75 L310,75 L310,60 L360,60 L360,90 L420,90 L420,30 L460,30 L460,75 L520,75 L520,55 L580,55 L580,85 L640,85 L640,45 L700,45 L700,70 L760,70 L760,30 L820,30 L820,75 L880,75 L880,55 L940,55 L940,90 L1000,90 L1000,40 L1060,40 L1060,75 L1120,75 L1120,60 L1180,60 L1180,90 L1240,90 L1240,70 L1280,70 L1280,180 Z"
            fill={sv.bg2}
            opacity="0.9"
          />
          {/* Front row (taller, sharper) */}
          <path
            d="M0,180 L0,130 L60,130 L60,110 L120,110 L120,140 L180,140 L180,95 L240,95 L240,125 L300,125 L300,105 L360,105 L360,135 L420,135 L420,90 L480,90 L480,120 L540,120 L540,140 L600,140 L600,100 L660,100 L660,130 L720,130 L720,95 L780,95 L780,135 L840,135 L840,115 L900,115 L900,140 L960,140 L960,105 L1020,105 L1020,130 L1080,130 L1080,95 L1140,95 L1140,125 L1200,125 L1200,140 L1260,140 L1260,105 L1280,105 L1280,180 Z"
            fill={sv.bg1}
          />
          {/* Window lights — cyan + magenta dots, flicker via CSS animation */}
          <g style={{ animation: "svFlicker 4s ease-in-out infinite" }}>
            <rect x="65"  y="115" width="2" height="2" fill={sv.cyan}    />
            <rect x="145" y="100" width="2" height="2" fill={sv.cyan}    />
            <rect x="205" y="115" width="2" height="2" fill={sv.magenta} />
            <rect x="305" y="125" width="2" height="2" fill={sv.cyan}    />
            <rect x="425" y="105" width="2" height="2" fill={sv.magenta} />
            <rect x="555" y="120" width="2" height="2" fill={sv.cyan}    />
            <rect x="685" y="115" width="2" height="2" fill={sv.magenta} />
            <rect x="785" y="105" width="2" height="2" fill={sv.cyan}    />
            <rect x="905" y="125" width="2" height="2" fill={sv.cyan}    />
            <rect x="1045" y="115" width="2" height="2" fill={sv.magenta} />
            <rect x="1145" y="115" width="2" height="2" fill={sv.cyan}   />
            <rect x="1225" y="115" width="2" height="2" fill={sv.cyan}   />
          </g>
          {/* Soft fade overlay to blend into the page bg */}
          <rect width="1280" height="180" fill="url(#sv-skyline-fade)" opacity="0.6" />
        </svg>
      )}
      <div style={content}>{children}</div>
    </div>
  );
}

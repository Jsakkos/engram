import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";
import { SvMark } from "./SvMark";
import { Wordmark } from "./Wordmark";

interface BaseProps {
  /** Wordmark font size; the mark scales relative to this. */
  size?: number;
  color?: string;
  paper?: boolean;
  glow?: boolean;
  style?: CSSProperties;
}

/**
 * 01 — Horizontal lockup (primary).
 * Mark + wordmark, baseline-aligned. Gap = size * 0.34, mark height = size * 1.18.
 */
export function LockupHorizontal({
  size = 56,
  color,
  paper = false,
  glow = true,
  style,
}: BaseProps) {
  return (
    <div
      data-testid="sv-lockup-horizontal"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.34,
        ...style,
      }}
    >
      <SvMark size={size * 1.18} color={color} glow={glow} monochrome={paper} />
      <Wordmark size={size} color={color} paper={paper} />
    </div>
  );
}

/**
 * 02 — Stacked lockup.
 * Mark above wordmark, both centered. Vertical gap = size * 0.30,
 * mark height = size * 1.6 (the focal element).
 */
export function LockupStacked({
  size = 64,
  color,
  paper = false,
  glow = true,
  style,
}: BaseProps) {
  return (
    <div
      data-testid="sv-lockup-stacked"
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: size * 0.3,
        ...style,
      }}
    >
      <SvMark size={size * 1.6} color={color} glow={glow} monochrome={paper} />
      <Wordmark size={size} color={color} paper={paper} />
    </div>
  );
}

interface DescriptorProps extends BaseProps {
  descriptor?: ReactNode;
}

/**
 * 03 — Horizontal lockup with mono descriptor beneath the wordmark.
 * Default descriptor: "MEDIA ARCHIVE".
 */
export function LockupWithDescriptor({
  size = 56,
  color,
  paper = false,
  glow = true,
  descriptor = "MEDIA ARCHIVE",
  style,
}: DescriptorProps) {
  const descriptorColor = paper ? "#5a5b62" : sv.inkDim;
  return (
    <div
      data-testid="sv-lockup-descriptor"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.34,
        ...style,
      }}
    >
      <SvMark size={size * 1.18} color={color} glow={glow} monochrome={paper} />
      <div style={{ display: "flex", flexDirection: "column", gap: size * 0.1 }}>
        <Wordmark size={size} color={color} paper={paper} />
        <div
          style={{
            fontFamily: sv.mono,
            fontSize: size * 0.2,
            fontWeight: 500,
            letterSpacing: "0.34em",
            color: descriptorColor,
            paddingLeft: 2,
            textTransform: "uppercase",
          }}
        >
          {descriptor}
        </div>
      </div>
    </div>
  );
}

interface MarkOnlyProps {
  size?: number;
  color?: string;
  accent?: string;
  glow?: boolean;
  monochrome?: boolean;
  style?: CSSProperties;
}

/**
 * 04 — Mark only.
 * For app icons, favicons, dock badges, and standalone branding moments.
 * The wordmark never appears in isolation without geometric reason.
 */
export function LockupMarkOnly({
  size = 64,
  color,
  accent,
  glow = true,
  monochrome = false,
  style,
}: MarkOnlyProps) {
  return (
    <div data-testid="sv-lockup-mark-only" style={{ display: "inline-flex", ...style }}>
      <SvMark
        size={size}
        color={color}
        accent={accent}
        glow={glow}
        monochrome={monochrome}
      />
    </div>
  );
}

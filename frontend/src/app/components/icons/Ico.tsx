import type { CSSProperties, ReactNode } from "react";

export interface IconProps {
  size?: number;
  /** Stroke color. Defaults to currentColor so the parent can drive it. */
  color?: string;
  /** Optional inner accent (used by lit icons like ripping/matching). */
  accent?: string;
  /** Add a drop-shadow glow at the icon's color. */
  glow?: boolean;
  title?: string;
  className?: string;
  style?: CSSProperties;
}

interface IcoBaseProps extends IconProps {
  children: ReactNode;
  defaultTitle?: string;
}

/**
 * Base wrapper for the Engram icon set.
 * 24×24 viewBox, 1.5px stroke, round caps + joins, no fill (children
 * declare their own fills for "lit" elements via currentColor or explicit
 * accent).
 *
 * Accessibility note: we use `aria-label` for the icon's name and do NOT
 * include an inner `<title>` element. SVG `<title>` is matched by
 * Playwright's `text=` selectors (and by some screen readers as duplicate
 * speech), and labels like "Complete"/"Ripping" inside an icon used next
 * to a visible "COMPLETE"/"RIPPING" pill would cause text-locator matches
 * to resolve to the (invisible) `<title>` rather than the visible label.
 * aria-label alone is sufficient for AT and avoids the conflict.
 */
export function Ico({
  children,
  size = 20,
  color,
  glow = false,
  title,
  defaultTitle,
  className,
  style,
}: IcoBaseProps) {
  const stroke = color ?? "currentColor";
  const accessibleName = title ?? defaultTitle;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      stroke={stroke}
      fill="none"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={accessibleName ? "img" : "presentation"}
      aria-label={accessibleName}
      aria-hidden={accessibleName ? undefined : true}
      className={className}
      style={{
        display: "block",
        overflow: "visible",
        color: stroke,
        filter: glow ? `drop-shadow(0 0 6px ${stroke}aa)` : undefined,
        ...style,
      }}
    >
      {children}
    </svg>
  );
}

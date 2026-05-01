import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";
import { SvCorners } from "./SvCorners";

interface Props {
  children: ReactNode;
  /** Border + corner-tick color. Defaults to lineMid (cyan @ 24% alpha). */
  accent?: string;
  /** Inner padding (px). Defaults to 24. */
  pad?: number;
  /** Adds an outer cyan glow + inner soft sheen. */
  glow?: boolean;
  /** Suppresses the corner ticks (for nested panels that already inherit ticks). */
  hideCorners?: boolean;
  className?: string;
  style?: CSSProperties;
  testid?: string;
}

/**
 * Bordered panel — the load-bearing container of the Synapse v2 UI.
 * 1px border, sharp 90° corners, optional outer glow, always renders
 * 8px L-bracket ticks at all four corners (the recurring motif).
 */
export function SvPanel({
  children,
  accent = sv.lineMid,
  pad = 24,
  glow = false,
  hideCorners = false,
  className,
  style,
  testid = "sv-panel",
}: Props) {
  const composed: CSSProperties = {
    position: "relative",
    border: `1px solid ${accent}`,
    background: `linear-gradient(180deg, rgba(18,24,39,0.75), rgba(10,14,24,0.85))`,
    padding: pad,
    boxShadow: glow
      ? `0 0 24px ${sv.cyan}22, inset 0 0 32px rgba(94,234,212,0.03)`
      : undefined,
    ...style,
  };

  return (
    <div data-testid={testid} className={className} style={composed}>
      {!hideCorners && <SvCorners color={sv.lineHi} />}
      {children}
    </div>
  );
}

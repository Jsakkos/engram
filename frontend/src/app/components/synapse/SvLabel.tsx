import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";

interface Props {
  children: ReactNode;
  /** Label color. Defaults to inkDim. */
  color?: string;
  /** Caret color (the leading `›`). Defaults to cyan. */
  caretColor?: string;
  /** Hide the caret. Default: false. */
  noCaret?: boolean;
  /** Font size in px. Defaults to 10. */
  size?: number;
  className?: string;
  style?: CSSProperties;
}

/**
 * Mono uppercase label with a leading `›` cyan caret.
 * The signature label form throughout Synapse v2 — used for section
 * headers, field labels, table column headers, etc.
 */
export function SvLabel({
  children,
  color = sv.inkDim,
  caretColor = sv.cyan,
  noCaret = false,
  size = 10,
  className,
  style,
}: Props) {
  const composed: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontFamily: sv.mono,
    fontSize: size,
    fontWeight: 400,
    letterSpacing: "0.20em",
    textTransform: "uppercase",
    color,
    ...style,
  };

  return (
    <span className={className} style={composed} data-testid="sv-label">
      {!noCaret && <span style={{ color: caretColor }}>›</span>}
      <span>{children}</span>
    </span>
  );
}

import type { CSSProperties } from "react";
import { sv } from "./tokens";

interface Props {
  size?: number;
  color?: string;
  /** Defaults to "0.14em"; track wider (0.20-0.26em) at small sizes. */
  letterSpacing?: string;
  /** Render against the light/paper edition. */
  paper?: boolean;
  /** Additional style overrides (text-shadow, line-height, etc.). */
  style?: CSSProperties;
}

const PAPER_INK = "#15161A";

/**
 * Engram wordmark — uppercase "ENGRAM" in Chakra Petch 700.
 * The wordmark stays uppercase always; title case is reserved for headings.
 */
export function Wordmark({
  size = 56,
  color,
  letterSpacing = "0.14em",
  paper = false,
  style,
}: Props) {
  return (
    <span
      data-testid="sv-wordmark"
      style={{
        fontFamily: sv.display,
        fontWeight: 700,
        fontSize: size,
        letterSpacing,
        color: color ?? (paper ? PAPER_INK : sv.ink),
        lineHeight: 1,
        textTransform: "uppercase",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      ENGRAM
    </span>
  );
}

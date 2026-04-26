import { sv } from "./tokens";

interface Props {
  color?: string;
  size?: number;
}

/**
 * Four 8×8px L-bracket tick marks at each corner of the parent.
 * The parent must be `position: relative` for these to anchor correctly.
 */
export function SvCorners({ color = sv.lineHi, size = 8 }: Props) {
  const baseStyle = {
    position: "absolute" as const,
    width: size,
    height: size,
    borderColor: color,
    borderStyle: "solid" as const,
    pointerEvents: "none" as const,
  };

  return (
    <>
      <div data-testid="sv-corner-tl" style={{ ...baseStyle, top: 0, left: 0, borderWidth: "1.5px 0 0 1.5px" }} />
      <div data-testid="sv-corner-tr" style={{ ...baseStyle, top: 0, right: 0, borderWidth: "1.5px 1.5px 0 0" }} />
      <div data-testid="sv-corner-bl" style={{ ...baseStyle, bottom: 0, left: 0, borderWidth: "0 0 1.5px 1.5px" }} />
      <div data-testid="sv-corner-br" style={{ ...baseStyle, bottom: 0, right: 0, borderWidth: "0 1.5px 1.5px 0" }} />
    </>
  );
}

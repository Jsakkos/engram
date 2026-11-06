import type { CSSProperties } from "react";
import { sv } from "./tokens";
import { SvTelemetryBand } from "./SvTelemetryBand";

interface Props {
  activeCount: number;
  completedCount: number;
  isConnected: boolean;
  version: string;
  /** Optional drive descriptor — e.g. "DRIVE E:" or "DRIVE OFFLINE". */
  driveLabel?: string;
  /**
   * Telemetry strip strings. Must come from real signals; the bar renders an
   * empty spacer rather than filler when none are known (#720).
   */
  telemetry?: string[];
}

/**
 * Bottom status bar — fixed-height (44px) telemetry strip.
 * Layout: [active/archived counts + drive] [scrolling telemetry] [WS pill + version].
 *
 * `marginTop: auto` pins the bar to the bottom of `SvAtmosphere`'s flex
 * column even when the page is shorter than the viewport; `position: sticky`
 * keeps it docked while the page scrolls.
 */
export function SvStatusBar({
  activeCount,
  completedCount,
  isConnected,
  version,
  driveLabel,
  telemetry,
}: Props) {
  const root: CSSProperties = {
    height: 44,
    padding: "0 20px",
    display: "flex",
    alignItems: "center",
    gap: 24,
    borderTop: `1px solid ${sv.line}`,
    background: "rgba(18,24,39,0.55)",
    backdropFilter: "blur(8px)",
    fontFamily: sv.mono,
    fontSize: 10,
    letterSpacing: "0.20em",
    textTransform: "uppercase",
    color: sv.inkDim,
    position: "sticky",
    bottom: 0,
    marginTop: "auto",
    zIndex: 20,
  };

  return (
    <footer style={root} data-testid="sv-statusbar">
      {/* Left: live counts + drive */}
      <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <CountPill color={sv.magenta} label={`${activeCount} ACTIVE`} />
        <CountPill color={sv.green} label={`${completedCount} ARCHIVED`} />
        {driveLabel && <span style={{ color: sv.inkDim }}>{driveLabel}</span>}
      </div>

      {/* Center: scrolling telemetry */}
      {telemetry && telemetry.length > 0 ? (
        <SvTelemetryBand items={telemetry} />
      ) : (
        <div style={{ flex: 1 }} />
      )}

      {/* Right: WS state + version */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span
          style={{ color: isConnected ? sv.green : sv.inkFaint }}
          data-testid="sv-status-ws"
        >
          WS · {isConnected ? "CONNECTED" : "OFFLINE"}
        </span>
        <span
          style={{
            border: `1px solid ${sv.lineMid}`,
            padding: "2px 8px",
            color: sv.cyanHi,
          }}
        >
          v{version}
        </span>
      </div>
    </footer>
  );
}

function CountPill({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color }}>
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          boxShadow: `0 0 6px ${color}`,
        }}
      />
      <span>{label}</span>
    </span>
  );
}

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
  /** Optional override of the telemetry strip strings. */
  telemetry?: string[];
}

/**
 * Bottom status bar — fixed-height (36px) telemetry strip.
 * Layout: [active/archived counts + drive] [scrolling telemetry] [WS pill + version].
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
    height: 36,
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
    zIndex: 20,
  };

  // Ambient-only band: WS / version / drive are already shown as left & right
  // pills, so they're omitted here to avoid simultaneous duplication.
  const defaultTelemetry = [
    "UNIT 07",
    "SESSION 01",
    "BUFFER NOMINAL",
    "THERMAL NOMINAL",
    "CPU IDLE",
    "GPU IDLE",
  ];

  return (
    <footer style={root} data-testid="sv-statusbar">
      {/* Left: live counts + drive */}
      <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <CountPill color={sv.magenta} label={`${activeCount} ACTIVE`} />
        <CountPill color={sv.green} label={`${completedCount} ARCHIVED`} />
        {driveLabel && <span style={{ color: sv.inkFaint }}>{driveLabel}</span>}
      </div>

      {/* Center: scrolling telemetry */}
      <SvTelemetryBand items={telemetry ?? defaultTelemetry} />

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

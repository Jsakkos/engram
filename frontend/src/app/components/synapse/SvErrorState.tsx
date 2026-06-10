import type { ReactNode } from "react";
import { monoLabelStyle, sv } from "./tokens";
import { SvPanel } from "./SvPanel";
import { SvLabel } from "./SvLabel";
import { SvRuler } from "./SvRuler";

export type SvErrorKind = "no-match" | "no-drive";

interface Props {
  kind: SvErrorKind;
  /** Override the headline. Defaults to per-kind copy. */
  headline?: string;
  /** Override the subtitle. Defaults to per-kind copy. */
  subtitle?: string;
  /** Optional diagnostics map (label → value) shown in the right panel. */
  diagnostics?: Record<string, string>;
  /** Optional trace ID rendered in the diagnostics footer. */
  traceId?: string;
  /** Action buttons rendered below the subtitle (typically 1–2 ghost+primary). */
  actions?: ReactNode;
}

interface KindConfig {
  tag: string;
  headline: string;
  subtitle: string;
  color: string;
}

const KIND: Record<SvErrorKind, KindConfig> = {
  "no-match": {
    tag: "— NO MATCH FOUND —",
    headline: "Unable to classify disc",
    subtitle:
      "We couldn't confidently identify this disc. Try eject + reinsert, or use Edit · Manual to provide a title.",
    color: sv.red,
  },
  "no-drive": {
    tag: "— DRIVE OFFLINE —",
    headline: "Optical drive not available",
    subtitle:
      "No optical drive detected. Check the cable, or drop MKV folders into your staging directory.",
    color: sv.red,
  },
};

/**
 * Full-screen takeover for terminal/empty states.
 * Two-column layout (1.2fr / 1fr) at 60px padding.
 *  - Left: tag, big headline (color-tinted, glow), subtitle, action buttons
 *  - Right: diagnostics panel with key/value rows + ruler + trace ID footer
 *
 * Designed to drop inside <SvAtmosphere> on the dashboard
 * when a job state warrants a full takeover (FAILED, no-drive, no-match).
 */
export function SvErrorState({
  kind,
  headline,
  subtitle,
  diagnostics,
  traceId,
  actions,
}: Props) {
  const k = KIND[kind];
  const head = headline ?? k.headline;
  const sub = subtitle ?? k.subtitle;

  // Kind-color tints reused across the diagnostics panel.
  const tint = {
    panel: `${k.color}55`,
    ruler: `${k.color}33`,
    divider: `${k.color}22`,
  };

  return (
    <div
      data-testid="sv-error-state"
      data-kind={kind}
      style={{
        flex: 1,
        display: "grid",
        gridTemplateColumns: "1.2fr 1fr",
        gap: 32,
        padding: 60,
        alignItems: "center",
      }}
    >
      {/* Left — message column */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 560 }}>
        <span
          style={{
            ...monoLabelStyle({ size: 11, color: k.color, letterSpacing: "0.30em" }),
            fontWeight: 600,
          }}
        >
          {k.tag}
        </span>
        <h1
          style={{
            fontFamily: sv.display,
            fontSize: 56,
            fontWeight: 700,
            letterSpacing: "0.02em",
            lineHeight: 1.1,
            color: k.color,
            textShadow: `0 0 24px ${k.color}66`,
            textWrap: "balance",
            margin: 0,
          }}
        >
          {head}
        </h1>
        <p
          style={{
            fontFamily: sv.sans,
            fontSize: 16,
            lineHeight: 1.5,
            color: sv.inkDim,
            maxWidth: 480,
            margin: 0,
          }}
        >
          {sub}
        </p>
        {actions && <div style={{ display: "flex", gap: 12, marginTop: 8 }}>{actions}</div>}
      </div>

      {/* Right — diagnostics */}
      <SvPanel pad={20} accent={tint.panel} style={{ background: `${sv.bg1}cc` }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <SvLabel size={11}>Diagnostics</SvLabel>
          <span style={monoLabelStyle({ size: 9, color: k.color, letterSpacing: "0.20em" })}>
            {kind}
          </span>
        </div>
        <SvRuler ticks={32} color={tint.ruler} />
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 110px) 1fr",
            rowGap: 8,
            columnGap: 12,
            marginTop: 14,
            fontFamily: sv.mono,
            fontSize: 11,
          }}
        >
          {diagnostics &&
            Object.entries(diagnostics).map(([key, value]) => (
              <div key={key} style={{ display: "contents" }}>
                <dt style={{ color: sv.inkDim, letterSpacing: "0.18em", textTransform: "uppercase" }}>
                  {key}
                </dt>
                <dd style={{ color: sv.ink, margin: 0, wordBreak: "break-all" }}>{value}</dd>
              </div>
            ))}
        </dl>
        <div
          style={{
            ...monoLabelStyle({ size: 9, color: sv.inkDim, letterSpacing: "0.20em" }),
            marginTop: 18,
            paddingTop: 12,
            borderTop: `1px solid ${tint.divider}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span>Trace ID</span>
          <span className="sv-tnum" style={{ color: sv.inkDim }}>
            {traceId ?? "—"}
          </span>
        </div>
      </SvPanel>
    </div>
  );
}

import type { ReactNode } from "react";
import { sv } from "./tokens";
import { SvPanel } from "./SvPanel";
import { SvLabel } from "./SvLabel";
import { SvRuler } from "./SvRuler";

export type SvErrorKind = "no-match" | "no-drive" | "empty-library";

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
  "empty-library": {
    tag: "— LIBRARY EMPTY —",
    headline: "Your library is waiting",
    subtitle:
      "Insert a disc to start your archive. Each completed rip lands here as a poster card.",
    color: sv.cyan,
  },
};

/**
 * Full-screen takeover for terminal/empty states.
 * Two-column layout (1.2fr / 1fr) at 60px padding.
 *  - Left: tag, big headline (color-tinted, glow), subtitle, action buttons
 *  - Right: diagnostics panel with key/value rows + ruler + trace ID footer
 *
 * Designed to drop inside <SvAtmosphere> on the dashboard or library pages
 * when a job state warrants a full takeover (FAILED, no-drive, empty).
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
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.30em",
            textTransform: "uppercase",
            color: k.color,
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
      <SvPanel pad={20} accent={`${k.color}55`} style={{ background: `${sv.bg1}cc` }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <SvLabel size={11}>Diagnostics</SvLabel>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 9,
              letterSpacing: "0.20em",
              color: k.color,
              textTransform: "uppercase",
            }}
          >
            {kind}
          </span>
        </div>
        <SvRuler ticks={32} color={`${k.color}33`} />
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
                <dt style={{ color: sv.inkFaint, letterSpacing: "0.18em", textTransform: "uppercase" }}>
                  {key}
                </dt>
                <dd style={{ color: sv.ink, margin: 0, wordBreak: "break-all" }}>{value}</dd>
              </div>
            ))}
        </dl>
        <div
          style={{
            marginTop: 18,
            paddingTop: 12,
            borderTop: `1px solid ${k.color}22`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontFamily: sv.mono,
            fontSize: 9,
            letterSpacing: "0.20em",
            color: sv.inkFaint,
            textTransform: "uppercase",
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

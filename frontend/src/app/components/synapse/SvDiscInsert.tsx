import type { CSSProperties } from "react";
import { SvBadge } from "./SvBadge";
import { SvCorners } from "./SvCorners";
import { SvLabel } from "./SvLabel";
import { SvPanel } from "./SvPanel";
import { SvRuler } from "./SvRuler";
import { sv } from "./tokens";

export type DiscInsertPhase = "detect" | "scan" | "classify" | "ready";

const PHASES: DiscInsertPhase[] = ["detect", "scan", "classify", "ready"];

interface Props {
  phase: DiscInsertPhase;
  /** Drive label to render top-left (e.g. "Drive E:\\"). */
  driveLabel?: string;
  /** Disc capacity / volume sub-line (e.g. "BD50 · 46.2 GB"). */
  driveMeta?: string;
  /** Best-match title (large headline). */
  bestMatch?: string;
  /** Mono meta line under the best match (e.g. "TV · SEASON 01 · ARRESTED_DEVELOPMENT_S1D1"). */
  bestMatchMeta?: string;
  /** When the right panel has no useful data, show a soft-placeholder line instead. */
  scanningHint?: string;
  testid?: string;
}

/**
 * Disc-insert / classification visualization. Two-column layout: animated
 * SVG disc on the left (rings + sweep + chapter ticks + detection dots),
 * classification details on the right, phase breadcrumb at the bottom.
 *
 * Rendered inside DiscCard when a job is in the `identifying` state.
 * Compact form factor (~320px tall) so it fits inside a card body.
 */
export function SvDiscInsert({
  phase,
  driveLabel,
  driveMeta,
  bestMatch,
  bestMatchMeta,
  scanningHint = "› analyzing disc structure…",
  testid = "sv-disc-insert",
}: Props) {
  const phaseIdx = PHASES.indexOf(phase);

  return (
    <div
      data-testid={testid}
      data-phase={phase}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(220px, 1fr) 1.2fr",
        gap: 14,
        alignItems: "stretch",
      }}
    >
      <DiscPanel phase={phase} driveLabel={driveLabel} driveMeta={driveMeta} phaseIdx={phaseIdx} />
      <ClassifyPanel
        bestMatch={bestMatch}
        bestMatchMeta={bestMatchMeta}
        scanningHint={scanningHint}
        phase={phase}
      />
    </div>
  );
}

// ── Left column: SVG disc + phase breadcrumb ────────────────────────────────

function DiscPanel({
  phase,
  driveLabel,
  driveMeta,
  phaseIdx,
}: {
  phase: DiscInsertPhase;
  driveLabel?: string;
  driveMeta?: string;
  phaseIdx: number;
}) {
  const isAnimating = phase === "scan" || phase === "classify";
  const showDots = phase === "classify" || phase === "ready";

  const wrap: CSSProperties = {
    position: "relative",
    border: `1px solid ${sv.lineMid}`,
    background: `radial-gradient(ellipse at 50% 40%, rgba(94,234,212,0.08), transparent 60%), rgba(5,7,12,0.7)`,
    minHeight: 260,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  };

  return (
    <div style={wrap} data-testid="sv-disc-insert-panel">
      <SvCorners color={sv.lineHi} />

      {/* drive label overlay (top-left) */}
      {(driveLabel || driveMeta) && (
        <div
          style={{
            position: "absolute",
            top: 14,
            left: 14,
            display: "flex",
            flexDirection: "column",
            gap: 4,
            zIndex: 1,
          }}
        >
          {driveLabel && <SvLabel color={sv.cyan}>{driveLabel}</SvLabel>}
          {driveMeta && (
            <span
              style={{
                fontFamily: sv.mono,
                fontSize: 10,
                letterSpacing: "0.22em",
                color: sv.ink,
                textTransform: "uppercase",
              }}
            >
              {driveMeta}
            </span>
          )}
        </div>
      )}

      {/* radar SVG */}
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "30px 14px 14px",
        }}
      >
        <svg
          viewBox="0 0 400 400"
          style={{ width: "85%", maxHeight: 220, aspectRatio: "1 / 1" }}
          data-testid="sv-disc-insert-radar"
        >
          <defs>
            <radialGradient id="sv-disc-bg" cx="0.5" cy="0.5" r="0.5">
              <stop offset="0%" stopColor={sv.cyan} stopOpacity="0.3" />
              <stop offset="60%" stopColor={sv.cyan} stopOpacity="0.05" />
              <stop offset="100%" stopColor={sv.cyan} stopOpacity="0" />
            </radialGradient>
            <linearGradient id="sv-disc-sweep" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor={sv.cyan} stopOpacity="0" />
              <stop offset="100%" stopColor={sv.cyan} stopOpacity="0.7" />
            </linearGradient>
          </defs>

          <circle cx="200" cy="200" r="180" fill="url(#sv-disc-bg)" />

          {/* concentric rings */}
          {[180, 150, 120, 90, 60, 30].map((r, i) => (
            <circle
              key={r}
              cx="200"
              cy="200"
              r={r}
              fill="none"
              stroke={sv.cyan}
              strokeWidth="0.6"
              opacity={0.2 + i * 0.08}
            />
          ))}

          {/* crosshair */}
          <line x1="200" y1="10" x2="200" y2="390" stroke={sv.cyan} strokeWidth="0.4" opacity="0.3" />
          <line x1="10" y1="200" x2="390" y2="200" stroke={sv.cyan} strokeWidth="0.4" opacity="0.3" />

          {/* spinning sweep wedge — only during scan/classify */}
          {isAnimating && (
            <g style={{ transformOrigin: "200px 200px", animation: "svSpin 3s linear infinite" }}>
              <path
                d="M 200 200 L 380 200 A 180 180 0 0 0 200 20 Z"
                fill="url(#sv-disc-sweep)"
                opacity="0.5"
              />
            </g>
          )}

          {/* center hub */}
          <circle cx="200" cy="200" r="8" fill={sv.cyan} />
          <circle cx="200" cy="200" r="3" fill={sv.bg0} />

          {/* 36 chapter ticks on outer ring; lit progressively as phase advances */}
          {Array.from({ length: 36 }, (_, i) => {
            const ang = (i / 36) * Math.PI * 2;
            const active = i < Math.floor((phaseIdx + 1) * 9);
            return (
              <line
                key={i}
                x1={200 + Math.cos(ang) * 180}
                y1={200 + Math.sin(ang) * 180}
                x2={200 + Math.cos(ang) * 170}
                y2={200 + Math.sin(ang) * 170}
                stroke={active ? sv.cyanHi : sv.inkGhost}
                strokeWidth="1.2"
              />
            );
          })}

          {/* 8 pulsing detection dots (classify/ready phases) */}
          {showDots &&
            [0, 2, 5, 7, 11, 14, 17, 22].map((t) => {
              const ang = (t / 36) * Math.PI * 2 - Math.PI / 2;
              return (
                <circle
                  key={t}
                  cx={200 + Math.cos(ang) * 150}
                  cy={200 + Math.sin(ang) * 150}
                  r="3"
                  fill={sv.cyan}
                >
                  <animate
                    attributeName="opacity"
                    values="1;0.3;1"
                    dur="1.5s"
                    repeatCount="indefinite"
                    begin={`${t * 0.05}s`}
                  />
                </circle>
              );
            })}
        </svg>
      </div>

      {/* phase breadcrumb */}
      <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <SvRuler ticks={20} />
        <div style={{ display: "flex", gap: 0 }} data-testid="sv-disc-insert-breadcrumb">
          {PHASES.map((p, i) => {
            const isActive = i === phaseIdx;
            const isPast = i < phaseIdx;
            const borderColor = i <= phaseIdx ? sv.cyan : sv.line;
            const textColor = isActive ? sv.cyanHi : isPast ? sv.cyan : sv.inkFaint;
            return (
              <div
                key={p}
                data-testid={`sv-disc-insert-phase-${p}`}
                data-active={isActive ? "true" : "false"}
                style={{
                  flex: 1,
                  padding: "6px 8px",
                  borderTop: `2px solid ${borderColor}`,
                  fontFamily: sv.mono,
                  fontSize: 9,
                  letterSpacing: "0.2em",
                  color: textColor,
                  textTransform: "uppercase",
                }}
              >
                <div style={{ opacity: 0.6, fontSize: 8 }}>0{i + 1}</div>
                {p}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Right column: classification details ────────────────────────────────────

function ClassifyPanel({
  bestMatch,
  bestMatchMeta,
  scanningHint,
  phase,
}: {
  bestMatch?: string;
  bestMatchMeta?: string;
  scanningHint: string;
  phase: DiscInsertPhase;
}) {
  const hasMatch = phase === "classify" || phase === "ready";
  const badgeState = phase === "ready" ? "complete" : phase === "detect" ? "queued" : "scanning";
  const badgeText = phase === "ready" ? "READY" : phase === "detect" ? "DETECTING" : "ANALYZING";

  return (
    <SvPanel
      pad={18}
      glow
      testid="sv-disc-insert-classify"
      style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 260 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <SvLabel color={sv.cyan}>Disc · classification</SvLabel>
        <SvBadge state={badgeState}>{badgeText}</SvBadge>
      </div>

      {hasMatch && bestMatch ? (
        <div>
          <SvLabel size={9} style={{ marginBottom: 6 }}>
            Best match
          </SvLabel>
          <div
            data-testid="sv-disc-insert-best-match"
            style={{
              fontFamily: sv.display,
              fontWeight: 700,
              fontSize: 28,
              color: sv.cyanHi,
              letterSpacing: "0.04em",
              lineHeight: 1.1,
              textShadow: `0 0 18px ${sv.cyan}55`,
              wordBreak: "break-word",
            }}
          >
            {bestMatch}
          </div>
          {bestMatchMeta && (
            <div
              style={{
                marginTop: 8,
                fontFamily: sv.mono,
                fontSize: 10,
                letterSpacing: "0.18em",
                color: sv.inkDim,
                textTransform: "uppercase",
              }}
            >
              {bestMatchMeta}
            </div>
          )}
        </div>
      ) : (
        <div
          style={{
            fontFamily: sv.mono,
            fontSize: 11,
            letterSpacing: "0.2em",
            color: sv.cyan,
            textTransform: "uppercase",
            animation: "svPulse 1.5s ease-in-out infinite",
          }}
        >
          {scanningHint}
        </div>
      )}

      <SvRuler ticks={24} />

      <div
        style={{
          marginTop: "auto",
          fontFamily: sv.mono,
          fontSize: 9,
          letterSpacing: "0.22em",
          color: sv.inkFaint,
          textTransform: "uppercase",
        }}
      >
        ›{" "}
        {phase === "detect"
          ? "drive online · awaiting media"
          : phase === "scan"
            ? "reading title structure · runtime patterns"
            : phase === "classify"
              ? "tmdb match found · audio fingerprint pending"
              : "classification complete · ripping queued"}
      </div>
    </SvPanel>
  );
}

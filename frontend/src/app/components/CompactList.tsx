import { AnimatePresence, motion } from "motion/react";
import type { CSSProperties, ReactNode } from "react";
import { sv } from "./synapse";
import type { DiscData } from "./DiscCard";
import { discStateLabel } from "./discState";
import { formatEtaCompact } from "../../utils/formatting";

const buttonBase: CSSProperties = {
  fontFamily: sv.mono,
  letterSpacing: "0.20em",
  textTransform: "uppercase",
  cursor: "pointer",
};

/**
 * Compact list view for the dashboard — sv-token row layout that mirrors the
 * SvPanel vocabulary used elsewhere (1px tinted border, sharp corners, mono
 * uppercase headers). Row actions mirror the expanded cards: Review for
 * match reviews, Fix title for identity reviews, Cancel for non-terminal jobs.
 */
export function CompactList({
  discs,
  onReview,
  onCancel,
  onReIdentify,
}: {
  discs: DiscData[];
  onReview: (id: string) => void;
  onCancel: (id: string) => void;
  onReIdentify: (id: string) => void;
}) {
  const colTemplate = "auto auto 1fr 140px 60px auto";
  const stateColor: Partial<Record<DiscData["state"], string>> = {
    completed: sv.green,
    error: sv.red,
    ripping: sv.magenta,
    scanning: sv.cyan,
    review_needed: sv.yellow,
    matching: sv.amber,
    organizing: sv.purple,
  };
  const typeColor: Record<DiscData["mediaType"], string> = {
    movie: sv.magenta,
    tv: sv.cyan,
    unknown: sv.inkFaint,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {/* Column header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: colTemplate,
          columnGap: 16,
          padding: "8px 12px",
          fontFamily: sv.mono,
          fontSize: 9,
          fontWeight: 700,
          letterSpacing: "0.22em",
          textTransform: "uppercase",
          color: sv.inkFaint,
          borderBottom: `1px solid ${sv.line}`,
        }}
      >
        <span>State</span>
        <span>Type</span>
        <span>Title</span>
        <span>Progress</span>
        <span style={{ textAlign: "right" }}>ETA</span>
        <span>Actions</span>
      </div>
      <AnimatePresence mode="popLayout">
        {discs.map((disc) => {
          const stateC = stateColor[disc.state] ?? sv.inkDim;
          const typeC = typeColor[disc.mediaType];
          const showProgress = disc.progress > 0 && disc.state !== "completed";
          const matchReview =
            disc.needsReview && !disc.identityReview && (disc.tracks?.length ?? 0) > 0;
          // tracksLoaded mirrors DiscCard's gate: titles resolve after the job
          // list, so without it the button could flash before data settles.
          const identityReview =
            disc.needsReview && !!disc.identityReview && !!disc.tracksLoaded;
          return (
            <motion.div
              key={disc.id}
              layout
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              style={{
                display: "grid",
                gridTemplateColumns: colTemplate,
                columnGap: 16,
                alignItems: "center",
                padding: "10px 12px",
                background: sv.bg1,
                border: `1px solid ${sv.line}`,
                fontFamily: sv.mono,
                fontSize: 12,
                transition: "background 120ms, border-color 120ms",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = sv.bg2;
                e.currentTarget.style.borderColor = sv.lineMid;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = sv.bg1;
                e.currentTarget.style.borderColor = sv.line;
              }}
            >
              {/* State */}
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    background: stateC,
                    boxShadow: `0 0 6px ${stateC}aa`,
                    flexShrink: 0,
                  }}
                />
                <span
                  style={{
                    color: sv.inkDim,
                    textTransform: "uppercase",
                    letterSpacing: "0.16em",
                    fontSize: 10,
                    width: 110,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {discStateLabel(disc.state)}
                </span>
              </div>
              {/* Type */}
              <span
                style={{
                  color: typeC,
                  fontWeight: 700,
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: "0.18em",
                }}
              >
                {disc.mediaType === "unknown" ? "…" : disc.mediaType}
              </span>
              {/* Title */}
              <span
                style={{
                  color: sv.ink,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {disc.title}
              </span>
              {/* Progress */}
              <div>
                {showProgress ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div
                      style={{
                        flex: 1,
                        height: 3,
                        background: sv.bg3,
                        position: "relative",
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          inset: "0 auto 0 0",
                          width: `${disc.progress}%`,
                          background: `linear-gradient(90deg, ${sv.cyan}, ${sv.cyanHi})`,
                          boxShadow: `0 0 6px ${sv.cyan}88`,
                          transition: "width 0.3s ease",
                        }}
                      />
                    </div>
                    <span
                      className="sv-tnum"
                      style={{ color: sv.cyanHi, fontSize: 10, fontWeight: 700 }}
                    >
                      {disc.progress.toFixed(0)}%
                    </span>
                  </div>
                ) : disc.state === "completed" ? (
                  <span style={{ color: sv.green, fontSize: 10, letterSpacing: "0.20em" }}>DONE</span>
                ) : (
                  <span style={{ color: sv.inkFaint }}>—</span>
                )}
              </div>
              {/* ETA */}
              <span
                className="sv-tnum"
                style={{
                  color: sv.inkDim,
                  fontSize: 11,
                  textAlign: "right",
                }}
              >
                {formatEtaCompact(disc.etaSeconds)}
              </span>
              {/* Actions */}
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {matchReview && (
                  <CompactRowButton color={sv.yellow} onClick={() => onReview(disc.id)}>
                    Review
                  </CompactRowButton>
                )}
                {identityReview && (
                  <CompactRowButton color={sv.yellow} onClick={() => onReIdentify(disc.id)}>
                    Fix title
                  </CompactRowButton>
                )}
                {disc.state !== "completed" && disc.state !== "error" && (
                  <CompactRowButton color={sv.red} onClick={() => onCancel(disc.id)}>
                    Cancel
                  </CompactRowButton>
                )}
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

function CompactRowButton({
  color,
  onClick,
  children,
}: {
  color: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        ...buttonBase,
        height: 22,
        padding: "0 8px",
        background: sv.bg0,
        border: `1px solid ${color}55`,
        color,
        fontSize: 9,
        fontWeight: 700,
        transition: "border-color 120ms, box-shadow 120ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = color;
        e.currentTarget.style.boxShadow = `0 0 8px ${color}55`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = `${color}55`;
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {children}
    </button>
  );
}

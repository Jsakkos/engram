import React from "react";
import { motion } from "motion/react";
import { Loader2, CheckCircle2, AlertTriangle, Vote } from "lucide-react";
import type { Track, TrackState } from "./DiscCard";
import { sv, SvBar, SvLabel } from "./synapse";

interface TrackGridProps {
  tracks: Track[];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

interface StateConfig {
  label: string;
  color: string;
  border: string;
  bg: string;
  Icon: typeof Loader2 | null;
}

const STATE: Record<TrackState, StateConfig> = {
  pending:   { label: "PENDING",  color: sv.inkDim,  border: `${sv.line}`,         bg: `${sv.bg2}66`, Icon: null         },
  ripping:   { label: "RIPPING",  color: sv.magenta, border: `${sv.magenta}66`,    bg: `${sv.magenta}10`, Icon: Loader2  },
  matching:  { label: "MATCHING", color: sv.amber,   border: `${sv.amber}55`,      bg: `${sv.amber}10`, Icon: Vote       },
  matched:   { label: "MATCHED",  color: sv.green,   border: `${sv.green}55`,      bg: `${sv.green}10`, Icon: CheckCircle2 },
  failed:    { label: "FAILED",   color: sv.red,     border: `${sv.red}66`,        bg: `${sv.red}10`, Icon: AlertTriangle  },
  completed: { label: "DONE",     color: sv.green,   border: `${sv.green}55`,      bg: `${sv.green}10`, Icon: CheckCircle2 },
};

interface BadgeProps {
  color: string;
  children: React.ReactNode;
  testid?: string;
}

function MicroBadge({ color, children, testid }: BadgeProps) {
  return (
    <span
      data-testid={testid}
      style={{
        padding: "1px 6px",
        background: `${color}22`,
        border: `1px solid ${color}55`,
        fontFamily: sv.mono,
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: "0.16em",
        color,
      }}
    >
      {children}
    </span>
  );
}

const matchSourceColor = (source?: string): string => {
  if (source === "discdb") return "#60a5fa"; // blue
  if (source === "user") return sv.green;
  return sv.purple;
};

const matchSourceLabel = (source?: string): string => {
  if (source === "discdb") return "DISCDB";
  if (source === "user") return "MANUAL";
  return "ENGRAM";
};

export const TrackGrid = React.memo(function TrackGrid({ tracks }: TrackGridProps) {
  return (
    <div data-testid="sv-track-grid" style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <SvLabel>TRACK STATUS</SvLabel>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 8,
        }}
      >
        {tracks.map((track, index) => {
          const config = STATE[track.state];
          const Icon = config.Icon;
          const ripPct =
            track.expectedSizeBytes && track.actualSizeBytes
              ? Math.min(1, track.actualSizeBytes / track.expectedSizeBytes)
              : Math.max(0, Math.min(1, track.progress / 100));

          return (
            <motion.div
              key={track.id}
              data-testid="sv-track-card"
              data-state={track.state}
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: index * 0.05 }}
              style={{
                position: "relative",
                padding: 12,
                background: config.bg,
                border: `1px solid ${config.border}`,
                overflow: "hidden",
                transition: "all 0.18s",
                cursor: "pointer",
              }}
              whileHover={{ y: -2 }}
            >
              {/* Left accent bar */}
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: 2,
                  background: config.color,
                  boxShadow: `0 0 6px ${config.color}88`,
                }}
              />

              {/* Header — title + icon */}
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  {track.title.startsWith('Track ') && (
                    <div style={{ fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing: "0.2em", marginBottom: 2 }}>
                      TRACK {index + 1}
                    </div>
                  )}
                  <div
                    style={{
                      fontFamily: sv.mono,
                      fontSize: 12,
                      fontWeight: 700,
                      color: config.color,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {track.title}
                  </div>
                  {track.duration && (
                    <div className="sv-tnum" style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, marginTop: 3 }}>
                      {track.duration}
                    </div>
                  )}

                  {/* Quality / source / extra badges */}
                  {(track.videoResolution || track.edition || track.isExtra || track.matchSource) && (
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
                      {track.matchSource && (
                        <MicroBadge
                          color={matchSourceColor(track.matchSource)}
                          testid={`source-badge-${track.matchSource}`}
                        >
                          {matchSourceLabel(track.matchSource)}
                        </MicroBadge>
                      )}
                      {track.videoResolution && (
                        <MicroBadge color={sv.cyan}>{track.videoResolution}</MicroBadge>
                      )}
                      {track.edition && (
                        <MicroBadge color={sv.magenta}>{track.edition}</MicroBadge>
                      )}
                      {track.isExtra && <MicroBadge color={sv.yellow}>EXTRA</MicroBadge>}
                    </div>
                  )}
                </div>

                {Icon && (
                  <motion.div
                    animate={
                      track.state === "ripping" || track.state === "matching"
                        ? { rotate: 360 }
                        : {}
                    }
                    transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                  >
                    <Icon size={14} color={config.color} />
                  </motion.div>
                )}
              </div>

              {/* Failed: error message */}
              {track.state === "failed" && track.errorMessage && (
                <div
                  title={track.errorMessage}
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 10,
                    color: `${sv.red}cc`,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    marginTop: 4,
                  }}
                >
                  {track.errorMessage}
                </div>
              )}

              {/* Pending: queued tag */}
              {track.state === "pending" && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, letterSpacing: "0.18em" }}>
                    QUEUED
                  </span>
                </div>
              )}

              {/* Ripping progress */}
              {track.state === "ripping" && (
                <div style={{ marginTop: 6 }}>
                  <SvBar value={ripPct} color={sv.magenta} secondary={sv.magentaHi} height={3} chunked={false} />
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      marginTop: 4,
                    }}
                  >
                    <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkFaint }}>
                      {config.label}
                    </span>
                    {track.expectedSizeBytes && track.actualSizeBytes ? (
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="sv-tnum" style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim }}>
                          {formatBytes(track.actualSizeBytes)} / {formatBytes(track.expectedSizeBytes)}
                        </span>
                        <span
                          className="sv-tnum"
                          style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, color: config.color }}
                        >
                          {(ripPct * 100).toFixed(1)}%
                        </span>
                      </div>
                    ) : (
                      <span
                        className="sv-tnum"
                        style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, color: config.color }}
                      >
                        {track.progress.toFixed(1)}%
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Output filename after rip / before organization */}
              {track.outputFilename && !track.organizedTo && track.state !== "pending" && track.state !== "ripping" && (
                <div
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 10,
                    color: sv.inkDim,
                    marginTop: 4,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {track.outputFilename}
                </div>
              )}

              {/* Matching progress */}
              {track.state === "matching" && (
                <div style={{ marginTop: 6 }}>
                  <SvBar value={track.progress / 100} color={sv.amber} secondary={sv.cyan} height={3} chunked={false} />
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      marginTop: 4,
                    }}
                  >
                    <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkFaint }}>
                      {config.label}
                    </span>
                    <span
                      className="sv-tnum"
                      style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, color: config.color }}
                    >
                      {track.progress.toFixed(1)}%
                    </span>
                  </div>
                </div>
              )}

              {/* Matching: top candidates with voting */}
              {track.state === "matching" && track.matchCandidates && track.matchCandidates.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {track.matchCandidates.slice(0, 3).map((candidate, idx) => (
                    <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                      <span
                        style={{
                          fontFamily: sv.mono,
                          fontSize: 10,
                          color: sv.amber,
                          fontWeight: 600,
                          flex: 1,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {candidate.episode}
                      </span>
                      <span
                        className="sv-tnum"
                        style={{ fontFamily: sv.mono, fontSize: 10, color: sv.amber, fontWeight: 700, flexShrink: 0 }}
                      >
                        {Math.min(candidate.votes, candidate.targetVotes)}/{candidate.targetVotes}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Matched: final match + runners-up + organization paths */}
              {track.state === "matched" && track.finalMatch && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                    <span
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 11,
                        color: sv.green,
                        borderLeft: `2px solid ${sv.green}`,
                        paddingLeft: 6,
                        flex: 1,
                      }}
                    >
                      → {track.finalMatch}
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                      {track.finalMatchConfidence !== undefined && (
                        <span
                          className="sv-tnum"
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            fontWeight: 700,
                            color:
                              track.finalMatchConfidence >= 0.7 ? sv.green :
                              track.finalMatchConfidence >= 0.4 ? sv.yellow : sv.red,
                          }}
                        >
                          {(track.finalMatchConfidence * 100).toFixed(0)}%
                        </span>
                      )}
                      {track.finalMatchVotes !== undefined && (
                        <span
                          className="sv-tnum"
                          style={{ fontFamily: sv.mono, fontSize: 10, color: sv.green, fontWeight: 700 }}
                        >
                          {Math.min(track.finalMatchVotes, track.finalMatchTargetVotes || 4)}/{track.finalMatchTargetVotes || 4}
                        </span>
                      )}
                    </span>
                  </div>

                  {track.matchCandidates && track.matchCandidates.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingTop: 4 }}>
                      {track.matchCandidates
                        .filter(c => c.episode !== track.finalMatch)
                        .slice(0, 2)
                        .map((candidate, idx) => (
                          <div
                            key={idx}
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: 12,
                              paddingLeft: 6,
                              borderLeft: `2px solid ${sv.inkGhost}`,
                            }}
                          >
                            <span
                              style={{
                                fontFamily: sv.mono,
                                fontSize: 10,
                                color: sv.inkFaint,
                                flex: 1,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {candidate.episode}
                            </span>
                            <span
                              className="sv-tnum"
                              style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, flexShrink: 0 }}
                            >
                              {Math.min(candidate.votes, candidate.targetVotes)}/{candidate.targetVotes}
                            </span>
                          </div>
                        ))}
                    </div>
                  )}

                  {/* Organization paths (after organizing completes) */}
                  {track.organizedTo && (
                    <div
                      style={{
                        paddingTop: 8,
                        borderTop: `1px solid ${sv.green}33`,
                        display: "flex",
                        flexDirection: "column",
                        gap: 4,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                        <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkFaint, flexShrink: 0 }}>
                          FROM:
                        </span>
                        <span
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            color: sv.inkDim,
                            wordBreak: "break-all",
                          }}
                        >
                          {track.outputFilename || track.organizedFrom}
                        </span>
                      </div>
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                        <span
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            color: sv.green,
                            flexShrink: 0,
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                          }}
                        >
                          <span>→</span>
                          {track.isExtra && <span style={{ color: sv.yellow }}>[EXTRA]</span>}
                        </span>
                        <span
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            color: sv.green,
                            wordBreak: "break-all",
                          }}
                        >
                          {track.organizedTo.split('/').slice(-2).join('/')}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
});

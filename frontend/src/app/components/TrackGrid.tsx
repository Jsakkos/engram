import React from "react";
import { motion } from "motion/react";
import { IcoRipping, IcoMatching, IcoComplete, IcoError } from "./icons";
import { IcoDisc } from "./icons/media";
import type { Track, TrackState } from "./DiscCard";
import { sv, SvBadge, SvBar, SvLabel, MarkMono } from "./synapse";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
import { formatBytesBinary } from "../../utils/formatting";

interface TrackGridProps {
  tracks: Track[];
  /** Job-level escalation note (e.g. "Resolving episode conflicts — pass 2 of 3"
   *  / "Deep re-matching low-confidence titles — pass 1 of 3"). When set, the
   *  currently-matching tracks get a "DEEP RE-MATCH" chip so the user sees that
   *  the spinning matching state is an auto-resolution pass, not initial work. */
  conflictStatus?: string;
  /** Skip a queued/not-yet-ripped track (PENDING/QUEUED). Omit to hide the control. */
  onSkipTrack?: (titleId: number) => void;
  /** Reverse a skip while still reversible (SKIPPED, not yet ripped). */
  onUnskipTrack?: (titleId: number) => void;
}

/** Parse "… pass N of M" out of a conflict_status note, if present. */
function parsePassInfo(note?: string): string | null {
  if (!note) return null;
  const m = /pass (\d+) of (\d+)/i.exec(note);
  return m ? `${m[1]}/${m[2]}` : null;
}

interface StateConfig {
  label: string;
  color: string;
  border: string;
  bg: string;
  Icon: React.ComponentType<{ size?: number; color?: string }> | null;
}

const STATE: Record<TrackState, StateConfig> = {
  pending:   { label: "PENDING",  color: sv.inkDim,  border: `${sv.line}`,         bg: `${sv.bg2}66`, Icon: null         },
  ripping:   { label: "RIPPING",  color: sv.magenta, border: `${sv.magenta}66`,    bg: `${sv.magenta}10`, Icon: IcoRipping },
  // QUEUED: ripped/on disk, waiting for a match slot. Muted cyan + no icon so it
  // reads as idle ("in line"), distinct from the active amber MATCHING spinner.
  queued:    { label: "QUEUED",   color: sv.cyan,    border: `${sv.cyan}44`,       bg: `${sv.cyan}0a`, Icon: null        },
  matching:  { label: "MATCHING", color: sv.amber,   border: `${sv.amber}55`,      bg: `${sv.amber}10`, Icon: IcoMatching },
  matched:   { label: "MATCHED",  color: sv.green,   border: `${sv.green}55`,      bg: `${sv.green}10`, Icon: IcoComplete },
  review:    { label: "NEEDS REVIEW", color: sv.yellow, border: `${sv.yellow}66`,  bg: `${sv.yellow}10`, Icon: IcoError  },
  failed:    { label: "FAILED",   color: sv.red,     border: `${sv.red}66`,        bg: `${sv.red}10`, Icon: IcoError    },
  completed: { label: "DONE",     color: sv.green,   border: `${sv.green}55`,      bg: `${sv.green}10`, Icon: IcoComplete },
  // SKIPPED: user opted this track out of the rip. Muted, no icon, no accent —
  // it should recede from the active grid rather than compete for attention.
  skipped:   { label: "SKIPPED",  color: sv.inkDim,  border: `${sv.inkDim}44`,     bg: `${sv.bg2}55`, Icon: null       },
};

type SourceDesc = {
  kind: "icon" | "text";
  label: string;
  tone: string;
  tooltip: string;
  node?: boolean;
  Icon?: React.ComponentType<{ size?: number; color?: string }>;
};

// Engram-engine sources render the brand mark (icon-only, label on hover);
// external/manual sources render a text chip. Unknown sources fall back to a
// generic purple text chip so nothing renders blank.
const SOURCE_DESC: Record<string, SourceDesc> = {
  engram:             { kind: "icon", label: "ENGRAM", tone: sv.cyan,    tooltip: "Matched by Engram (ASR)" },
  engram_chromaprint: { kind: "icon", label: "ENGRAM", tone: sv.magenta, tooltip: "Matched by Engram (audio fingerprint)", node: true },
  discdb:             { kind: "icon", label: "DISCDB", tone: sv.blue,    tooltip: "Matched from TheDiscDB", Icon: IcoDisc },
  ai_llm:             { kind: "text", label: "AI",     tone: sv.purple,  tooltip: "Identified by AI" },
  user:               { kind: "text", label: "MANUAL", tone: sv.green,   tooltip: "Assigned manually" },
};

function sourceDesc(source: string): SourceDesc {
  return (
    SOURCE_DESC[source] ?? {
      kind: "text",
      label: source.toUpperCase(),
      tone: sv.purple,
      tooltip: `Matched by ${source}`,
    }
  );
}

/** Provider chip — Engram mark (icon + tooltip) or a text source badge. */
function SourceChip({ source }: { source: string }) {
  const desc = sourceDesc(source);
  if (desc.kind === "text") {
    return (
      <SvBadge size="sm" tone={desc.tone} testid={`source-badge-${source}`}>
        {desc.label}
      </SvBadge>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid={`source-badge-${source}`}
          aria-label={desc.tooltip}
          title={desc.tooltip}
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 5px",
            border: `1px solid ${desc.tone}55`,
            background: `${desc.tone}10`,
          }}
        >
          {desc.Icon ? (
            <desc.Icon size={12} color={desc.tone} />
          ) : (
            <MarkMono size={12} color={desc.tone} node={desc.node} />
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent>{desc.tooltip}</TooltipContent>
    </Tooltip>
  );
}

export const TrackGrid = React.memo(function TrackGrid({ tracks, conflictStatus, onSkipTrack, onUnskipTrack }: TrackGridProps) {
  const passInfo = parsePassInfo(conflictStatus);
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

          // Only a confidently-matched, non-extra track carries a provider chip;
          // default to the Engram (ASR) mark when match_source is missing
          // (older/edge rows). Review/pending/ripping tracks get no chip even if
          // they still carry a stale match_source.
          const isConfidentMatch =
            (track.state === "matched" || track.state === "completed") && !!track.finalMatch;
          const chipSource =
            track.isExtra || !isConfidentMatch ? undefined : track.matchSource ?? "engram";

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
                    <div style={{ fontFamily: sv.mono, fontSize: 9, color: sv.inkDim, letterSpacing: "0.2em", marginBottom: 2 }}>
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

                  {/* Quality / source / method / extra / deep-rematch badges */}
                  {(track.videoResolution || track.edition || track.isExtra || chipSource || (conflictStatus && track.state === "matching")) && (
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
                      {conflictStatus && track.state === "matching" && (
                        <SvBadge
                          size="sm"
                          tone={sv.magenta}
                          testid={`deep-rematch-chip-${track.id}`}
                        >
                          {passInfo ? `DEEP · ${passInfo}` : "DEEP RE-MATCH"}
                        </SvBadge>
                      )}
                      {chipSource && <SourceChip source={chipSource} />}
                      {chipSource && track.matchMethod === "full_file" && (
                        <SvBadge size="sm" tone={sv.inkDim} testid={`method-tag-full-file-${track.id}`}>
                          FULL-FILE
                        </SvBadge>
                      )}
                      {track.videoResolution && (
                        <SvBadge size="sm" tone={sv.cyan}>{track.videoResolution}</SvBadge>
                      )}
                      {track.edition && (
                        <SvBadge size="sm" tone={sv.magenta}>{track.edition}</SvBadge>
                      )}
                      {track.isExtra && <SvBadge size="sm" tone={sv.yellow}>EXTRA</SvBadge>}
                    </div>
                  )}
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                  {onSkipTrack && (track.state === "pending" || track.state === "queued") && (
                    <button
                      type="button"
                      data-testid={`skip-track-${track.id}`}
                      aria-label={`Skip track ${track.title}`}
                      title="Skip this track (don't rip it)"
                      onClick={(e) => { e.stopPropagation(); onSkipTrack(Number(track.id)); }}
                      onMouseEnter={(e) => {
                        // Arm destructive-but-reversible: magenta is the palette's
                        // rip/destructive accent, so hover previews the consequence.
                        e.currentTarget.style.color = sv.magenta;
                        e.currentTarget.style.borderColor = `${sv.magenta}88`;
                        e.currentTarget.style.background = `${sv.magenta}12`;
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.color = sv.inkDim;
                        e.currentTarget.style.borderColor = sv.line;
                        e.currentTarget.style.background = "transparent";
                      }}
                      style={{
                        fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                        color: sv.inkDim, background: "transparent",
                        border: `1px solid ${sv.line}`, borderRadius: 0, padding: "2px 6px",
                        cursor: "pointer", lineHeight: 1, transition: "color 0.15s, border-color 0.15s, background 0.15s",
                      }}
                    >
                      SKIP ✕
                    </button>
                  )}
                  {onUnskipTrack && track.state === "skipped" && (
                    <button
                      type="button"
                      data-testid={`unskip-track-${track.id}`}
                      aria-label={`Un-skip track ${track.title}`}
                      title="Un-skip (rip this track after all)"
                      onClick={(e) => { e.stopPropagation(); onUnskipTrack(Number(track.id)); }}
                      onMouseEnter={(e) => {
                        // Cyan is the affirmative/queued accent — brightening on
                        // hover reads as "put it back in line".
                        e.currentTarget.style.color = sv.cyanHi;
                        e.currentTarget.style.borderColor = `${sv.cyan}aa`;
                        e.currentTarget.style.background = `${sv.cyan}12`;
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.color = sv.cyan;
                        e.currentTarget.style.borderColor = `${sv.cyan}44`;
                        e.currentTarget.style.background = "transparent";
                      }}
                      style={{
                        fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                        color: sv.cyan, background: "transparent",
                        border: `1px solid ${sv.cyan}44`, borderRadius: 0, padding: "2px 6px",
                        cursor: "pointer", lineHeight: 1, transition: "color 0.15s, border-color 0.15s, background 0.15s",
                      }}
                    >
                      UN-SKIP
                    </button>
                  )}
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
                  <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing: "0.18em" }}>
                    QUEUED
                  </span>
                </div>
              )}

              {/* Skipped: opted out of the rip — muted, struck-through mark so the
                  card visually recedes from active tracks. */}
              {track.state === "skipped" && (
                <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 6 }}>
                  <span aria-hidden style={{ fontFamily: sv.mono, fontSize: 10, color: `${sv.inkDim}99` }}>
                    ✕
                  </span>
                  <span
                    style={{
                      fontFamily: sv.mono,
                      fontSize: 10,
                      color: sv.inkDim,
                      letterSpacing: "0.18em",
                      textDecoration: "line-through",
                      textDecorationColor: `${sv.inkDim}66`,
                    }}
                  >
                    SKIPPED, WILL NOT RIP
                  </span>
                </div>
              )}

              {/* Review: show best-guess episode when available, or "no match found" */}
              {track.state === "review" && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.yellow, letterSpacing: "0.18em", fontWeight: 700 }}>
                    NEEDS REVIEW
                  </span>
                  {track.finalMatch ? (
                    <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, marginLeft: 8 }}>
                      best guess {track.finalMatch}
                      {track.finalMatchConfidence !== undefined && (
                        <> — {Math.round(track.finalMatchConfidence * 100)}% confidence</>
                      )}
                      {track.finalMatchVotes !== undefined && track.finalMatchTargetVotes !== undefined && (
                        <> ({track.finalMatchVotes}/{track.finalMatchTargetVotes} votes)</>
                      )}
                      {" — confirm in review queue"}
                    </span>
                  ) : (
                    <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, marginLeft: 8 }}>
                      no match found — assign in review queue
                    </span>
                  )}
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
                    <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkDim }}>
                      {config.label}
                    </span>
                    {track.expectedSizeBytes && track.actualSizeBytes ? (
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="sv-tnum" style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim }}>
                          {formatBytesBinary(track.actualSizeBytes)} / {formatBytesBinary(track.expectedSizeBytes)}
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
                    <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkDim }}>
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
                                color: sv.inkDim,
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
                              style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, flexShrink: 0 }}
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
                        <span style={{ fontFamily: sv.mono, fontSize: 9, letterSpacing: "0.18em", color: sv.inkDim, flexShrink: 0 }}>
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

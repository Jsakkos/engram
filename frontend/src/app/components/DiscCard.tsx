import React from "react";
import { motion } from "motion/react";
import { CheckCircle2, Clock, Database, Folder } from "lucide-react";
import { IcoDisc, IcoRetry } from "./icons";
import { StateIndicator } from "./StateIndicator";
import { TrackGrid } from "./TrackGrid";
import { usePosterImage } from "./DiscCard/hooks/usePosterImage";
import { MediaTypeBadge } from "./DiscCard/MediaTypeBadge";
import { DiscMetadata } from "./DiscCard/DiscMetadata";
import { ActionButtons } from "./DiscCard/ActionButtons";
import { Bug } from "lucide-react";
import { useElapsedTime } from "../hooks/useElapsedTime";
import { sv, SvPanel, SvLabel, SvDiscInsert, SvProgressBar, type DiscInsertPhase } from "./synapse";
import { formatEta } from "../../utils/formatting";

export type MediaType = "movie" | "tv" | "unknown";
export type DiscState = "idle" | "scanning" | "review_needed" | "archiving_iso" | "ripping" | "matching" | "organizing" | "processing" | "completed" | "error";
export type TrackState = "pending" | "ripping" | "queued" | "matching" | "matched" | "review" | "failed" | "completed" | "skipped";

export interface MatchCandidate {
  episode: string;
  confidence: number;
  votes: number;
  targetVotes: number;
}

export interface Track {
  id: string;
  title: string;
  duration: string;
  state: TrackState;
  progress: number;

  matchCandidates?: MatchCandidate[];
  finalMatch?: string;
  finalMatchConfidence?: number;
  finalMatchVotes?: number;
  finalMatchTargetVotes?: number;

  outputFilename?: string;
  organizedFrom?: string;
  organizedTo?: string;
  isExtra?: boolean;

  videoResolution?: string;
  edition?: string;
  matchSource?: string;
  /** Which Engram matcher produced this result, when distinguishable:
   *  'chunk_vote' (ranked voting, has votes) | 'full_file' (whole-file fallback,
   *  no votes by construction). Undefined for DiscDB/AI/manual matches. */
  matchMethod?: "chunk_vote" | "full_file";

  fileSizeBytes?: number;
  expectedSizeBytes?: number;
  actualSizeBytes?: number;
  chapterCount?: number;

  errorMessage?: string;
}

export interface DiscData {
  id: string;
  title: string;
  subtitle?: string;
  discLabel?: string;
  sourceType?: 'disc' | 'import' | 'staging';
  coverUrl: string;
  mediaType: MediaType;
  state: DiscState;
  progress: number;
  isoProgress?: number;
  tracks?: Track[];
  currentSpeed?: string;
  etaSeconds?: number;
  subtitleStatus?: string;
  subtitleError?: string;
  startedAt?: string;
  needsReview?: boolean;
  reviewReason?: string;
  /** Which identify prompt this disc should surface, if any — 'name'
   *  (unreadable label), 'season' (show known, season unknown), or 'reidentify'
   *  (ambiguous same-name identity). Derived in the adapter from the live
   *  identity prompt (walk-away Phase B — surfaces while RIPPING too) or the
   *  review reason; drives the on-card / compact-row CTA (P13). Null when no
   *  prompt applies. */
  promptKind?: 'name' | 'season' | 'reidentify' | null;
  /** Review is about confirming the disc's IDENTITY (an ambiguous/unconfirmed
   *  show), not assigning episodes. Derived in the adapter from a null tmdb_id
   *  (or a same-name collision with no ripped titles). Drives the on-card hint
   *  banner + "Wrong title?" emphasis and suppresses the dead-end review-queue
   *  button. See transformJobToDiscData. */
  identityReview?: boolean;
  /** Resolved TMDB identity (null/absent until confirmed). */
  tmdbId?: number | null;
  tmdbName?: string | null;
  tmdbYear?: number | null;
  /** Whether this disc's titles have been fetched yet (vs. genuinely empty).
   *  Guards the identity-review banner against the title-load race. */
  tracksLoaded?: boolean;
  conflictStatus?: string;
  /** Backend-set human-readable cause when classification ran WITHOUT TMDB
   *  (key absent or rejected). Shown verbatim on active jobs; it also covers
   *  the configured-but-invalid-key case the global flag can't see (#243). */
  tmdbDegradedReason?: string;
  /** True when at least one title on this disc has a rip-level failure (re-rippable). */
  hasDamagedTrack?: boolean;
}

interface DiscCardProps {
  disc: DiscData;
  onCancel?: () => void;
  onReview?: () => void;
  onReIdentify?: () => void;
  onAdvance?: () => void;
  onReportBug?: () => void;
  onOpenSettings?: () => void;
  /** Open this disc's identify prompt (name / season) on demand. When set, the
   *  card renders a prominent CTA — the non-modal affordance that replaces the
   *  old auto-opening modal for review jobs that aren't the only active one (P13). */
  onIdentify?: () => void;
  /** Label for the identify CTA, e.g. "Name this disc" / "Select season". */
  identifyLabel?: string;
}

/**
 * Compact stat block — caret label above, big mono value below.
 * Used by the ripping/matching/organizing state stat grids.
 */
function SvStat({
  label,
  value,
  color = sv.cyanHi,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <SvLabel size={9}>{label}</SvLabel>
      <span
        className="sv-tnum"
        style={{
          fontFamily: sv.mono,
          fontSize: 16,
          fontWeight: 700,
          color,
          textShadow: `0 0 8px ${color}66`,
        }}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * Animated mono-uppercase state caption (e.g. "› MATCHING EPISODES…").
 * The caption text pulses; `extra` renders as a non-pulsing sibling.
 */
function PulseCaption({
  color,
  children,
  extra,
}: {
  color: string;
  children: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontFamily: sv.mono,
        fontSize: 12,
        color,
        letterSpacing: "0.2em",
        textTransform: "uppercase",
      }}
    >
      <motion.span
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1.5, repeat: Infinity }}
      >
        {children}
      </motion.span>
      {extra}
    </div>
  );
}

/**
 * Full-cover overlay anchored over the disc cover art — dark scrim with a
 * centered icon. Used by the active-state spinner and the completed checkmark.
 */
function CoverOverlay({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.35)",
      }}
    >
      {children}
    </div>
  );
}

const DiscCardComponent = React.forwardRef<HTMLDivElement, DiscCardProps>(
  ({ disc, onCancel, onReview, onReIdentify, onAdvance, onReportBug, onOpenSettings, onIdentify, identifyLabel }, ref) => {
    const [isHovered, setIsHovered] = React.useState(false);
    const posterUrl = usePosterImage(disc.id, disc.title);
    const isActive = !['completed', 'error', 'idle'].includes(disc.state);
    const elapsed = useElapsedTime(isActive ? disc.startedAt : undefined);
    const isRipping = disc.state === "ripping";

    const totalTrackCount = disc.tracks?.length ?? 0;
    const doneTrackCount =
      disc.tracks?.filter(t => ["matched", "completed"].includes(t.state)).length ?? 0;
    const failedTrackCount = disc.tracks?.filter(t => t.state === "failed").length ?? 0;

    // A disc in review to confirm its IDENTITY (ambiguous/unconfirmed show) has
    // nothing to do in the episode review queue — the only useful action is
    // "Wrong title?" (re-identify). `identityReview` is derived in the adapter
    // (null tmdb_id, or a same-name collision with no ripped titles) so it fires
    // even though such discs DO have titles enumerated at scan time — the reason
    // the old tracks-count check never triggered. Drives the on-card hint banner
    // and the emphasis on the re-identify button. `tracksLoaded` gates out the
    // title-load race: titles resolve after the job list, so without this guard
    // the banner/emphasis could flash then vanish on first render / reconnect.
    const showIdentityReview = !!disc.identityReview && !!disc.tracksLoaded;

    return (
      <motion.div
        ref={ref}
        layout
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -20 }}
        onHoverStart={() => setIsHovered(true)}
        onHoverEnd={() => setIsHovered(false)}
        aria-label={`${disc.title} — ${disc.state}`}
        data-state={disc.state}
      >
        <SvPanel
          glow
          pad={20}
          accent={isRipping ? `${sv.magenta}66` : sv.lineMid}
          testid="sv-job-card"
          style={{
            background: `linear-gradient(180deg, ${sv.bg2}cc, ${sv.bg1}ee)`,
          }}
        >
          <div style={{ display: "flex", gap: 20 }}>
            {/* Cover art — sharp 90° corners, holographic overlay on hover */}
            <motion.div
              whileHover={{ scale: 1.03 }}
              transition={{ type: "spring", stiffness: 300 }}
              style={{
                position: "relative",
                flexShrink: 0,
                width: 144,
                height: 144,
                overflow: "hidden",
                border: `1px solid ${sv.lineMid}`,
                background: sv.bg1,
              }}
            >
              {posterUrl ? (
                <img
                  src={posterUrl}
                  alt={`Poster for ${disc.title}`}
                  style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              ) : (
                <div
                  style={{
                    width: "100%",
                    height: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: `linear-gradient(135deg, ${sv.bg3}, ${sv.bg0})`,
                  }}
                >
                  <IcoDisc size={48} color={`${sv.cyan}55`} />
                </div>
              )}

              {/* Subtle holographic overlay */}
              <motion.div
                style={{
                  position: "absolute",
                  inset: 0,
                  background: `linear-gradient(135deg, ${sv.cyan}22, transparent, ${sv.magenta}22)`,
                  pointerEvents: "none",
                }}
                animate={{ opacity: [0.3, 0.6, 0.3] }}
                transition={{ duration: 3, repeat: Infinity }}
              />

              {/* Active-state spinning disc overlay */}
              {["scanning", "archiving_iso", "ripping", "matching", "organizing", "processing"].includes(disc.state) && (
                <CoverOverlay>
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                  >
                    <IcoDisc
                      size={44}
                      color={isRipping ? sv.magenta : sv.cyan}
                      style={{ filter: `drop-shadow(0 0 8px ${isRipping ? sv.magenta : sv.cyan}cc)` }}
                    />
                  </motion.div>
                </CoverOverlay>
              )}

              {disc.state === "completed" && (
                <CoverOverlay>
                  <CheckCircle2
                    size={44}
                    color={sv.green}
                    style={{ filter: `drop-shadow(0 0 8px ${sv.green}cc)` }}
                  />
                </CoverOverlay>
              )}

              {/* Media type badge anchored top-left */}
              <div style={{ position: "absolute", top: 6, left: 6, zIndex: 2 }}>
                <MediaTypeBadge mediaType={disc.mediaType} />
              </div>

              {/* Source badge — folder icon for watch-folder-imported jobs */}
              {disc.sourceType === 'import' && (
                <div
                  role="img"
                  aria-label="Imported from watch folder"
                  title="Imported from watch folder"
                  style={{
                    position: "absolute",
                    top: 6,
                    right: 6,
                    zIndex: 2,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 24,
                    height: 24,
                    background: `${sv.bg2}cc`,
                    border: `1px solid ${sv.line}`,
                  }}
                >
                  <Folder size={13} color={sv.cyanHi} />
                </div>
              )}
            </motion.div>

            {/* Content */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* Header — title + state pill + actions */}
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  gap: 16,
                  marginBottom: 16,
                }}
              >
                <DiscMetadata
                  title={disc.title}
                  subtitle={disc.subtitle}
                  discLabel={disc.discLabel}
                />
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                  {disc.hasDamagedTrack && (
                    <span
                      data-testid="disccard-damaged-badge"
                      title="One or more tracks failed to rip and may need re-ripping"
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: "0.2em",
                        color: sv.magenta,
                        textShadow: `0 0 6px ${sv.magenta}66`,
                      }}
                    >
                      DAMAGED TRACK
                    </span>
                  )}
                  {failedTrackCount > 0 && (
                    <span
                      title="Some tracks failed during ripping"
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: "0.2em",
                        color: sv.red,
                      }}
                    >
                      {failedTrackCount} FAILED
                    </span>
                  )}
                  {elapsed && (
                    <div
                      title="Elapsed time"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        fontFamily: sv.mono,
                        fontSize: 11,
                        color: sv.inkDim,
                      }}
                    >
                      <Clock size={12} />
                      <span className="sv-tnum">{elapsed}</span>
                    </div>
                  )}
                  {!!onReportBug && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onReportBug(); }}
                      title="Report a bug for this job"
                      aria-label="Report a bug for this job"
                      data-testid="disc-card-report-bug"
                      style={{
                        opacity: isHovered ? 1 : 0,
                        pointerEvents: isHovered ? "auto" : "none",
                        transition: "opacity 120ms ease-out",
                        width: 30,
                        height: 30,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "transparent",
                        border: `1px solid ${isHovered ? sv.red : "transparent"}`,
                        color: sv.red,
                        cursor: "pointer",
                        flexShrink: 0,
                      }}
                    >
                      <Bug size={14} />
                    </button>
                  )}
                  <StateIndicator state={disc.state} />
                  <ActionButtons
                    state={disc.state}
                    isHovered={isHovered}
                    onCancel={onCancel}
                    onReview={onReview}
                    onReIdentify={onReIdentify}
                    onAdvance={onAdvance}
                    emphasizeReIdentify={showIdentityReview}
                  />
                </div>
              </div>

              {/* Identify CTA — the non-modal affordance for a disc that needs a
                  name or season before it can proceed (P13). The prompt no longer
                  auto-opens over the dashboard when other jobs are active; this
                  persistent, prominent button is how the user opens it on demand
                  (and recovers after dismissing it). */}
              {onIdentify && (
                <motion.button
                  type="button"
                  onClick={onIdentify}
                  data-testid="disccard-identify-cta"
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    width: "100%",
                    marginBottom: 16,
                    padding: "12px 16px",
                    fontFamily: sv.mono,
                    fontSize: 12,
                    fontWeight: 700,
                    letterSpacing: "0.18em",
                    textTransform: "uppercase",
                    color: sv.cyanHi,
                    border: `1px solid ${sv.cyan}`,
                    background: `${sv.cyan}1f`,
                    boxShadow: `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`,
                    cursor: "pointer",
                  }}
                >
                  <IcoDisc size={14} />
                  <span>{identifyLabel ?? "Identify disc"} →</span>
                </motion.button>
              )}

              {/* Ambiguous / unconfirmed identity — the episode review queue can't
                  help until the show is confirmed, so point the user at "Wrong
                  title?" instead and explain why (reuses the backend's
                  review_reason sentence). */}
              {showIdentityReview && (
                <div
                  role="alert"
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    marginBottom: 16,
                    padding: "10px 12px",
                    border: `1px solid ${sv.yellow}99`,
                    borderLeft: `3px solid ${sv.yellow}`,
                    background: `${sv.yellow}14`,
                    fontFamily: sv.mono,
                    fontSize: 12,
                    lineHeight: 1.45,
                    letterSpacing: "0.02em",
                    color: sv.yellow,
                  }}
                >
                  <span aria-hidden style={{ flexShrink: 0 }}>⚠</span>
                  <span>
                    {disc.reviewReason ||
                      "This disc's identity isn't confirmed yet."}{" "}
                    Use "Wrong title?" to pick the correct show.
                  </span>
                </div>
              )}

              {/* No reference subtitles — loud + actionable while the job is still
                  actionable. Hidden once completed (e.g. user assigned manually). */}
              {disc.subtitleStatus === 'failed' && disc.state !== 'completed' && (
                <div
                  role="alert"
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    marginBottom: 16,
                    padding: "10px 12px",
                    border: `1px solid ${sv.red}`,
                    borderLeft: `3px solid ${sv.red}`,
                    background: "rgba(255, 77, 79, 0.08)",
                    fontFamily: sv.mono,
                    fontSize: 12,
                    lineHeight: 1.45,
                    letterSpacing: "0.02em",
                    color: sv.red,
                  }}
                >
                  <span aria-hidden style={{ flexShrink: 0 }}>⚠</span>
                  <span>
                    {disc.subtitleError ||
                      "No subtitles found — episode matching can't run. Open to assign episodes manually."}
                  </span>
                </div>
              )}

              {/* TMDB degraded-mode warning — active jobs only, and only for a
                  per-job reason (covers a configured-but-REJECTED key, which the
                  global flag can't see, #243). The generic "not configured" case
                  is deliberately NOT repeated here: the dashboard-level banner
                  owns that message, and echoing it on every card stacked N+1
                  identical warnings on one screen. */}
              {isActive && disc.tmdbDegradedReason && (
                <div
                  role="alert"
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    marginBottom: 16,
                    padding: "10px 12px",
                    border: `1px solid ${sv.amber}`,
                    borderLeft: `3px solid ${sv.amber}`,
                    background: "rgba(252, 211, 77, 0.08)",
                    fontFamily: sv.mono,
                    fontSize: 12,
                    lineHeight: 1.45,
                    letterSpacing: "0.02em",
                    color: sv.amber,
                  }}
                >
                  <span aria-hidden style={{ flexShrink: 0 }}>⚠</span>
                  <span>
                    {disc.tmdbDegradedReason}{" "}
                    {onOpenSettings && (
                      <button
                        onClick={onOpenSettings}
                        style={{
                          fontFamily: "inherit",
                          fontSize: "inherit",
                          color: sv.amber,
                          textDecoration: "underline",
                          textUnderlineOffset: 2,
                          background: "none",
                          border: 0,
                          padding: 0,
                          cursor: "pointer",
                        }}
                      >
                        Configure token
                      </button>
                    )}
                  </span>
                </div>
              )}

              {/* Scanning / identifying — full disc-insert visualization */}
              {disc.state === "scanning" && (() => {
                // Map identifying-state job data to a phase. The backend doesn't
                // emit fine-grained phases yet, so we infer:
                //   - no detected_title → 'scan' (still reading structure)
                //   - has detected_title + known content_type → 'classify'
                const hasMatch = !!disc.title && disc.mediaType !== "unknown";
                const phase: DiscInsertPhase = hasMatch ? "classify" : "scan";
                const typeLabel =
                  disc.mediaType === "tv" ? "TV" : disc.mediaType === "movie" ? "MOVIE" : "UNKNOWN";
                const meta = [typeLabel, disc.discLabel].filter(Boolean).join(" · ");
                return (
                  <SvDiscInsert
                    phase={phase}
                    driveLabel={disc.discLabel ? `Drive · ${disc.discLabel}` : "Drive · scanning"}
                    driveMeta={disc.discLabel ?? "—"}
                    bestMatch={hasMatch ? disc.title : undefined}
                    bestMatchMeta={hasMatch ? meta : undefined}
                  />
                );
              })()}

              {/* ISO archiving */}
              {disc.state === "archiving_iso" && disc.isoProgress !== undefined && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      fontFamily: sv.mono,
                      fontSize: 12,
                      color: sv.magenta,
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                    }}
                  >
                    <Database size={14} />
                    <span>› ARCHIVING TO ISO…</span>
                  </div>
                  <SvProgressBar progress={disc.isoProgress} color="magenta" label="ISO ARCHIVE" />
                </div>
              )}

              {/* Ripping */}
              {disc.state === "ripping" && disc.tracks && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <SvProgressBar progress={disc.progress} color="cyan" label="OVERALL PROGRESS" />
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(3, 1fr)",
                      gap: 16,
                    }}
                  >
                    {disc.currentSpeed && <SvStat label="SPEED" value={disc.currentSpeed} />}
                    {disc.etaSeconds !== undefined && (
                      <SvStat label="ETA" value={formatEta(disc.etaSeconds)} />
                    )}
                    <SvStat
                      label="TRACKS"
                      value={`${doneTrackCount}/${totalTrackCount}`}
                      color={sv.yellow}
                    />
                  </div>
                  <TrackGrid tracks={disc.tracks} />
                </div>
              )}

              {/* Matching */}
              {disc.state === "matching" && disc.tracks && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {disc.conflictStatus ? (
                    <div
                      data-testid="deep-rematch-banner"
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 10,
                        padding: "10px 12px",
                        border: `1px solid ${sv.magenta}66`,
                        background: `${sv.magenta}10`,
                      }}
                    >
                      <motion.div
                        animate={{ rotate: 360 }}
                        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                        style={{ flexShrink: 0, marginTop: 1 }}
                      >
                        <IcoRetry size={14} color={sv.magenta} />
                      </motion.div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 12,
                            fontWeight: 700,
                            color: sv.magenta,
                            letterSpacing: "0.08em",
                            textTransform: "uppercase",
                          }}
                        >
                          {disc.conflictStatus}
                        </div>
                        <div
                          style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            color: sv.inkDim,
                            marginTop: 4,
                            letterSpacing: "0.02em",
                          }}
                        >
                          Auto-resolving without manual review — each pass scans more of the
                          track. This can take a few minutes per pass.
                        </div>
                      </div>
                    </div>
                  ) : (
                    <PulseCaption
                      color={sv.amber}
                      extra={
                        disc.subtitleStatus === 'downloading' ? (
                          <span style={{ color: sv.cyan, fontSize: 10 }}>
                            (downloading subtitles)
                          </span>
                        ) : undefined
                      }
                    >
                      › MATCHING EPISODES…
                    </PulseCaption>
                  )}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(3, 1fr)",
                      gap: 16,
                    }}
                  >
                    <SvStat
                      label="MATCHED"
                      value={`${doneTrackCount}/${totalTrackCount}`}
                      color={sv.green}
                    />
                    <SvStat
                      label={disc.conflictStatus ? "RE-MATCHING" : "IN PROGRESS"}
                      value={String(disc.tracks.filter(t => t.state === "matching").length)}
                      color={disc.conflictStatus ? sv.magenta : sv.amber}
                    />
                    <SvStat
                      label="PENDING"
                      value={String(disc.tracks.filter(t => t.state === "pending").length)}
                      color={sv.inkDim}
                    />
                  </div>
                  <TrackGrid tracks={disc.tracks} conflictStatus={disc.conflictStatus} />
                </div>
              )}

              {/* Organizing */}
              {disc.state === "organizing" && disc.tracks && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {/* TV / multi-track: a real count-based bar (Organizing N of M).
                      Single-file movie: an indeterminate pulse — a 0/1 bar would
                      be meaningless for one large file moving over the NAS. */}
                  {disc.mediaType === "tv" || disc.tracks.length > 1 ? (
                    <SvProgressBar
                      progress={
                        disc.tracks.length
                          ? (disc.tracks.filter(t => t.organizedTo).length / disc.tracks.length) * 100
                          : 0
                      }
                      color="purple"
                      label="ORGANIZING TO LIBRARY"
                    />
                  ) : (
                    <PulseCaption color={sv.purple}>
                      › ORGANIZING TO LIBRARY…
                    </PulseCaption>
                  )}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(2, 1fr)",
                      gap: 16,
                    }}
                  >
                    <SvStat
                      label="ORGANIZED"
                      value={`${disc.tracks.filter(t => t.organizedTo).length}/${disc.tracks.length}`}
                      color={sv.green}
                    />
                    <SvStat
                      label="REMAINING"
                      value={String(disc.tracks.filter(t => !t.organizedTo).length)}
                      color={sv.purple}
                    />
                  </div>
                  <TrackGrid tracks={disc.tracks} />
                </div>
              )}

              {/* Legacy processing fallback */}
              {disc.state === "processing" && disc.tracks && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <PulseCaption color={sv.amber}>
                    › PROCESSING…
                  </PulseCaption>
                  <TrackGrid tracks={disc.tracks} />
                </div>
              )}

              {/* Completed */}
              {disc.state === "completed" && (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontFamily: sv.mono,
                    fontSize: 12,
                    letterSpacing: "0.2em",
                    color: sv.green,
                    textTransform: "uppercase",
                  }}
                >
                  <CheckCircle2 size={14} />
                  <span>› ARCHIVED TO LIBRARY</span>
                </div>
              )}
            </div>
          </div>
        </SvPanel>
      </motion.div>
    );
  });

DiscCardComponent.displayName = 'DiscCard';

export const DiscCard = React.memo(DiscCardComponent);

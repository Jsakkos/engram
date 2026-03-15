import React from "react";
import { motion } from "motion/react";
import { CheckCircle2, Clock, Database, Disc } from "lucide-react";
import { CyberpunkProgressBar } from "./CyberpunkProgressBar";
import { StateIndicator } from "./StateIndicator";
import { TrackGrid } from "./TrackGrid";
import { usePosterImage } from "./DiscCard/hooks/usePosterImage";
import { MediaTypeBadge } from "./DiscCard/MediaTypeBadge";
import { DiscMetadata } from "./DiscCard/DiscMetadata";
import { ActionButtons } from "./DiscCard/ActionButtons";
import { useElapsedTime } from "../hooks/useElapsedTime";

export type MediaType = "movie" | "tv" | "unknown";
export type DiscState = "idle" | "scanning" | "archiving_iso" | "ripping" | "matching" | "organizing" | "processing" | "completed" | "error";
export type TrackState = "pending" | "ripping" | "matching" | "matched" | "failed" | "completed";

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

  // Matching data
  matchCandidates?: MatchCandidate[];
  finalMatch?: string;
  finalMatchConfidence?: number;  // Confidence score of the final match
  finalMatchVotes?: number;        // Vote count of the final match
  finalMatchTargetVotes?: number;  // Target vote count (usually 4)

  // File tracking
  outputFilename?: string;         // Original rip filename
  organizedFrom?: string;          // Source path before organization
  organizedTo?: string;            // Final destination path
  isExtra?: boolean;               // Extra content flag

  // Quality metadata
  videoResolution?: string;        // "1080p", "4K", etc.
  edition?: string;                // "Extended", "Theatrical", etc.

  // Size tracking
  fileSizeBytes?: number;          // Actual file size
  expectedSizeBytes?: number;      // Expected size (for rip progress)
  actualSizeBytes?: number;        // Current size during rip
  chapterCount?: number;           // Number of chapters

  // Error info
  errorMessage?: string;           // Error reason for failed tracks
}

export interface DiscData {
  id: string;
  title: string;
  subtitle?: string;
  discLabel?: string;
  coverUrl: string;
  mediaType: MediaType;
  state: DiscState;
  progress: number;

  // ISO archival
  isoProgress?: number;

  // Tracks (parallel processing)
  tracks?: Track[];

  // Stats
  currentSpeed?: string;
  etaSeconds?: number;

  // Subtitle status for warning display
  subtitleStatus?: string;

  // Timing
  startedAt?: string;

  // Review flag
  needsReview?: boolean;
}

interface DiscCardProps {
  disc: DiscData;
  onCancel?: () => void;
  onReview?: () => void;
}

const stateColors = {
  idle: { from: "#64748b", to: "#94a3b8" },
  scanning: { from: "#06b6d4", to: "#22d3ee" }, // cyan
  archiving_iso: { from: "#8b5cf6", to: "#a78bfa" },
  ripping: { from: "#ec4899", to: "#f472b6" }, // magenta
  matching: { from: "#f59e0b", to: "#fbbf24" }, // amber
  organizing: { from: "#8b5cf6", to: "#a78bfa" }, // violet
  processing: { from: "#f59e0b", to: "#fbbf24" }, // amber — legacy fallback
  completed: { from: "#10b981", to: "#34d399" },
  error: { from: "#ef4444", to: "#f87171" },
};

function formatEta(seconds?: number): string {
  if (!seconds) return "—";
  if (seconds < 60) return "< 1 min";
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
}

const DiscCardComponent = React.forwardRef<HTMLDivElement, DiscCardProps>(
  ({ disc, onCancel, onReview }, ref) => {
    const colors = stateColors[disc.state];
    const [isHovered, setIsHovered] = React.useState(false);
    const posterUrl = usePosterImage(disc.id, disc.title);
    const isActive = !['completed', 'error', 'idle'].includes(disc.state);
    const elapsed = useElapsedTime(isActive ? disc.startedAt : undefined);

    return (
      <motion.div
        ref={ref}
        layout
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -20 }}
        onHoverStart={() => setIsHovered(true)}
        onHoverEnd={() => setIsHovered(false)}
        className="relative overflow-hidden rounded-lg bg-navy-800/80 border border-cyan-500/20 shadow-2xl"
        style={{
          boxShadow: `0 0 15px rgba(6, 182, 212, 0.15), 0 0 30px rgba(236, 72, 153, 0.05), inset 0 0 20px rgba(6, 182, 212, 0.05)`,
        }}
      >
        {/* Scanline effect */}
        <div className="absolute inset-0 pointer-events-none opacity-5">
          <div
            className="h-full w-full"
            style={{
              backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(6, 182, 212, 0.5) 2px, rgba(6, 182, 212, 0.5) 4px)",
            }}
          />
        </div>

        {/* Animated corner accents */}
        <motion.div
          className="absolute top-0 left-0 w-8 h-8 border-t-2 border-l-2 border-cyan-400"
          animate={{ opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
        <motion.div
          className="absolute top-0 right-0 w-8 h-8 border-t-2 border-r-2 border-magenta-400"
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
        <motion.div
          className="absolute bottom-0 left-0 w-8 h-8 border-b-2 border-l-2 border-magenta-400"
          animate={{ opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 2, repeat: Infinity, delay: 1 }}
        />
        <motion.div
          className="absolute bottom-0 right-0 w-8 h-8 border-b-2 border-r-2 border-cyan-400"
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ duration: 2, repeat: Infinity, delay: 1 }}
        />

        {/* Glowing background gradient */}
        <motion.div
          className="absolute inset-0 opacity-5"
          animate={{
            background: [
              `radial-gradient(circle at 0% 0%, ${colors.from} 0%, transparent 50%)`,
              `radial-gradient(circle at 100% 100%, ${colors.to} 0%, transparent 50%)`,
              `radial-gradient(circle at 0% 0%, ${colors.from} 0%, transparent 50%)`,
            ],
          }}
          transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
        />

        <div className="relative p-4 sm:p-6">
          <div className="flex gap-4 sm:gap-6">
            {/* Cover Art with holographic effect */}
            <motion.div
              className="relative flex-shrink-0 w-24 h-24 sm:w-40 sm:h-40 overflow-hidden"
              style={{
                clipPath: "polygon(0% 0%, 90% 0%, 100% 10%, 100% 100%, 10% 100%, 0% 90%)",
              }}
              whileHover={{ scale: 1.05 }}
              transition={{ type: "spring", stiffness: 300 }}
            >
              {/* Neon border glow */}
              <div className="absolute inset-0 bg-gradient-to-br from-cyan-400 via-magenta-500 to-yellow-400 opacity-50 blur-sm" />

              <div className="absolute inset-0.5 overflow-hidden bg-navy-900 holo-sweep">
                {posterUrl ? (
                  <img
                    src={posterUrl}
                    alt={disc.title}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      // Fallback to disc icon on error
                      (e.target as HTMLImageElement).style.display = 'none';
                    }}
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-navy-700 to-navy-900">
                    <Disc className="w-8 h-8 sm:w-16 sm:h-16 text-cyan-400/30" />
                  </div>
                )}

                {/* Holographic overlay */}
                <motion.div
                  className="absolute inset-0 bg-gradient-to-br from-cyan-400/20 via-transparent to-magenta-400/20"
                  animate={{
                    opacity: [0.3, 0.6, 0.3],
                  }}
                  transition={{ duration: 3, repeat: Infinity }}
                />

                {/* State overlay icon */}
                {["scanning", "archiving_iso", "ripping", "matching", "organizing", "processing"].includes(disc.state) && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                    <motion.div
                      animate={{ rotate: 360 }}
                      transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                    >
                      <Disc className="w-8 h-8 sm:w-12 sm:h-12 text-cyan-400" style={{ filter: "drop-shadow(0 0 8px rgba(6, 182, 212, 0.8))" }} />
                    </motion.div>
                  </div>
                )}

                {disc.state === "completed" && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                    <CheckCircle2 className="w-8 h-8 sm:w-12 sm:h-12 text-green-400" style={{ filter: "drop-shadow(0 0 8px rgba(16, 185, 129, 0.8))" }} />
                  </div>
                )}
              </div>

              {/* Media type badge */}
              <div className="absolute top-2 left-2 z-10">
                <MediaTypeBadge mediaType={disc.mediaType} />
              </div>
            </motion.div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              {/* Header */}
              <div className="flex items-start justify-between gap-4 mb-4">
                <DiscMetadata
                  title={disc.title}
                  subtitle={disc.subtitle}
                  discLabel={disc.discLabel}
                />
                <div className="flex items-center gap-2">
                  {disc.tracks && disc.tracks.filter(t => t.state === 'failed').length > 0 && (
                    <span className="text-red-400 text-xs font-mono" title="Some tracks failed during ripping">
                      {disc.tracks.filter(t => t.state === 'failed').length} FAILED
                    </span>
                  )}
                  {disc.subtitleStatus === 'failed' && (
                    <div className="text-yellow-500 text-lg" title="Subtitle download failed">
                      ⚠️
                    </div>
                  )}
                  {elapsed && (
                    <div className="flex items-center gap-1 text-xs font-mono text-slate-400" title="Elapsed time">
                      <Clock className="w-3 h-3" />
                      <span>{elapsed}</span>
                    </div>
                  )}
                  <StateIndicator state={disc.state} />
                  <ActionButtons
                    state={disc.state}
                    isHovered={isHovered}
                    onCancel={onCancel}
                    onReview={onReview}
                  />
                </div>
              </div>

              {/* Scanning state */}
              {disc.state === "scanning" && (
                <div className="flex items-center gap-3 text-sm text-cyan-400 font-mono">
                  <motion.div
                    animate={{ opacity: [0.4, 1, 0.4] }}
                    transition={{ duration: 1.5, repeat: Infinity }}
                  >
                    &gt; SCANNING DISC STRUCTURE...
                  </motion.div>
                </div>
              )}

              {/* ISO Archiving state */}
              {disc.state === "archiving_iso" && disc.isoProgress !== undefined && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm text-magenta-400 font-mono">
                    <Database className="w-4 h-4" />
                    <span>&gt; ARCHIVING TO ISO...</span>
                  </div>
                  <CyberpunkProgressBar progress={disc.isoProgress} color="magenta" label="ISO ARCHIVE" />
                </div>
              )}

              {/* Ripping state - show track grid with speed/ETA */}
              {disc.state === "ripping" && disc.tracks && (
                <>
                  <CyberpunkProgressBar progress={disc.progress} color="cyan" label="OVERALL PROGRESS" />

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-4 mt-3 sm:mt-4 font-mono">
                    {disc.currentSpeed && (
                      <div className="flex flex-col">
                        <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                          &gt; SPEED
                        </span>
                        <span className="text-sm font-bold text-cyan-400" style={{ textShadow: "0 0 8px rgba(6, 182, 212, 0.8)" }}>
                          {disc.currentSpeed}
                        </span>
                      </div>
                    )}

                    {disc.etaSeconds !== undefined && (
                      <div className="flex flex-col">
                        <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                          &gt; ETA
                        </span>
                        <span className="text-sm font-bold text-cyan-400" style={{ textShadow: "0 0 8px rgba(6, 182, 212, 0.8)" }}>
                          {formatEta(disc.etaSeconds)}
                        </span>
                      </div>
                    )}

                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; TRACKS
                      </span>
                      <span className="text-sm font-bold text-yellow-400" style={{ textShadow: "0 0 8px rgba(250, 204, 21, 0.8)" }}>
                        {disc.tracks.filter(t => ["matched", "completed"].includes(t.state)).length}/{disc.tracks.length}
                      </span>
                    </div>
                  </div>

                  {/* Track Grid */}
                  <TrackGrid tracks={disc.tracks} />
                </>
              )}

              {/* Matching state - show track grid with match progress */}
              {disc.state === "matching" && disc.tracks && (
                <>
                  <div className="flex items-center gap-3 text-sm text-amber-400 font-mono mb-3">
                    <motion.div
                      animate={{ opacity: [0.4, 1, 0.4] }}
                      transition={{ duration: 1.5, repeat: Infinity }}
                    >
                      &gt; MATCHING EPISODES...
                    </motion.div>
                    {disc.subtitleStatus === 'downloading' && (
                      <span className="text-xs text-cyan-400">(downloading subtitles)</span>
                    )}
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-4 font-mono">
                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; MATCHED
                      </span>
                      <span className="text-sm font-bold text-green-400" style={{ textShadow: "0 0 8px rgba(16, 185, 129, 0.8)" }}>
                        {disc.tracks.filter(t => ["matched", "completed"].includes(t.state)).length}/{disc.tracks.length}
                      </span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; IN PROGRESS
                      </span>
                      <span className="text-sm font-bold text-amber-400" style={{ textShadow: "0 0 8px rgba(245, 158, 11, 0.8)" }}>
                        {disc.tracks.filter(t => t.state === "matching").length}
                      </span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; PENDING
                      </span>
                      <span className="text-sm font-bold text-slate-400" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        {disc.tracks.filter(t => t.state === "pending").length}
                      </span>
                    </div>
                  </div>

                  {/* Track Grid */}
                  <TrackGrid tracks={disc.tracks} />
                </>
              )}

              {/* Organizing state - show file organization progress */}
              {disc.state === "organizing" && disc.tracks && (
                <>
                  <div className="flex items-center gap-3 text-sm text-violet-400 font-mono mb-3">
                    <motion.div
                      animate={{ opacity: [0.4, 1, 0.4] }}
                      transition={{ duration: 1.5, repeat: Infinity }}
                    >
                      &gt; ORGANIZING TO LIBRARY...
                    </motion.div>
                  </div>

                  <div className="grid grid-cols-2 gap-4 font-mono">
                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; ORGANIZED
                      </span>
                      <span className="text-sm font-bold text-green-400" style={{ textShadow: "0 0 8px rgba(16, 185, 129, 0.8)" }}>
                        {disc.tracks.filter(t => t.organizedTo).length}/{disc.tracks.length}
                      </span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-xs text-slate-400 uppercase tracking-wider mb-1 font-bold" style={{ textShadow: "0 0 4px rgba(148, 163, 184, 0.5)" }}>
                        &gt; REMAINING
                      </span>
                      <span className="text-sm font-bold text-violet-400" style={{ textShadow: "0 0 8px rgba(139, 92, 246, 0.8)" }}>
                        {disc.tracks.filter(t => !t.organizedTo).length}
                      </span>
                    </div>
                  </div>

                  {/* Track Grid */}
                  <TrackGrid tracks={disc.tracks} />
                </>
              )}

              {/* Legacy processing state fallback */}
              {disc.state === "processing" && disc.tracks && (
                <>
                  <div className="flex items-center gap-3 text-sm text-amber-400 font-mono mb-3">
                    <motion.div
                      animate={{ opacity: [0.4, 1, 0.4] }}
                      transition={{ duration: 1.5, repeat: Infinity }}
                    >
                      &gt; PROCESSING...
                    </motion.div>
                  </div>
                  <TrackGrid tracks={disc.tracks} />
                </>
              )}

              {/* Completed state */}
              {disc.state === "completed" && (
                <div className="flex items-center gap-2 text-sm text-green-400 font-mono">
                  <CheckCircle2 className="w-4 h-4" />
                  <span>&gt; ARCHIVED TO LIBRARY</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </motion.div>
    );
  });

DiscCardComponent.displayName = 'DiscCard';

// Memoize component to prevent unnecessary re-renders
export const DiscCard = React.memo(DiscCardComponent);

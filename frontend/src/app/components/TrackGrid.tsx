import React from "react";
import { motion } from "motion/react";
import { Loader2, CheckCircle2, AlertTriangle, Vote } from "lucide-react";
import type { Track } from "./DiscCard";

interface TrackGridProps {
  tracks: Track[];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

const stateConfig = {
  pending: {
    label: "PENDING",
    color: "border-slate-700/50 bg-slate-900/30",
    textColor: "text-slate-500",
    icon: null,
  },
  ripping: {
    label: "RIPPING",
    color: "border-cyan-500/50 bg-cyan-950/30",
    textColor: "text-cyan-400",
    icon: Loader2,
    glow: "rgba(6, 182, 212, 0.3)",
  },
  matching: {
    label: "MATCHING",
    color: "border-yellow-500/50 bg-yellow-950/30",
    textColor: "text-yellow-400",
    icon: Vote,
    glow: "rgba(250, 204, 21, 0.3)",
  },
  matched: {
    label: "MATCHED",
    color: "border-green-500/50 bg-green-950/30",
    textColor: "text-green-400",
    icon: CheckCircle2,
    glow: "rgba(16, 185, 129, 0.3)",
  },
  failed: {
    label: "FAILED",
    color: "border-red-500/50 bg-red-950/30",
    textColor: "text-red-400",
    icon: AlertTriangle,
    glow: "rgba(239, 68, 68, 0.3)",
  },
};

// Memoized component to prevent unnecessary re-renders
export const TrackGrid = React.memo(function TrackGrid({ tracks }: TrackGridProps) {
  return (
    <div className="mt-4 space-y-2">
      <div className="text-xs text-cyan-400 uppercase tracking-wider font-mono font-bold mb-3">
        &gt; TRACK STATUS
      </div>
      
      <div className="grid grid-cols-2 gap-2">
        {tracks.map((track, index) => {
          const config = stateConfig[track.state];
          const Icon = config.icon;
          
          return (
            <motion.div
              key={track.id}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: index * 0.05 }}
              className={`relative border-2 ${config.color} p-3 overflow-hidden group cursor-pointer`}
              style={{
                boxShadow: 'glow' in config ? `0 0 10px ${config.glow}` : "none",
              }}
            >
              {/* Scanlines */}
              <div 
                className="absolute inset-0 opacity-10 pointer-events-none"
                style={{
                  backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,255,255,0.1) 2px, rgba(255,255,255,0.1) 4px)",
                }}
              />
              
              {/* Corner accents */}
              <div className="absolute top-0 right-0 w-2 h-2 border-t border-r border-white/30" />
              <div className="absolute bottom-0 left-0 w-2 h-2 border-b border-l border-white/30" />
              
              <div className="relative">
                {/* Header */}
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex-1 min-w-0">
                    {/* Only show track number if no meaningful title */}
                    {track.title.startsWith('Track ') && (
                      <div className="text-xs text-slate-400 font-mono mb-1">TRACK {index + 1}</div>
                    )}
                    <div className={`text-sm font-bold ${config.textColor} truncate font-mono`}>
                      {track.title}
                    </div>
                    {/* Always show duration */}
                    {track.duration && (
                      <div className="text-xs text-slate-300 font-mono mt-1">{track.duration}</div>
                    )}

                    {/* Quality badges */}
                    {(track.videoResolution || track.edition || track.isExtra) && (
                      <div className="flex gap-1 mt-1">
                        {track.videoResolution && (
                          <span className="px-1.5 py-0.5 bg-cyan-500/20 border border-cyan-500/30 text-cyan-400 text-[10px] font-bold tracking-wider">
                            {track.videoResolution}
                          </span>
                        )}
                        {track.edition && (
                          <span className="px-1.5 py-0.5 bg-magenta-500/20 border border-magenta-500/30 text-magenta-400 text-[10px] font-bold tracking-wider">
                            {track.edition}
                          </span>
                        )}
                        {track.isExtra && (
                          <span className="px-1.5 py-0.5 bg-yellow-500/20 border border-yellow-500/30 text-yellow-400 text-[10px] font-bold tracking-wider">
                            EXTRA
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  
                  {/* Icon */}
                  {Icon && (
                    <motion.div
                      animate={
                        track.state === "ripping" || track.state === "matching"
                          ? { rotate: 360 }
                          : {}
                      }
                      transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                    >
                      <Icon className={`w-4 h-4 ${config.textColor}`} />
                    </motion.div>
                  )}
                </div>
                
                {/* Progress bar for ripping - with byte-level progress */}
                {track.state === "ripping" && (
                  <div className="mb-2">
                    <div className="h-1.5 bg-black border border-white/20 overflow-hidden">
                      <motion.div
                        className="h-full bg-gradient-to-r from-cyan-600 to-yellow-400"
                        initial={{ width: 0 }}
                        animate={{
                          width: `${
                            track.expectedSizeBytes && track.actualSizeBytes
                              ? (track.actualSizeBytes / track.expectedSizeBytes) * 100
                              : track.progress
                          }%`
                        }}
                        transition={{ duration: 0.3 }}
                        style={{
                          boxShadow: "0 0 10px rgba(6, 182, 212, 0.6)",
                        }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-xs text-slate-500 font-mono">{config.label}</span>
                      {track.expectedSizeBytes && track.actualSizeBytes ? (
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-slate-500 font-mono">
                            {formatBytes(track.actualSizeBytes)} / {formatBytes(track.expectedSizeBytes)}
                          </span>
                          <span className={`text-xs ${config.textColor} font-mono font-bold`}>
                            {((track.actualSizeBytes / track.expectedSizeBytes) * 100).toFixed(1)}%
                          </span>
                        </div>
                      ) : (
                        <span className={`text-xs ${config.textColor} font-mono font-bold`}>{track.progress.toFixed(1)}%</span>
                      )}
                    </div>
                  </div>
                )}

                {/* Output filename after ripping completes */}
                {track.outputFilename && !track.organizedTo && track.state !== "pending" && track.state !== "ripping" && (
                  <div className="text-xs text-slate-400 font-mono mt-1 truncate">{track.outputFilename}</div>
                )}

                {/* Progress bar for matching */}
                {track.state === "matching" && (
                  <div className="mb-2">
                    <div className="h-1.5 bg-black border border-white/20 overflow-hidden">
                      <motion.div
                        className="h-full bg-gradient-to-r from-cyan-500 via-magenta-500 to-yellow-400"
                        initial={{ width: 0 }}
                        animate={{ width: `${track.progress}%` }}
                        transition={{ duration: 0.3 }}
                        style={{
                          boxShadow: "0 0 10px rgba(6, 182, 212, 0.6)",
                        }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-xs text-slate-500 font-mono">{config.label}</span>
                      <span className={`text-xs ${config.textColor} font-mono font-bold`}>{track.progress.toFixed(1)}%</span>
                    </div>
                  </div>
                )}
                
                {/* Match candidates with voting */}
                {track.state === "matching" && track.matchCandidates && track.matchCandidates.length > 0 && (
                  <div className="mt-2 space-y-1.5">
                    {track.matchCandidates.slice(0, 3).map((candidate, idx) => (
                      <div key={idx} className="flex justify-between items-center text-xs gap-3">
                        <span className="text-yellow-300 font-mono truncate flex-1 font-bold">{candidate.episode}</span>
                        <span className="text-yellow-300 font-mono font-bold shrink-0">
                          {Math.min(candidate.votes, candidate.targetVotes)}/{candidate.targetVotes}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                
                {/* Final match with organization info */}
                {track.state === "matched" && track.finalMatch && (
                  <div className="mt-2 space-y-1">
                    <div className="flex justify-between items-center text-xs gap-3">
                      <span className="text-green-400 font-mono border-l-2 border-green-400 pl-2 flex-1">
                        → {track.finalMatch}
                      </span>
                      {track.finalMatchVotes !== undefined && (
                        <span className="text-green-300 font-mono font-bold shrink-0">
                          {Math.min(track.finalMatchVotes, track.finalMatchTargetVotes || 4)}/{track.finalMatchTargetVotes || 4}
                        </span>
                      )}
                    </div>

                    {/* Runner-ups */}
                    {track.matchCandidates && track.matchCandidates.length > 0 && (
                      <div className="space-y-0.5 pt-1">
                        {track.matchCandidates.filter(c => c.episode !== track.finalMatch).slice(0, 2).map((candidate, idx) => (
                          <div key={idx} className="flex justify-between items-center text-xs gap-3 pl-2 border-l-2 border-slate-700">
                            <span className="text-slate-500 font-mono truncate flex-1">{candidate.episode}</span>
                            <span className="text-slate-500 font-mono shrink-0">
                              {Math.min(candidate.votes, candidate.targetVotes)}/{candidate.targetVotes}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Organization paths */}
                    {track.organizedTo && (
                      <div className="pt-2 border-t border-green-500/20 space-y-1">
                        <div className="flex items-start gap-2">
                          <span className="text-xs text-slate-500 font-mono shrink-0">FROM:</span>
                          <span className="text-xs text-slate-400 font-mono break-all">
                            {track.outputFilename || track.organizedFrom}
                          </span>
                        </div>
                        <div className="flex items-start gap-2">
                          <span className="text-xs text-green-400 font-mono shrink-0 flex items-center gap-1">
                            <span>→</span>
                            {track.isExtra && <span className="text-yellow-400">[EXTRA]</span>}
                          </span>
                          <span className="text-xs text-green-400 font-mono break-all">
                            {track.organizedTo.split('/').slice(-2).join('/')}
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
});

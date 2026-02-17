import { motion } from "motion/react";
import { TrendingUp } from "lucide-react";
import type { MatchCandidate } from "./DiscCard";

interface MatchingVisualizerProps {
  candidates: MatchCandidate[];
  compact?: boolean;
}

export function MatchingVisualizer({ candidates, compact = false }: MatchingVisualizerProps) {
  // Sort by votes descending
  const sortedCandidates = [...candidates].sort((a, b) => b.votes - a.votes);

  if (compact) {
    return (
      <div className="mt-2 space-y-1">
        {sortedCandidates.slice(0, 2).map((candidate, index) => {
          const voteProgress = (candidate.votes / candidate.targetVotes) * 100;
          const isLeading = index === 0;
          
          return (
            <div key={candidate.episode} className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs font-mono truncate ${
                  isLeading ? "text-yellow-400" : "text-slate-500"
                }`}>
                  {candidate.episode}
                </span>
                <span className={`text-xs font-mono font-bold ${
                  isLeading ? "text-yellow-400" : "text-slate-600"
                }`}>
                  {candidate.votes}/{candidate.targetVotes}
                </span>
              </div>
              <div className="h-1 bg-black border border-white/10 overflow-hidden">
                <motion.div
                  className={`h-full ${isLeading ? "bg-yellow-400" : "bg-slate-600"}`}
                  initial={{ width: 0 }}
                  animate={{ width: `${voteProgress}%` }}
                  transition={{ duration: 0.5 }}
                  style={{
                    boxShadow: isLeading ? "0 0 8px rgba(250, 204, 21, 0.6)" : "none",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    );
  }
  
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-yellow-400 uppercase tracking-wider font-mono font-bold">
        <TrendingUp className="w-4 h-4" />
        <span>&gt; MATCH VOTING</span>
      </div>
      
      <div className="space-y-2">
        {sortedCandidates.map((candidate, index) => {
          const voteProgress = (candidate.votes / candidate.targetVotes) * 100;
          const isLeading = index === 0;
          const confidenceColor = 
            candidate.confidence >= 0.8 ? "text-green-400" :
            candidate.confidence >= 0.6 ? "text-yellow-400" :
            "text-red-400";
          
          return (
            <motion.div
              key={candidate.episode}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.1 }}
              className={`relative border ${
                isLeading ? "border-yellow-500/50 bg-yellow-950/20" : "border-slate-700/30 bg-slate-900/20"
              } p-3`}
              style={{
                boxShadow: isLeading ? "0 0 15px rgba(250, 204, 21, 0.2)" : "none",
              }}
            >
              {/* Leading indicator */}
              {isLeading && (
                <motion.div
                  className="absolute top-0 left-0 w-1 h-full bg-yellow-400"
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  style={{ boxShadow: "0 0 10px rgba(250, 204, 21, 0.8)" }}
                />
              )}
              
              <div className="relative pl-2">
                {/* Episode info */}
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm font-bold font-mono truncate ${
                      isLeading ? "text-yellow-400" : "text-slate-400"
                    }`}>
                      {candidate.episode}
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-slate-500 font-mono">
                        CONFIDENCE
                      </span>
                      <span className={`text-xs font-bold font-mono ${confidenceColor}`}>
                        {Math.round(candidate.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                  
                  {/* Vote count */}
                  <div className="text-right">
                    <div className={`text-lg font-bold font-mono ${
                      isLeading ? "text-yellow-400" : "text-slate-500"
                    }`}>
                      {candidate.votes}
                    </div>
                    <div className="text-xs text-slate-600 font-mono">
                      /{candidate.targetVotes}
                    </div>
                  </div>
                </div>
                
                {/* Vote progress bar */}
                <div className="h-2 bg-black border border-white/20 overflow-hidden">
                  <motion.div
                    className={`h-full ${
                      isLeading 
                        ? "bg-gradient-to-r from-yellow-400 to-yellow-500" 
                        : "bg-gradient-to-r from-slate-600 to-slate-700"
                    }`}
                    initial={{ width: 0 }}
                    animate={{ width: `${voteProgress}%` }}
                    transition={{ duration: 0.5, ease: "easeOut" }}
                    style={{
                      boxShadow: isLeading ? "0 0 10px rgba(250, 204, 21, 0.6)" : "none",
                    }}
                  >
                    {/* Shimmer effect for leading candidate */}
                    {isLeading && voteProgress < 100 && (
                      <motion.div
                        className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent"
                        animate={{ x: ["-100%", "200%"] }}
                        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                      />
                    )}
                  </motion.div>
                </div>
                
                <div className="flex items-center justify-between mt-1">
                  <span className="text-xs text-slate-600 font-mono">
                    VOTES
                  </span>
                  <span className={`text-xs font-bold font-mono ${
                    isLeading ? "text-yellow-400" : "text-slate-500"
                  }`}>
                    {Math.round(voteProgress)}%
                  </span>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

import { motion } from "motion/react";
import { TrendingUp } from "lucide-react";
import type { MatchCandidate } from "./DiscCard";
import { sv, SvBar, SvLabel } from "./synapse";

interface MatchingVisualizerProps {
  candidates: MatchCandidate[];
  compact?: boolean;
}

export function MatchingVisualizer({ candidates, compact = false }: MatchingVisualizerProps) {
  const sortedCandidates = [...candidates].sort((a, b) => b.votes - a.votes);

  if (compact) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
        {sortedCandidates.slice(0, 2).map((candidate, index) => {
          const voteProgress = candidate.votes / Math.max(1, candidate.targetVotes);
          const isLeading = index === 0;
          const accent = isLeading ? sv.yellow : sv.inkFaint;
          return (
            <div
              key={candidate.episode}
              style={{ display: "flex", flexDirection: "column", gap: 4 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 11,
                    color: accent,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {candidate.episode}
                </span>
                <span
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 11,
                    fontWeight: 700,
                    color: accent,
                  }}
                >
                  {candidate.votes}/{candidate.targetVotes}
                </span>
              </div>
              <SvBar
                value={voteProgress}
                color={accent}
                glow={isLeading}
                chunked={false}
                height={3}
              />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          color: sv.yellow,
          textTransform: "uppercase",
          letterSpacing: "0.18em",
          fontFamily: sv.mono,
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        <TrendingUp size={14} />
        <SvLabel size={10} color={sv.yellow}>
          Match voting
        </SvLabel>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sortedCandidates.map((candidate, index) => {
          const voteProgress = candidate.votes / Math.max(1, candidate.targetVotes);
          const isLeading = index === 0;
          const confidencePct = Math.round(candidate.confidence * 100);
          const confidenceColor =
            candidate.confidence >= 0.8
              ? sv.green
              : candidate.confidence >= 0.6
                ? sv.yellow
                : sv.red;
          const borderColor = isLeading ? `${sv.yellow}80` : sv.lineMid;
          const bgColor = isLeading ? `${sv.yellow}0a` : `${sv.bg2}80`;

          return (
            <motion.div
              key={candidate.episode}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.1 }}
              style={{
                position: "relative",
                border: `1px solid ${borderColor}`,
                background: bgColor,
                padding: 12,
                boxShadow: isLeading ? `0 0 15px ${sv.yellow}33` : "none",
              }}
            >
              {isLeading && (
                <motion.div
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: 2,
                    height: "100%",
                    background: sv.yellow,
                    boxShadow: `0 0 10px ${sv.yellow}cc`,
                  }}
                />
              )}

              <div style={{ position: "relative", paddingLeft: 8 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    gap: 8,
                    marginBottom: 8,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 13,
                        fontWeight: 700,
                        color: isLeading ? sv.yellow : sv.inkDim,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {candidate.episode}
                    </div>
                    <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 4 }}>
                      <span
                        style={{
                          fontFamily: sv.mono,
                          fontSize: 10,
                          color: sv.inkFaint,
                          letterSpacing: "0.18em",
                          textTransform: "uppercase",
                        }}
                      >
                        Confidence
                      </span>
                      <span
                        style={{
                          fontFamily: sv.mono,
                          fontSize: 11,
                          fontWeight: 700,
                          color: confidenceColor,
                        }}
                      >
                        {confidencePct}%
                      </span>
                    </div>
                  </div>

                  <div style={{ textAlign: "right" }}>
                    <div
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 18,
                        fontWeight: 700,
                        color: isLeading ? sv.yellow : sv.inkDim,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {candidate.votes}
                    </div>
                    <div
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        color: sv.inkFaint,
                      }}
                    >
                      /{candidate.targetVotes}
                    </div>
                  </div>
                </div>

                <SvBar
                  value={voteProgress}
                  color={isLeading ? sv.yellow : sv.inkFaint}
                  secondary={isLeading ? sv.amber : sv.inkDim}
                  glow={isLeading}
                  chunked={false}
                  height={4}
                />

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: 4,
                  }}
                >
                  <span
                    style={{
                      fontFamily: sv.mono,
                      fontSize: 10,
                      color: sv.inkFaint,
                      letterSpacing: "0.18em",
                      textTransform: "uppercase",
                    }}
                  >
                    Votes
                  </span>
                  <span
                    style={{
                      fontFamily: sv.mono,
                      fontSize: 10,
                      fontWeight: 700,
                      color: isLeading ? sv.yellow : sv.inkDim,
                    }}
                  >
                    {Math.round(voteProgress * 100)}%
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

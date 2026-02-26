import { motion } from "motion/react";
import type { DiscState } from "./DiscCard";

interface StateIndicatorProps {
  state: DiscState;
}

const stateConfig: Record<DiscState, { label: string; color: string; glow: string }> = {
  idle: { label: "IDLE", color: "text-slate-400", glow: "rgba(148, 163, 184, 0.5)" },
  scanning: { label: "SCANNING", color: "text-cyan-400", glow: "rgba(6, 182, 212, 0.8)" },
  archiving_iso: { label: "ARCHIVING", color: "text-purple-400", glow: "rgba(139, 92, 246, 0.8)" },
  ripping: { label: "RIPPING", color: "text-magenta-400", glow: "rgba(236, 72, 153, 0.8)" },
  processing: { label: "PROCESSING", color: "text-amber-400", glow: "rgba(245, 158, 11, 0.8)" },
  completed: { label: "COMPLETE", color: "text-green-400", glow: "rgba(16, 185, 129, 0.8)" },
  error: { label: "ERROR", color: "text-red-400", glow: "rgba(239, 68, 68, 0.8)" },
};

export function StateIndicator({ state }: StateIndicatorProps) {
  const config = stateConfig[state];
  
  return (
    <motion.div
      layout
      className="flex items-center gap-2 px-3 py-1.5 bg-black border border-cyan-500/30 font-mono"
    >
      {/* Animated dot */}
      <div className="relative flex items-center justify-center w-3 h-3">
        <motion.div
          className="absolute w-2 h-2 rounded-full"
          style={{ backgroundColor: config.glow }}
          animate={
            state !== "completed" && state !== "error" && state !== "idle"
              ? {
                  scale: [1, 1.5, 1],
                  opacity: [1, 0.5, 1],
                }
              : {}
          }
          transition={{
            duration: 1.5,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
        
        {/* Pulse rings for active states */}
        {state !== "completed" && state !== "error" && state !== "idle" && (
          <>
            <motion.div
              className="absolute w-2 h-2 rounded-full"
              style={{ backgroundColor: config.glow }}
              animate={{
                scale: [1, 2.5],
                opacity: [0.6, 0],
              }}
              transition={{
                duration: 1.5,
                repeat: Infinity,
                ease: "easeOut",
              }}
            />
            <motion.div
              className="absolute w-2 h-2 rounded-full"
              style={{ backgroundColor: config.glow }}
              animate={{
                scale: [1, 2.5],
                opacity: [0.6, 0],
              }}
              transition={{
                duration: 1.5,
                repeat: Infinity,
                ease: "easeOut",
                delay: 0.75,
              }}
            />
          </>
        )}
      </div>
      
      <span className={`text-xs font-bold ${config.color} uppercase tracking-wider`} style={{ textShadow: `0 0 8px ${config.glow}` }}>
        {config.label}
      </span>
    </motion.div>
  );
}

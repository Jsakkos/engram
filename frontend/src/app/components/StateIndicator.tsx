import { motion } from "motion/react";
import { Search, Disc3, Fingerprint, FolderOutput, CheckCircle2, AlertTriangle, Archive, Loader2 } from "lucide-react";
import type { DiscState } from "./DiscCard";

interface StateIndicatorProps {
  state: DiscState;
}

const stateConfig: Record<DiscState, { label: string; color: string; glow: string; icon: React.ElementType }> = {
  idle: { label: "IDLE", color: "text-slate-400", glow: "rgba(148, 163, 184, 0.5)", icon: Loader2 },
  scanning: { label: "SCANNING", color: "text-cyan-400", glow: "rgba(6, 182, 212, 0.8)", icon: Search },
  archiving_iso: { label: "ARCHIVING", color: "text-purple-400", glow: "rgba(139, 92, 246, 0.8)", icon: Archive },
  ripping: { label: "RIPPING", color: "text-magenta-400", glow: "rgba(236, 72, 153, 0.8)", icon: Disc3 },
  matching: { label: "MATCHING", color: "text-amber-400", glow: "rgba(245, 158, 11, 0.8)", icon: Fingerprint },
  organizing: { label: "ORGANIZING", color: "text-violet-400", glow: "rgba(139, 92, 246, 0.8)", icon: FolderOutput },
  processing: { label: "PROCESSING", color: "text-amber-400", glow: "rgba(245, 158, 11, 0.8)", icon: Loader2 },
  completed: { label: "COMPLETE", color: "text-green-400", glow: "rgba(16, 185, 129, 0.8)", icon: CheckCircle2 },
  error: { label: "ERROR", color: "text-red-400", glow: "rgba(239, 68, 68, 0.8)", icon: AlertTriangle },
};

export function StateIndicator({ state }: StateIndicatorProps) {
  const config = stateConfig[state];
  const Icon = config.icon;
  const isActive = state !== "completed" && state !== "error" && state !== "idle";

  return (
    <motion.div
      layout
      className="flex items-center gap-2 px-3 py-1.5 bg-navy-800/80 border border-cyan-500/20 font-mono rounded-md"
    >
      {/* Icon with optional spin */}
      <motion.div
        animate={
          isActive && (state === "ripping" || state === "scanning")
            ? { rotate: 360 }
            : {}
        }
        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
        className="relative"
      >
        <Icon className={`w-3.5 h-3.5 ${config.color}`} style={{ filter: `drop-shadow(0 0 4px ${config.glow})` }} />
      </motion.div>

      {/* Animated dot for active states */}
      {isActive && (
        <div className="relative flex items-center justify-center w-2 h-2">
          <motion.div
            className="absolute w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: config.glow }}
            animate={{ scale: [1, 1.5, 1], opacity: [1, 0.5, 1] }}
            transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
          />
          <motion.div
            className="absolute w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: config.glow }}
            animate={{ scale: [1, 2.5], opacity: [0.6, 0] }}
            transition={{ duration: 1.5, repeat: Infinity, ease: "easeOut" }}
          />
        </div>
      )}

      <span className={`text-xs font-bold ${config.color} uppercase tracking-wider`} style={{ textShadow: `0 0 8px ${config.glow}` }}>
        {config.label}
      </span>
    </motion.div>
  );
}

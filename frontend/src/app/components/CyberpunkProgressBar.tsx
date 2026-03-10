import { motion } from "motion/react";

interface CyberpunkProgressBarProps {
  progress: number;
  color: "cyan" | "magenta" | "yellow" | "green";
  label?: string;
}

const colorStyles = {
  cyan: {
    bg: "bg-cyan-500",
    glow: "rgba(6, 182, 212, 0.6)",
    text: "text-cyan-400",
    border: "border-cyan-500/30",
    tick: "bg-cyan-400/20",
  },
  magenta: {
    bg: "bg-magenta-500",
    glow: "rgba(236, 72, 153, 0.6)",
    text: "text-magenta-400",
    border: "border-magenta-500/30",
    tick: "bg-magenta-400/20",
  },
  yellow: {
    bg: "bg-yellow-400",
    glow: "rgba(250, 204, 21, 0.6)",
    text: "text-yellow-400",
    border: "border-yellow-500/30",
    tick: "bg-yellow-400/20",
  },
  green: {
    bg: "bg-green-500",
    glow: "rgba(16, 185, 129, 0.6)",
    text: "text-green-400",
    border: "border-green-500/30",
    tick: "bg-green-400/20",
  },
};

export function CyberpunkProgressBar({ progress, color, label }: CyberpunkProgressBarProps) {
  const style = colorStyles[color];

  return (
    <div className="relative">
      {/* Background track */}
      <div className={`h-3 bg-navy-800 border ${style.border} relative overflow-hidden rounded-sm`}>
        {/* Grid pattern background */}
        <div
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage: "repeating-linear-gradient(90deg, transparent, transparent 4px, rgba(255,255,255,0.1) 4px, rgba(255,255,255,0.1) 5px)",
          }}
        />

        {/* Tick marks at 25/50/75% */}
        {[25, 50, 75].map((tick) => (
          <div
            key={tick}
            className={`absolute top-0 bottom-0 w-px ${style.tick}`}
            style={{ left: `${tick}%` }}
          />
        ))}

        {/* Progress fill */}
        <motion.div
          className={`h-full ${style.bg} relative`}
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          style={{
            boxShadow: `0 0 ${8 + (progress / 10)}px ${style.glow}, inset 0 0 10px ${style.glow}`,
          }}
        >
          {/* Animated glitch bars */}
          {progress < 100 && (
            <>
              <motion.div
                className="absolute inset-y-0 right-0 w-1 bg-white"
                animate={{
                  opacity: [1, 0.3, 1],
                  scaleY: [1, 0.8, 1],
                }}
                transition={{
                  duration: 0.3,
                  repeat: Infinity,
                  repeatType: "reverse",
                }}
              />

              <motion.div
                className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent"
                animate={{
                  x: ["-100%", "200%"],
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  ease: "linear",
                }}
              />
            </>
          )}
        </motion.div>
      </div>

      {/* Label and percentage */}
      <div className="flex items-center justify-between mt-2">
        <span className={`text-xs ${style.text} uppercase tracking-wider font-mono font-bold`}>
          {label || "PROGRESS"}
        </span>
        <motion.span
          key={progress}
          initial={{ scale: 1.3, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className={`text-sm font-bold ${style.text} font-mono`}
          style={{ textShadow: `0 0 8px ${style.glow}` }}
        >
          {progress.toFixed(1)}%
        </motion.span>
      </div>
    </div>
  );
}

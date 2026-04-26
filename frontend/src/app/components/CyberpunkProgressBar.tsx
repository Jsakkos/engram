import { motion } from "motion/react";
import { sv } from "./synapse";

interface CyberpunkProgressBarProps {
  progress: number;
  color: "cyan" | "magenta" | "yellow" | "green";
  label?: string;
}

/**
 * Map color name → Synapse v2 token. Centralizes the per-color glow /
 * gradient bookkeeping that was previously duplicated across screens.
 */
const COLOR: Record<CyberpunkProgressBarProps["color"], { fg: string; secondary: string; glow: string }> = {
  cyan:    { fg: sv.cyan,    secondary: sv.cyanHi,    glow: sv.cyan    },
  magenta: { fg: sv.magenta, secondary: sv.magentaHi, glow: sv.magenta },
  yellow:  { fg: sv.yellow,  secondary: sv.amber,     glow: sv.yellow  },
  green:   { fg: sv.green,   secondary: sv.greenDim,  glow: sv.green   },
};

/**
 * Synapse v2 progress bar — sharp 90° corners, gradient fill, chunked
 * ticks, glow, and a sweep highlight while in-flight. Public prop
 * contract preserved so DiscCard / TrackGrid keep working unchanged.
 */
export function CyberpunkProgressBar({ progress, color, label }: CyberpunkProgressBarProps) {
  const c = COLOR[color];
  const value = Math.min(100, Math.max(0, progress));

  return (
    <div
      data-testid="sv-bar-progress"
      data-value={value}
      style={{ position: "relative" }}
    >
      {/* Track */}
      <div
        style={{
          position: "relative",
          height: 12,
          background: sv.bg2,
          border: `1px solid ${c.fg}44`,
          overflow: "hidden",
        }}
      >
        {/* Major tick lines at 25/50/75 */}
        {[25, 50, 75].map((t) => (
          <div
            key={t}
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: `${t}%`,
              width: 1,
              background: `${c.fg}33`,
            }}
          />
        ))}

        {/* Gradient fill */}
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${value}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          style={{
            position: "absolute",
            inset: "0 auto 0 0",
            height: "100%",
            background: `linear-gradient(90deg, ${c.fg}, ${c.secondary})`,
            boxShadow: `0 0 ${8 + value / 10}px ${c.glow}aa, inset 0 0 10px ${c.glow}66`,
          }}
        >
          {/* Live sweep highlight while in-flight */}
          {value < 100 && value > 0 && (
            <>
              <motion.div
                style={{
                  position: "absolute",
                  inset: "0 0 0 auto",
                  width: 2,
                  background: sv.ink,
                  opacity: 0.85,
                }}
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 0.3, repeat: Infinity, repeatType: "reverse" }}
              />
              <motion.div
                style={{
                  position: "absolute",
                  inset: 0,
                  background: `linear-gradient(90deg, transparent, ${sv.ink}33, transparent)`,
                }}
                animate={{ x: ["-100%", "200%"] }}
                transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
              />
            </>
          )}
        </motion.div>

        {/* Chunked tick overlay (10% repeating, low alpha) */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `repeating-linear-gradient(90deg, transparent 0 9%, ${sv.bg0}55 9% 10%)`,
            pointerEvents: "none",
          }}
        />
      </div>

      {/* Label + percentage */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 6,
        }}
      >
        <span
          style={{
            fontFamily: sv.mono,
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.20em",
            textTransform: "uppercase",
            color: c.fg,
          }}
        >
          {label || "PROGRESS"}
        </span>
        <motion.span
          key={Math.round(value)}
          initial={{ scale: 1.2, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="sv-tnum"
          style={{
            fontFamily: sv.mono,
            fontSize: 13,
            fontWeight: 700,
            color: c.fg,
            textShadow: `0 0 8px ${c.glow}`,
          }}
        >
          {value.toFixed(1)}%
        </motion.span>
      </div>
    </div>
  );
}

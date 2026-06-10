import { motion } from "motion/react";
import type { DiscState } from "./DiscCard";
import { sv } from "./synapse";
import { DISC_STATE_CONFIG } from "./discState";

interface StateIndicatorProps {
  state: DiscState;
}

/**
 * State pill — Synapse v2 Sv badge styling with a Lucide icon prefix.
 * Public prop contract is unchanged so existing callers keep working.
 * Label/color/icon mapping lives in discState.ts (shared with CompactList).
 */
export function StateIndicator({ state }: StateIndicatorProps) {
  const config = DISC_STATE_CONFIG[state];
  const Icon = config.icon;
  const isActive = state !== "completed" && state !== "error" && state !== "idle";
  const shouldSpin = state === "ripping" || state === "scanning";

  return (
    <span
      data-testid="sv-state-indicator"
      data-state={state}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 10px",
        background: `${config.color}10`,
        border: `1px solid ${config.color}55`,
        fontFamily: sv.mono,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.20em",
        textTransform: "uppercase",
        color: config.color,
        textShadow: `0 0 8px ${config.glow}66`,
      }}
    >
      <motion.span
        animate={shouldSpin ? { rotate: 360 } : {}}
        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
        style={{ display: "inline-flex", filter: `drop-shadow(0 0 4px ${config.glow}88)` }}
      >
        <Icon size={12} color={config.color} />
      </motion.span>

      {isActive && (
        <motion.span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: config.color,
            boxShadow: `0 0 6px ${config.glow}`,
          }}
          animate={{ opacity: [1, 0.4, 1] }}
          transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
        />
      )}

      <span>{config.label}</span>
    </span>
  );
}

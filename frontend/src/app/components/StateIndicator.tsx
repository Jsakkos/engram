import { motion } from "motion/react";
import { Search, Disc3, Fingerprint, FolderOutput, CheckCircle2, AlertTriangle, Archive, Loader2 } from "lucide-react";
import type { DiscState } from "./DiscCard";
import { sv, type SvBadgeState } from "./synapse";

interface StateIndicatorProps {
  state: DiscState;
}

interface StateConfig {
  label: string;
  badgeState: SvBadgeState;
  color: string;
  glow: string;
  icon: React.ElementType;
}

/**
 * Map domain DiscState → Synapse v2 badge state + label + iconography.
 * Single source of truth for how each pipeline phase reads visually.
 */
const STATE: Record<DiscState, StateConfig> = {
  idle:           { label: "IDLE",          badgeState: "idle",     color: sv.inkDim,   glow: sv.inkDim,   icon: Loader2 },
  scanning:       { label: "SCANNING",      badgeState: "scanning", color: sv.yellow,   glow: sv.yellow,   icon: Search },
  review_needed:  { label: "REVIEW NEEDED", badgeState: "review",   color: sv.yellow,   glow: sv.yellow,   icon: AlertTriangle },
  archiving_iso:  { label: "ARCHIVING",     badgeState: "matching", color: sv.purple,   glow: sv.purple,   icon: Archive },
  ripping:        { label: "RIPPING",       badgeState: "ripping",  color: sv.magenta,  glow: sv.magenta,  icon: Disc3 },
  matching:       { label: "MATCHING",      badgeState: "matching", color: sv.amber,    glow: sv.amber,    icon: Fingerprint },
  organizing:     { label: "ORGANIZING",    badgeState: "matching", color: sv.purple,   glow: sv.purple,   icon: FolderOutput },
  processing:     { label: "PROCESSING",    badgeState: "matching", color: sv.amber,    glow: sv.amber,    icon: Loader2 },
  completed:      { label: "COMPLETE",      badgeState: "complete", color: sv.green,    glow: sv.green,    icon: CheckCircle2 },
  error:          { label: "ERROR",         badgeState: "error",    color: sv.red,      glow: sv.red,      icon: AlertTriangle },
};

/**
 * State pill — Synapse v2 Sv badge styling with a Lucide icon prefix.
 * Public prop contract is unchanged so existing callers keep working.
 */
export function StateIndicator({ state }: StateIndicatorProps) {
  const config = STATE[state];
  const Icon = config.icon;
  const isActive = state !== "completed" && state !== "error" && state !== "idle";
  const shouldSpin = state === "ripping" || state === "scanning";
  const shouldPulse = isActive;

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

      {shouldPulse && (
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

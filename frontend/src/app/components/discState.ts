import {
  IcoIdle,
  IcoScan,
  IcoRipping,
  IcoMatching,
  IcoComplete,
  IcoError,
  IcoLibrary,
  IcoReview,
} from "./icons";
import type { DiscState } from "./DiscCard";
import { sv, type SvBadgeState } from "./synapse";

export interface StateConfig {
  label: string;
  badgeState: SvBadgeState;
  color: string;
  glow: string;
  icon: React.ElementType;
}

/**
 * Map domain DiscState → Synapse v2 badge state + label + iconography.
 * Single source of truth for how each pipeline phase reads visually —
 * consumed by the StateIndicator badge and the compact list rows.
 */
export const DISC_STATE_CONFIG: Record<DiscState, StateConfig> = {
  idle:           { label: "IDLE",          badgeState: "idle",     color: sv.inkDim,   glow: sv.inkDim,   icon: IcoIdle },
  scanning:       { label: "SCANNING",      badgeState: "scanning", color: sv.yellow,   glow: sv.yellow,   icon: IcoScan },
  review_needed:  { label: "REVIEW NEEDED", badgeState: "review",   color: sv.yellow,   glow: sv.yellow,   icon: IcoReview },
  archiving_iso:  { label: "ARCHIVING",     badgeState: "matching", color: sv.purple,   glow: sv.purple,   icon: IcoLibrary },
  ripping:        { label: "RIPPING",       badgeState: "ripping",  color: sv.magenta,  glow: sv.magenta,  icon: IcoRipping },
  matching:       { label: "MATCHING",      badgeState: "matching", color: sv.amber,    glow: sv.amber,    icon: IcoMatching },
  organizing:     { label: "ORGANIZING",    badgeState: "matching", color: sv.purple,   glow: sv.purple,   icon: IcoLibrary },
  processing:     { label: "PROCESSING",    badgeState: "matching", color: sv.amber,    glow: sv.amber,    icon: IcoMatching },
  completed:      { label: "COMPLETE",      badgeState: "complete", color: sv.green,    glow: sv.green,    icon: IcoComplete },
  error:          { label: "ERROR",         badgeState: "error",    color: sv.red,      glow: sv.red,      icon: IcoError },
};

/**
 * Human-readable label for a pipeline state ("review_needed" → "REVIEW NEEDED").
 * Reuses the badge config so list views and badges can never disagree.
 */
export function discStateLabel(state: DiscState): string {
  return DISC_STATE_CONFIG[state]?.label ?? state.replace(/_/g, " ").toUpperCase();
}

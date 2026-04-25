/**
 * Synapse v2 primitive component library.
 *
 * Source of truth (visual design): docs/design_handoff_synapse/synapse-v2/.
 * These components are pure-presentational. Data integration happens at
 * the call site (App.tsx, DiscCard.tsx, ReviewQueue.tsx, etc.).
 */
export { sv, accentColor, accentHi } from "./tokens";
export type { SvAccent } from "./tokens";

export { SvAtmosphere } from "./SvAtmosphere";
export { SvPanel } from "./SvPanel";
export { SvCorners } from "./SvCorners";
export { SvLabel } from "./SvLabel";
export { SvBar } from "./SvBar";
export { SvBadge } from "./SvBadge";
export type { SvBadgeState } from "./SvBadge";
export { SvRuler } from "./SvRuler";
export { SvAnimValue } from "./SvAnimValue";
export { SvTelemetryBand } from "./SvTelemetryBand";
export { SvMark } from "./SvMark";

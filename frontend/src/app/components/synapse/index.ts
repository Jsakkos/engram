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
export { SvBarChart } from "./SvBarChart";
export { SvBadge } from "./SvBadge";
export type { SvBadgeState } from "./SvBadge";
export { SvRuler } from "./SvRuler";
export { SvAnimValue } from "./SvAnimValue";
export { SvTelemetryBand } from "./SvTelemetryBand";
export { SvMark } from "./SvMark";
export { SvTopBar } from "./SvTopBar";
export { SvStatusBar } from "./SvStatusBar";
export { SvErrorState } from "./SvErrorState";
export type { SvErrorKind } from "./SvErrorState";
export { SvDiscInsert } from "./SvDiscInsert";
export type { DiscInsertPhase } from "./SvDiscInsert";
export { SvPageHeader } from "./SvPageHeader";
export { SvProgressBar } from "./SvProgressBar";
export { SvActionButton } from "./SvActionButton";
export type { SvActionButtonTone, SvActionButtonSize } from "./SvActionButton";
export { SvNotice } from "./SvNotice";
export type { SvNoticeTone } from "./SvNotice";

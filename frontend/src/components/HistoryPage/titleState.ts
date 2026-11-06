import type { SvBadgeState } from "../../app/components/synapse";

const TITLE_STATES: Record<string, { state: SvBadgeState; label: string }> = {
  completed: { state: "complete", label: "OK" },
  failed:    { state: "error",    label: "FAIL" },
  matched:   { state: "matched",  label: "MATCHED" },
  review:    { state: "review",   label: "REVIEW" },
  pending:   { state: "queued",   label: "PENDING" },
  ripping:   { state: "ripping",  label: "RIPPING" },
  matching:  { state: "matching", label: "MATCHING" },
};

/**
 * Badge for a track in History. A failed job never revisits its titles, so a
 * track still PENDING (or caught mid-RIPPING) on one will never rip: say so
 * rather than implying it is still queued (#713).
 */
export function titleStateBadge(
  state: string,
  jobState?: string,
): { state: SvBadgeState; label: string } {
  if (jobState === "failed" && (state === "pending" || state === "ripping")) {
    return { state: "idle", label: "NOT RIPPED" };
  }
  return TITLE_STATES[state] ?? { state: "idle", label: state.toUpperCase() };
}

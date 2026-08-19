/**
 * Presentation helpers for job state on the History page.
 *
 * History used to contain only completed and failed jobs, so the page rendered
 * state as a binary: anything not "completed" was drawn as a failure. Now that
 * a parked REVIEW_NEEDED job is reachable here, that fallback would libel it as
 * failed. These helpers keep the not-terminal case honest.
 */
import type { SvBadgeState } from "../app/components/synapse";

export function historyBadgeState(state: string): SvBadgeState {
  if (state === "completed") return "complete";
  if (state === "failed") return "error";
  if (state === "review_needed") return "review";
  return "idle";
}

export function historyTimelineLabel(state: string): string {
  if (state === "completed") return "Completed";
  if (state === "failed") return "Failed";
  // The row this labels carries completed_at, which stays null until a job
  // ends. Naming a finish event here would render "Finished: -" for a job
  // that is still running or parked in review.
  return "In progress";
}

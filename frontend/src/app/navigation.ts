/**
 * Top-nav item construction — shared by every page that renders `SvTopBar`.
 *
 * Centralizing this prevents the nav from drifting per-page (the cause of the
 * `/review` black screen: one page's REVIEW tab linked to a bare `/review`
 * path no route handled). All `to` values come from `config/routes`.
 */
import { FEATURES } from "../config/constants";
import { ROUTES, reviewPath } from "../config/routes";

export interface NavItem {
  label: string;
  to: string;
  /** Path prefix used for active-state detection. Defaults to `to` when absent. */
  activeWhen?: string;
  /** Numeric badge (yellow). Falsy = no badge. */
  badge?: number;
  /** Show the route in the nav. Default true. */
  show?: boolean;
  /** Render as an inert tab (no navigation). */
  disabled?: boolean;
  /** Tooltip explaining why the tab is disabled. */
  disabledHint?: string;
}

export interface NavState {
  /** Id of the first job awaiting review, if any. Drives the REVIEW deep-link. */
  firstReviewJobId?: number;
  /** Count shown on the REVIEW badge. */
  reviewCount?: number;
  /** Count shown on the CONTRIBUTE badge (DiscDB feature). */
  contributionPending?: number;
}

/**
 * Build the top-nav items.
 *
 * The REVIEW tab deep-links to the first job awaiting review so the tab lands
 * on real content. When nothing needs review the tab is disabled — NOT linked
 * to a bare `/review` (which renders nothing, the original black-screen bug)
 * and NOT to the dashboard (which navigated users away with no explanation
 * and gave two tabs the same destination).
 */
export function buildNavItems({
  firstReviewJobId,
  reviewCount = 0,
  contributionPending = 0,
}: NavState = {}): NavItem[] {
  return [
    { label: "DASHBOARD", to: ROUTES.HOME },
    {
      label: "REVIEW",
      to: firstReviewJobId ? reviewPath(firstReviewJobId) : ROUTES.HOME,
      activeWhen: ROUTES.REVIEW,
      badge: reviewCount,
      disabled: !firstReviewJobId,
      disabledHint: firstReviewJobId ? undefined : "No jobs awaiting review",
    },
    { label: "HISTORY", to: ROUTES.HISTORY },
    {
      label: "CONTRIBUTE",
      to: ROUTES.CONTRIBUTE,
      badge: contributionPending,
      show: FEATURES.DISCDB_CONTRIBUTE,
    },
  ];
}

/**
 * Route paths — single source of truth for React Router.
 *
 * `<Route path>` definitions, `<Link to>` targets, and `navigate()` calls must
 * all derive their paths from here. Defining routes and links as independent
 * string literals is what produced the `/review` black-screen bug: the nav
 * tab linked to a path no route handled, so the router rendered nothing.
 *
 * Use the path *patterns* for `<Route path>` and the path *builders* for
 * navigation. `routeExists()` backs the nav-integrity test that guards against
 * links pointing at undefined routes.
 */
import { matchPath } from "react-router-dom";
import { FEATURES } from "./constants";

/** Route path patterns — mirror these exactly in the `<Routes>` table. */
export const ROUTES = {
  HOME: "/",
  HISTORY: "/history",
  HISTORY_DETAIL: "/history/:jobId",
  CONTRIBUTE: "/contribute",
  /** Bare `/review` — redirects to the dashboard (no jobId to review). */
  REVIEW: "/review",
  REVIEW_DETAIL: "/review/:jobId",
} as const;

/**
 * Every *mounted* route pattern. Drives `routeExists()` validation. The
 * `/contribute` route is mounted only when `FEATURES.DISCDB` is on, so it is
 * included conditionally — mirroring the gate on its `<Route>` in App.tsx —
 * to keep `routeExists()` honest about what actually resolves.
 */
export const ROUTE_PATTERNS: readonly string[] = [
  ROUTES.HOME,
  ROUTES.HISTORY,
  ROUTES.HISTORY_DETAIL,
  ROUTES.REVIEW,
  ROUTES.REVIEW_DETAIL,
  ...(FEATURES.DISCDB ? [ROUTES.CONTRIBUTE] : []),
];

/** Concrete path to a job's review page — derived from the route pattern. */
export const reviewPath = (jobId: number | string): string =>
  ROUTES.REVIEW_DETAIL.replace(":jobId", String(jobId));

/** Concrete path to a job's history detail page — derived from the route pattern. */
export const historyDetailPath = (jobId: number | string): string =>
  ROUTES.HISTORY_DETAIL.replace(":jobId", String(jobId));

/**
 * True when `pathname` resolves to a mounted route. Uses React Router's own
 * `matchPath` so test-time resolution matches runtime resolution exactly.
 */
export function routeExists(pathname: string): boolean {
  return ROUTE_PATTERNS.some((pattern) => matchPath(pattern, pathname) !== null);
}

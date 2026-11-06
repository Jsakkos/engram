/**
 * Query-string construction for `GET /api/jobs/history`.
 *
 * Extracted from HistoryPage so the filter-to-parameter mapping is unit
 * testable without rendering the page. History is the backstop view: a job
 * parked in review for weeks is not on the dashboard's recency window, so the
 * "All jobs" option here has to reach the backend's `include_all_states` flag
 * rather than a `state` filter.
 */

/** Sentinel for the "All jobs" option; not a real JobState. */
export const ALL_JOBS_FILTER = "__all__";

export interface HistoryQueryInput {
  page: number;
  perPage: number;
  /** "" = all content types. */
  filterType: string;
  /** "" = backend default (completed + failed), or a JobState, or ALL_JOBS_FILTER. */
  filterState: string;
  /** Free-text search; blank/absent = no search. */
  q?: string;
}

export type HistoryFilters = Pick<HistoryQueryInput, "filterType" | "filterState" | "q">;

/** Filter parameters shared by the table and the summary tiles. */
function filterParams({ filterType, filterState, q }: HistoryFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (q && q.trim()) params.set("q", q.trim());
  if (filterType) params.set("content_type", filterType);
  if (filterState === ALL_JOBS_FILTER) {
    params.set("include_all_states", "true");
  } else if (filterState) {
    params.set("state", filterState);
  }
  return params;
}

/** `GET /api/jobs/stats`: the tiles describe the same job set as the table. */
export function buildStatsQuery(filters: HistoryFilters): URLSearchParams {
  return filterParams(filters);
}

export function buildHistoryQuery({
  page,
  perPage,
  filterType,
  filterState,
  q,
}: HistoryQueryInput): URLSearchParams {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
  });
  filterParams({ filterType, filterState, q }).forEach((v, k) => params.set(k, v));
  return params;
}

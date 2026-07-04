/**
 * Shared fetch helpers.
 *
 * These wrap the native `fetch` so callers always get a thrown Error (with the
 * HTTP status and any response body) on a non-2xx response, instead of silently
 * receiving an unparsed/error payload. Dependency-free on purpose.
 */

/** Error thrown by {@link apiFetch}/{@link apiFetchVoid} for non-ok responses. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(status: number, statusText: string, body: string) {
    const detail = body ? `: ${body}` : "";
    super(`Request failed (${status} ${statusText})${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, init);
  if (!res.ok) {
    // Read the body defensively — it may be empty or unreadable.
    let body = "";
    try {
      body = await res.text();
    } catch {
      // body stays "" if the response body is unreadable
    }
    throw new ApiError(res.status, res.statusText, body);
  }
  return res;
}

/**
 * Fetch and parse a JSON response, typed as `T`.
 * Throws {@link ApiError} when the response is not ok.
 */
export async function apiFetch<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const res = await request(input, init);
  return (await res.json()) as T;
}

/**
 * Fetch when the response body is not needed (e.g. POST/DELETE actions).
 * Throws {@link ApiError} when the response is not ok.
 */
export async function apiFetchVoid(input: RequestInfo | URL, init?: RequestInit): Promise<void> {
  await request(input, init);
}

/**
 * Fetch a binary response as a Blob (e.g. a downloadable .zip bundle).
 * Throws {@link ApiError} when the response is not ok.
 */
export async function apiFetchBlob(input: RequestInfo | URL, init?: RequestInit): Promise<Blob> {
  const res = await request(input, init);
  return await res.blob();
}

// ---------------------------------------------------------------------------
// Domain helpers
// ---------------------------------------------------------------------------

/**
 * Shape returned by `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`.
 *
 * `reason` discriminates the outcome. By HTTP status:
 * - **200** — `runLLMMatch` resolves with this shape. `reason` is one of:
 *   - `null` — success; `suggestion` is populated and persisted server-side.
 *   - `"cached"` — idempotent re-click; cached `suggestion` returned without re-transcribing.
 *   - `"ai_disabled"` — AI episode matching is turned off in config.
 *   - `"not_configured"` — enabled but no AI API key is set.
 *   - `"no_show"` — the job has no detected show title.
 *   - `"no_season"` — the job has no detected season.
 *   - `"show_not_found"` — the show could not be resolved on TMDB.
 *   - `"no_match"` — the model ran but produced no confident episode.
 * - **503** — `runLLMMatch` THROWS `ApiError`; retryable operational failures.
 *   `ApiError.body` carries the same `{ suggestion: null, reason }` JSON, where
 *   `reason` is `"matcher_unavailable"`, `"transcription_failed"`, or `"llm_error"`
 *   (the LLM provider call itself failed — rate-limit/credits/auth/5xx/network).
 * - **500** — `runLLMMatch` THROWS `ApiError`; unexpected server error,
 *   `reason: "internal_error"` (also in `ApiError.body`).
 */
export interface LLMMatchResult {
  suggestion: {
    episode: number;
    confidence: number;
    reasoning: string;
    runner_up: { episode: number; confidence: number } | null;
    model: string;
  } | null;
  reason: string | null;
}

/**
 * Run the LLM episode matcher for a single title.
 * The result is also persisted into `match_details.llm_suggestion` on the
 * backend, so refreshing the job via GET will surface it in the Inspector.
 */
export async function runLLMMatch(jobId: number, titleId: number): Promise<LLMMatchResult> {
  return apiFetch<LLMMatchResult>(
    `/api/jobs/${jobId}/titles/${titleId}/llm-match`,
    { method: 'POST' },
  );
}

/**
 * Reassign an episode code to a title, optionally tagging the source of the
 * assignment (e.g. `'ai_llm'` when accepting an LLM suggestion).
 */
export async function reassignEpisode(
  jobId: number,
  titleId: number,
  episodeCode: string,
  edition?: string,
  source?: string,
): Promise<void> {
  const body: Record<string, unknown> = { episode_code: episodeCode };
  if (edition !== undefined) body.edition = edition;
  if (source !== undefined) body.source = source;
  return apiFetchVoid(
    `/api/jobs/${jobId}/titles/${titleId}/reassign`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/**
 * Set a show's output ordering preference (#200), keyed by TMDB id. Ordering is
 * a property of the show (not a one-off review decision), so it persists and
 * applies to future organizes. The caller should refetch the season roster
 * afterwards so projections/divergence reflect the new choice.
 */
export async function setShowOrdering(tmdbId: number, ordering: string): Promise<void> {
  return apiFetchVoid(`/api/shows/${tmdbId}/ordering`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ordering }),
  });
}

/**
 * Re-run matching for a single title. Used for both the single-title "re-match"
 * action and the bulk re-match over a multiselect, so both go through the shared
 * {@link apiFetchVoid} wrapper instead of raw fetch.
 */
export async function rematchTitle(
  jobId: number,
  titleId: number,
  sourcePreference: string = 'engram',
  deep: boolean = false,
): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/rematch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_preference: sourcePreference, deep }),
  });
}

/** One title's review decision in a {@link submitReviewBatch} call. */
export interface ReviewDecisionPayload {
  title_id: number;
  episode_code?: string | null; // e.g. "S01E01", "extra", "skip"
  edition?: string | null;
}

/**
 * Submit several review decisions for a job in one atomic request. The backend
 * applies them all and finalizes once, which keeps bulk "mark as extra" from
 * colliding on FILE_EXISTS the way repeated single-title saves can.
 */
export async function submitReviewBatch(
  jobId: number,
  decisions: ReviewDecisionPayload[],
): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/review/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  });
}

/** Manually re-rip a single rip-failed title (Feature C). */
export async function reripTitle(jobId: number, titleId: number): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/rerip`, { method: 'POST' });
}

/** Skip a queued/not-yet-ripped title so MakeMKV does not rip it. */
export async function skipRipTitle(jobId: number, titleId: number): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/skip-rip`, { method: 'POST' });
}

/** Reverse a skip while the title has not been ripped yet. */
export async function unskipRipTitle(jobId: number, titleId: number): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/unskip-rip`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Manual import
// ---------------------------------------------------------------------------

export interface BrowseEntry {
  name: string;
  path: string;
  type: "dir" | "mkv";
  mkv_count?: number;
}

export interface BrowseResult {
  cwd: string | null;
  parent: string | null;
  roots: string[];
  entries: BrowseEntry[];
}

export interface PreviewUnit {
  show_name: string | null;
  season: number | null;
  file_count: number;
  total_bytes: number;
}

export interface PreviewResult {
  root: string;
  units: PreviewUnit[];
  loose_files: string[];
  total_jobs: number;
  total_files: number;
  total_bytes: number;
  truncated: boolean;
}

/** List a server directory for the import picker. Empty path returns roots. */
export async function browseDir(path: string): Promise<BrowseResult> {
  return apiFetch<BrowseResult>(`/api/import/browse?path=${encodeURIComponent(path)}`);
}

/** Scan a path and return the import units + totals (no job is created). */
export async function previewImport(path: string): Promise<PreviewResult> {
  return apiFetch<PreviewResult>("/api/import/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

/** Create one import job per (show, season) unit under path. */
export async function startImport(
  path: string,
  destinationMode: "library" | "in_place",
): Promise<{ job_ids: number[] }> {
  return apiFetch<{ job_ids: number[] }>("/api/import/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, destination_mode: destinationMode }),
  });
}

/** The target kind for a post-completion track amendment. */
export type AmendKind = "episode" | "extra" | "discard";

/**
 * Amend a completed title's assignment: reassign to a different episode, mark
 * as an extra, or discard it. Only available after the job has completed.
 *
 * @param jobId   - The parent job id.
 * @param titleId - The title/track id to amend.
 * @param target  - The new assignment (kind + optional episode_code for "episode").
 */
export async function amendTitle(
  jobId: number,
  titleId: number,
  target: { kind: AmendKind; episode_code?: string },
): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/amend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
}

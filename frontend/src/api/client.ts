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

/** Shape returned by POST /api/jobs/{job_id}/titles/{title_id}/llm-match */
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

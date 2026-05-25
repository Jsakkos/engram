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

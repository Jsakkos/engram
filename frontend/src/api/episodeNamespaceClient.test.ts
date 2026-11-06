import { afterEach, describe, expect, it, vi } from "vitest";
import { dismissTvdbSuggestion, setEpisodeNamespace } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockResponse(status: number, body: string) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "X",
    json: async () => JSON.parse(body),
    text: async () => body,
  } as unknown as Response);
}

describe("episode namespace client", () => {
  it("posts the namespace to the job endpoint", async () => {
    const fetchMock = mockResponse(200, '{"job_id":7,"episode_namespace":"tvdb"}');
    vi.stubGlobal("fetch", fetchMock);
    await setEpisodeNamespace(7, "tvdb");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/jobs/7/episode-namespace");
    expect(fetchMock.mock.calls[0][1].body).toBe('{"namespace":"tvdb"}');
  });

  it("surfaces the backend detail sentence as the error message", async () => {
    vi.stubGlobal(
      "fetch",
      mockResponse(503, '{"detail":"TheTVDB is unavailable right now; numbering was not changed."}'),
    );
    await expect(setEpisodeNamespace(7, "tvdb")).rejects.toThrow(
      /^TheTVDB is unavailable right now; numbering was not changed\.$/,
    );
  });

  it("falls back to the status when the body is not JSON", async () => {
    vi.stubGlobal("fetch", mockResponse(502, "<html>bad gateway</html>"));
    await expect(dismissTvdbSuggestion(42)).rejects.toThrow("Request failed (502).");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { reIdentifyJob, seasonAnswerBody } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("seasonAnswerBody (#874)", () => {
  it("sends a picked season as-is", () => {
    expect(seasonAnswerBody(3)).toEqual({ season: 3 });
  });

  it("sends a missing season as null, which the API reads as unchanged", () => {
    expect(seasonAnswerBody(undefined)).toEqual({ season: null });
  });

  it("sends 'all' as the explicit clear flag, never as a season number", () => {
    expect(seasonAnswerBody("all")).toEqual({ season: null, all_seasons: true });
  });
});

describe("reIdentifyJob", () => {
  it("posts all_seasons when the user chose All seasons", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await reIdentifyJob(7, "Frasier", "tv", "all", 195241);

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/jobs/7/re-identify");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      title: "Frasier",
      content_type: "tv",
      season: null,
      all_seasons: true,
      tmdb_id: 195241,
    });
  });
});

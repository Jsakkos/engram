import { afterEach, describe, expect, it, vi } from "vitest";
import { skipRipTitle, unskipRipTitle } from "./client";

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

describe("skip-rip client", () => {
  it("posts to the title's skip-rip endpoint", async () => {
    const fetchMock = mockResponse(200, '{"status":"skipped"}');
    vi.stubGlobal("fetch", fetchMock);
    await skipRipTitle(69, 382);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/jobs/69/titles/382/skip-rip");
  });

  it("rejects with the server's reason so the card can show it (#754)", async () => {
    // A refused skip used to be swallowed: the track kept its SKIP button and
    // nothing told the user why pressing it did nothing.
    vi.stubGlobal(
      "fetch",
      mockResponse(400, '{"detail":"Title not found, not part of this job, or not skippable"}'),
    );
    await expect(skipRipTitle(69, 382)).rejects.toThrow(
      /^Title not found, not part of this job, or not skippable$/,
    );
  });

  it("does the same for un-skip", async () => {
    vi.stubGlobal("fetch", mockResponse(400, '{"detail":"already ripped"}'));
    await expect(unskipRipTitle(69, 382)).rejects.toThrow(/^already ripped$/);
  });
});

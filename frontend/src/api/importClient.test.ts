import { afterEach, describe, expect, it, vi } from "vitest";
import { browseDir, previewImport, startImport } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockJson(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response);
}

describe("import client", () => {
  it("browseDir encodes the path query", async () => {
    const fetchMock = mockJson({ cwd: "/x", parent: null, roots: [], entries: [] });
    vi.stubGlobal("fetch", fetchMock);
    await browseDir("/media/My Rips");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/import/browse?path=");
    expect(url).toContain(encodeURIComponent("/media/My Rips"));
  });

  it("previewImport posts the path", async () => {
    const fetchMock = mockJson({ root: "/x", units: [], loose_files: [], total_jobs: 0, total_files: 0, total_bytes: 0, truncated: false });
    vi.stubGlobal("fetch", fetchMock);
    const res = await previewImport("/x");
    expect(res.total_jobs).toBe(0);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });

  it("startImport posts path and destination", async () => {
    const fetchMock = mockJson({ job_ids: [1, 2] });
    vi.stubGlobal("fetch", fetchMock);
    const res = await startImport("/x", "library");
    expect(res.job_ids).toEqual([1, 2]);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toEqual({ path: "/x", destination_mode: "library" });
  });
});

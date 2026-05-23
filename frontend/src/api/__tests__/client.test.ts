import { describe, it, expect, vi, afterEach } from "vitest";
import { apiFetch, apiFetchVoid, ApiError } from "../client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number; statusText?: string; text?: string } = {}) {
  const ok = init.ok ?? true;
  return {
    ok,
    status: init.status ?? (ok ? 200 : 500),
    statusText: init.statusText ?? (ok ? "OK" : "Internal Server Error"),
    json: async () => body,
    text: async () => init.text ?? JSON.stringify(body),
  } as Response;
}

describe("apiFetch", () => {
  it("returns parsed JSON on ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ hello: "world" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiFetch<{ hello: string }>("/api/thing");

    expect(result).toEqual({ hello: "world" });
    expect(fetchMock).toHaveBeenCalledWith("/api/thing", undefined);
  });

  it("throws ApiError with status + body on non-ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(null, { ok: false, status: 503, statusText: "Service Unavailable", text: "backend down" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/api/thing")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      body: "backend down",
    });
    await expect(apiFetch("/api/thing")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("apiFetchVoid", () => {
  it("resolves on ok response without parsing JSON", async () => {
    const json = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      statusText: "No Content",
      json,
      text: async () => "",
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetchVoid("/api/thing", { method: "POST" })).resolves.toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(null, { ok: false, status: 400, statusText: "Bad Request", text: "nope" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetchVoid("/api/thing", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
  });
});

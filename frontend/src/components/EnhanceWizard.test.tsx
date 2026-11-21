import "@testing-library/jest-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import EnhanceWizard from "./EnhanceWizard";

const JOB = {
  id: 7,
  volume_label: "ROSEANNE_S1D1",
  content_type: "tv",
  detected_title: "Roseanne",
  detected_season: 1,
  content_hash: "ABCDEF0123456789",
  completed_at: null,
  export_status: "pending" as const,
  submitted_at: null,
  contribute_url: null,
  release_group_id: null,
  upc_code: null,
  asin: null,
  release_date: null,
};

const COVER_URL = "https://m.media-amazon.com/images/I/cover.jpg";

const LOOKUP = {
  success: true,
  product_title: "Roseanne: Season 1",
  brand: null,
  match_confidence: "high",
  asins: ["B000123456"],
  images: [COVER_URL],
  release_date: null,
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith("/upc-lookup")) return jsonResponse(LOOKUP);
    if (url.endsWith("/fetch-cover")) return jsonResponse({ status: "saved" });
    if (url.endsWith("/enhance")) return jsonResponse({ status: "enhanced" });
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const calledPaths = () => fetchMock.mock.calls.map(([url]) => String(url).split("/").pop());

async function walkToSaveWithCoverPicked() {
  fireEvent.change(screen.getByPlaceholderText(/883929123456/), {
    target: { value: "012345678905" },
  });
  fireEvent.click(screen.getByRole("button", { name: (n) => /lookup/i.test(n) && !/skip/i.test(n) }));
  await waitFor(() => expect(screen.getByText("Roseanne: Season 1")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> ASIN
  fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> Cover
  fireEvent.click(await screen.findByRole("button", { name: "Cover" }));
  fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> Save
  await screen.findByRole("button", { name: /save enhancement/i });
}

describe("EnhanceWizard cover (#923)", () => {
  it("saves a picked cover on Save even when Fetch cover was never pressed", async () => {
    const onSave = vi.fn();
    render(<EnhanceWizard job={JOB} titles={[]} onSave={onSave} onCancel={() => {}} />);
    await walkToSaveWithCoverPicked();

    expect(screen.getByText(/cover art will be saved/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save enhancement/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(calledPaths()).toEqual(["upc-lookup", "fetch-cover", "enhance"]);
    const coverBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(coverBody).toEqual({ image_url: COVER_URL });
  });

  it("does not report success when the picked cover cannot be saved", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith("/upc-lookup")) return jsonResponse(LOOKUP);
      if (url.endsWith("/fetch-cover")) return jsonResponse({ detail: "Image server down" }, false, 502);
      return jsonResponse({ status: "enhanced" });
    });
    const onSave = vi.fn();
    render(<EnhanceWizard job={JOB} titles={[]} onSave={onSave} onCancel={() => {}} />);
    await walkToSaveWithCoverPicked();

    fireEvent.click(screen.getByRole("button", { name: /save enhancement/i }));

    expect(await screen.findByText(/image server down/i)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
    expect(calledPaths()).not.toContain("enhance");
  });
});

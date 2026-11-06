import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HistoryPage from "./HistoryPage";
import * as client from "../api/client";

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  apiFetch: vi.fn(),
}));

const EMPTY_STATS = {
  total_jobs: 0,
  completed_jobs: 0,
  failed_jobs: 0,
  tv_count: 0,
  movie_count: 0,
  total_titles_ripped: 0,
  avg_processing_seconds: null,
  common_errors: [],
  recent_jobs: [],
  daily_throughput: [],
};

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <LocationProbe />
      <Routes>
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/history/:jobId" element={<HistoryPage />} />
      </Routes>
    </MemoryRouter>
  );
}

function urlsCalled(prefix: string): string[] {
  return vi
    .mocked(client.apiFetch)
    .mock.calls.map((c) => String(c[0]))
    .filter((u) => u.startsWith(prefix));
}

function lastUrl(prefix: string): string {
  const urls = urlsCalled(prefix);
  return urls[urls.length - 1];
}

describe("HistoryPage text search", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(client.apiFetch).mockImplementation(async (url: URL | RequestInfo) =>
      (String(url).startsWith("/api/jobs/stats") ? EMPTY_STATS : [])
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("seeds the box and both requests from ?q=", async () => {
    renderAt("/history?q=frasier");
    await act(async () => {});
    expect(screen.getByRole("searchbox")).toHaveValue("frasier");
    expect(urlsCalled("/api/jobs/history")[0]).toContain("q=frasier");
    expect(urlsCalled("/api/jobs/stats")[0]).toContain("q=frasier");
  });

  it("debounces typing, then writes q to the URL and refetches", async () => {
    renderAt("/history");
    await act(async () => {});
    const before = urlsCalled("/api/jobs/history").length;

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "inception" } });
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(urlsCalled("/api/jobs/history")).toHaveLength(before);
    expect(screen.getByTestId("loc")).toHaveTextContent(/^\/history$/);

    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(screen.getByTestId("loc")).toHaveTextContent("/history?q=inception");
    expect(lastUrl("/api/jobs/history")).toContain("q=inception");
  });

  it("clearing the box drops q from the URL", async () => {
    renderAt("/history?q=frasier");
    await act(async () => {});
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(screen.getByTestId("loc")).toHaveTextContent(/^\/history$/);
    expect(lastUrl("/api/jobs/history")).not.toContain("q=");
  });
});

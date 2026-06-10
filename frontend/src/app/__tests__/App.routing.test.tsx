import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { FEATURES } from "../../config/constants";

// MainDashboard pulls its job feed from a WebSocket-backed hook and its disc
// list from useDiscFilters. Mock both so the dashboard renders deterministically
// without a live backend. One review_needed job makes the REVIEW tab deep-link.
vi.mock("../hooks/useJobManagement", () => ({
  useJobManagement: () => ({
    jobs: [{ id: 7, state: "review_needed" }],
    titlesMap: {},
    isConnected: true,
    updateStatus: null,
    cancelJob: vi.fn(),
    advanceJob: vi.fn(),
    clearCompleted: vi.fn(),
    setJobName: vi.fn(),
    reIdentifyJob: vi.fn(),
  }),
}));

vi.mock("../hooks/useDiscFilters", () => ({
  useDiscFilters: () => ({
    filter: "all",
    setFilter: vi.fn(),
    discsData: [],
    filteredDiscs: [],
    activeCount: 0,
    completedCount: 0,
  }),
}));

// Imported after the mocks so the dashboard picks them up.
import App from "../App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Every page fires fetch() in effects; keep them inert so renders are
  // deterministic and offline.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as unknown as Response),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("App routing — every page mounts (no blank/black screen)", () => {
  // Each route must render the SvAtmosphere wrapper. A route that renders null
  // (the /review black-screen bug) would leave the container empty and this
  // would throw. /contribute is gated behind FEATURES.DISCDB (off by default),
  // so it's only covered when that flag is enabled.
  const routeCases: Array<[string, string]> = [
    ["/", "dashboard"],
    ["/history", "history"],
    ["/review/7", "review detail"],
    ["/review", "bare review → redirects to dashboard"],
  ];
  if (FEATURES.DISCDB) routeCases.push(["/contribute", "contribute"]);

  it.each(routeCases)("renders content at %s (%s)", async (path) => {
    renderAt(path);
    expect(await screen.findByTestId("sv-atmosphere")).toBeDefined();
  });

  it("clicking the REVIEW nav tab lands on a mounted review page, not a blank screen", async () => {
    const user = userEvent.setup();
    renderAt("/");

    // Dashboard nav is present.
    const reviewTab = await screen.findByTestId("sv-nav-review");
    expect(reviewTab).toBeDefined();

    await user.click(reviewTab);

    // After navigation the dashboard top bar unmounts (the review page renders
    // its own SvPageHeader, not SvTopBar)…
    await waitFor(() => {
      expect(screen.queryByTestId("sv-topbar")).toBeNull();
    });
    // …and the review page mounts. On the original bug this click reached a
    // bare /review with no route → empty container → this would throw.
    expect(screen.getByTestId("sv-atmosphere")).toBeDefined();
  });
});

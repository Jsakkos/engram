import "@testing-library/jest-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ImportModal from "./ImportModal";
import * as client from "../api/client";

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  // Prior calls must not leak into the next test: vi.spyOn returns the same
  // mock (with its accumulated call history) once a method is already spied,
  // since it's the same module-level client object across the whole file.
  vi.restoreAllMocks();
  vi.spyOn(client, "browseDir").mockResolvedValue({
    cwd: "/media",
    parent: "/",
    roots: [],
    entries: [
      { name: "King of Queens", path: "/media/King of Queens", type: "dir", mkv_count: 0 },
    ],
  });
  vi.spyOn(client, "previewImport").mockResolvedValue({
    root: "/media/King of Queens",
    units: [{ show_name: "King of Queens", season: 1, file_count: 25, total_bytes: 100 }],
    loose_files: [],
    total_jobs: 1,
    total_files: 25,
    total_bytes: 100,
    truncated: false,
  });
  vi.spyOn(client, "startImport").mockResolvedValue({ job_ids: [1], blocked: [] });
});

describe("ImportModal", () => {
  it("lists entries from the starting directory", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => expect(screen.getByText("King of Queens")).toBeInTheDocument());
  });

  it("previews a folder and starts the import", async () => {
    const onClose = vi.fn();
    render(<ImportModal onClose={onClose} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));
    fireEvent.click(screen.getByText("King of Queens"));
    await waitFor(() => expect(client.previewImport).toHaveBeenCalledWith("/media/King of Queens"));
    const startBtn = await screen.findByTestId("import-start-btn");
    fireEvent.click(startBtn);
    await waitFor(() => expect(client.startImport).toHaveBeenCalledWith("/media/King of Queens", "library", []));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("navigates to and previews a typed path", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));

    fireEvent.change(screen.getByTestId("import-path-input"), {
      target: { value: "/Volumes/TV Shows/Engram" },
    });
    fireEvent.submit(screen.getByTestId("import-path-form"));

    await waitFor(() =>
      expect(client.browseDir).toHaveBeenCalledWith("/Volumes/TV Shows/Engram"),
    );
    await waitFor(() =>
      expect(client.previewImport).toHaveBeenCalledWith("/Volumes/TV Shows/Engram"),
    );
  });

  it("shows an error and skips the preview when the typed path is bad", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));

    vi.mocked(client.browseDir).mockRejectedValueOnce(new Error("Not a directory: /nope"));
    fireEvent.change(screen.getByTestId("import-path-input"), { target: { value: "/nope" } });
    fireEvent.submit(screen.getByTestId("import-path-form"));

    await waitFor(() => expect(screen.getByText("Not a directory: /nope")).toBeInTheDocument());
    expect(client.previewImport).not.toHaveBeenCalled();
  });

  it("keeps the up-a-level row and start button present with a large directory", async () => {
    vi.mocked(client.browseDir).mockResolvedValue({
      cwd: "/media",
      parent: "/",
      roots: [],
      entries: Array.from({ length: 400 }, (_, i) => ({
        name: `Show ${i}`,
        path: `/media/Show ${i}`,
        type: "dir" as const,
        mkv_count: 0,
      })),
    });
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);

    await waitFor(() => expect(screen.getByText("Show 399")).toBeInTheDocument());
    expect(screen.getByText("..")).toBeInTheDocument();
    expect(screen.getByTestId("import-start-btn")).toBeInTheDocument();
  });

  it("does not overwrite text the user is typing when a slow browse resolves", async () => {
    let resolveBrowse: (v: client.BrowseResult) => void = () => {};
    vi.mocked(client.browseDir).mockImplementationOnce(
      () =>
        new Promise<client.BrowseResult>((r) => {
          resolveBrowse = r;
        }),
    );

    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);

    // The initial browse is still in flight; the user types a path.
    const input = screen.getByTestId("import-path-input");
    fireEvent.change(input, { target: { value: "/Volumes/TV Shows/Engram" } });

    // The slow browse now lands on a different directory.
    resolveBrowse({ cwd: "/media", parent: "/", roots: [], entries: [] });

    await waitFor(() => expect(client.browseDir).toHaveBeenCalled());
    expect((input as HTMLInputElement).value).toBe("/Volumes/TV Shows/Engram");
  });

  it("resumes tracking the location when the user types but then clicks a folder", async () => {
    vi.mocked(client.browseDir)
      .mockResolvedValueOnce({
        cwd: "/media",
        parent: "/",
        roots: [],
        entries: [
          { name: "King of Queens", path: "/media/King of Queens", type: "dir", mkv_count: 0 },
        ],
      })
      .mockResolvedValueOnce({
        cwd: "/media/King of Queens",
        parent: "/media",
        roots: [],
        entries: [],
      });

    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    const input = screen.getByTestId("import-path-input") as HTMLInputElement;
    await waitFor(() => screen.getByText("King of Queens"));

    // User starts typing a path but abandons it and clicks a folder instead.
    fireEvent.change(input, { target: { value: "/half-typed" } });
    fireEvent.click(screen.getByText("King of Queens"));

    // The field must follow the click, not stay stuck on the abandoned text.
    await waitFor(() => expect(input.value).toBe("/media/King of Queens"));
  });

  it("syncs the field to the directory that a folder click navigates into", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));
    expect((screen.getByTestId("import-path-input") as HTMLInputElement).value).toBe("/media");
  });

  it("scrolls the folder you came from into view when navigating up", async () => {
    vi.mocked(client.browseDir)
      .mockResolvedValueOnce({
        cwd: "/media/King of Queens",
        parent: "/media",
        roots: [],
        entries: [],
      })
      .mockResolvedValueOnce({
        cwd: "/media",
        parent: "/",
        roots: [],
        entries: [
          { name: "King of Queens", path: "/media/King of Queens", type: "dir", mkv_count: 0 },
        ],
      });

    render(
      <ImportModal
        onClose={() => {}}
        defaultPath="/media/King of Queens"
        defaultDestinationMode="library"
      />,
    );
    await waitFor(() => expect(screen.getByText("..")).toBeInTheDocument());

    fireEvent.click(screen.getByText(".."));

    await waitFor(() => expect(screen.getByText("King of Queens")).toBeInTheDocument());
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });

  const blockedUnit = (over: Partial<client.BlockedUnit> = {}): client.BlockedUnit => ({
    unit_key: "key-s1",
    show_name: "King of Queens",
    season: 1,
    display_path: "/media/King of Queens/Season 1",
    reason: "already_imported",
    job_ids: [7],
    ...over,
  });

  async function startAndExpectConflict(result: client.ImportStartResult) {
    vi.mocked(client.startImport).mockResolvedValueOnce(result);
    const onClose = vi.fn();
    render(<ImportModal onClose={onClose} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));
    fireEvent.click(screen.getByText("King of Queens"));
    fireEvent.click(await screen.findByTestId("import-start-btn"));
    await waitFor(() => expect(screen.getByTestId("import-conflict-panel")).toBeInTheDocument());
    return onClose;
  }

  it("keeps the modal open and reports blocked units instead of closing silently", async () => {
    const onClose = await startAndExpectConflict({ job_ids: [], blocked: [blockedUnit()] });
    expect(screen.getByText(/previously imported/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("re-imports only the soft-blocked units when Re-import Anyway is pressed", async () => {
    await startAndExpectConflict({
      job_ids: [],
      blocked: [
        blockedUnit(),
        blockedUnit({ unit_key: "key-s2", season: 2, reason: "in_flight", job_ids: [9] }),
      ],
    });

    fireEvent.click(screen.getByTestId("import-force-btn"));

    // Only the already_imported unit is forced; the in_flight one is never forceable.
    await waitFor(() =>
      expect(client.startImport).toHaveBeenLastCalledWith(
        "/media/King of Queens",
        "library",
        ["key-s1"],
      ),
    );
  });

  it("offers no force action when every block is in flight", async () => {
    await startAndExpectConflict({
      job_ids: [],
      blocked: [blockedUnit({ reason: "in_flight", job_ids: [9] })],
    });
    expect(screen.getByText(/already processing/i)).toBeInTheDocument();
    expect(screen.queryByTestId("import-force-btn")).not.toBeInTheDocument();
  });

  it("reports how many units started when only some were blocked", async () => {
    await startAndExpectConflict({ job_ids: [11], blocked: [blockedUnit()] });
    expect(screen.getByText(/started 1 of 2/i)).toBeInTheDocument();
  });

  it("closes once a forced re-import succeeds", async () => {
    const onClose = await startAndExpectConflict({ job_ids: [], blocked: [blockedUnit()] });
    vi.mocked(client.startImport).mockResolvedValueOnce({ job_ids: [12], blocked: [] });
    fireEvent.click(screen.getByTestId("import-force-btn"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

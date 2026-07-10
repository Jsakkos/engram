import "@testing-library/jest-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ImportModal from "./ImportModal";
import * as client from "../api/client";

beforeEach(() => {
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
  vi.spyOn(client, "startImport").mockResolvedValue({ job_ids: [1] });
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
    await waitFor(() => expect(client.startImport).toHaveBeenCalledWith("/media/King of Queens", "library"));
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
});

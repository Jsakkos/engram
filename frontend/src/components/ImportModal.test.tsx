import "@testing-library/jest-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ImportModal from "./ImportModal";
import * as client from "../api/client";

beforeEach(() => {
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
});

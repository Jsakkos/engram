import "@testing-library/jest-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import BugReportModal from "./BugReportModal";
import * as client from "../api/client";

const BASE_REPORT = {
  app_version: "0.25.0",
  python_version: "3.13.11",
  os: "Windows 11",
  makemkv_version: "MakeMKV v1.17.7",
  ffmpeg_version: "ffmpeg version 6.1.1",
  job: null,
  recent_errors: ["t | ERROR | job=47 | a:b:1 - job 47's own error"],
  recent_errors_is_fallback: false,
  config: {},
  github_url: "https://github.com/Jsakkos/engram/issues/new?title=x&body=y",
  markdown: "## Bug Report\n",
  bundle_available: false,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("BugReportModal", () => {
  it("does not show a fallback notice when recent_errors_is_fallback is false", async () => {
    vi.spyOn(client, "apiFetch").mockResolvedValue(BASE_REPORT);
    render(<BugReportModal open onClose={() => {}} jobId={47} />);

    await waitFor(() => expect(screen.getByText(/job 47's own error/)).toBeInTheDocument());
    expect(screen.queryByText(/not specific to this job/i)).not.toBeInTheDocument();
  });

  it("shows a fallback notice above Recent Errors when the errors aren't job-specific", async () => {
    vi.spyOn(client, "apiFetch").mockResolvedValue({
      ...BASE_REPORT,
      recent_errors: ["t | ERROR | job=39 | a:b:1 - unrelated job's error"],
      recent_errors_is_fallback: true,
    });
    render(<BugReportModal open onClose={() => {}} jobId={47} />);

    await waitFor(() => expect(screen.getByText(/unrelated job's error/)).toBeInTheDocument());
    expect(screen.getByText(/not specific to this job/i)).toBeInTheDocument();
  });
});

import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDiscFilters } from "../useDiscFilters";
import type { Job } from "../../../types";

function job(id: number, state: Job["state"]): Job {
  return {
    id,
    drive_id: "import",
    volume_label: `VOL${id}`,
    content_type: "tv",
    state,
    current_speed: "0.0x",
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 0,
    error_message: null,
  } as Job;
}

describe("useDiscFilters", () => {
  it("counts failed jobs separately from completed ones", () => {
    const jobs = [job(1, "completed"), job(2, "failed"), job(3, "ripping")];
    const { result } = renderHook(() => useDiscFilters(jobs, {}, false));

    expect(result.current.completedCount).toBe(1);
    // Without this, App.tsx renders no clear button at all on a dashboard that
    // holds only failed jobs, leaving them permanently undismissable.
    expect(result.current.failedCount).toBe(1);
    expect(result.current.activeCount).toBe(1);
  });

  it("reports no failures when there are none", () => {
    const { result } = renderHook(() => useDiscFilters([job(1, "completed")], {}, false));
    expect(result.current.failedCount).toBe(0);
  });
});

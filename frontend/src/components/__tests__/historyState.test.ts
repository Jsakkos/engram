import { describe, expect, it } from "vitest";

import { historyBadgeState, historyTimelineLabel } from "../historyState";

describe("historyBadgeState", () => {
  it("marks a completed job complete", () => {
    expect(historyBadgeState("completed")).toBe("complete");
  });

  it("marks a failed job as an error", () => {
    expect(historyBadgeState("failed")).toBe("error");
  });

  it("does not call a job awaiting review an error", () => {
    expect(historyBadgeState("review_needed")).toBe("review");
  });

  it("treats any other in-flight state as neutral, not failed", () => {
    expect(historyBadgeState("ripping")).toBe("idle");
    expect(historyBadgeState("matching")).toBe("idle");
  });
});

describe("historyTimelineLabel", () => {
  it("names the terminal event", () => {
    expect(historyTimelineLabel("completed")).toBe("Completed");
    expect(historyTimelineLabel("failed")).toBe("Failed");
  });

  it("does not label an unfinished job with a finish event", () => {
    // The row's value is completed_at, which is null until a job ends, so a
    // "Finished" label here would read "Finished: -" for a job still running.
    expect(historyTimelineLabel("review_needed")).toBe("In progress");
    expect(historyTimelineLabel("ripping")).toBe("In progress");
  });
});

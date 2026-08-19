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

  it("does not claim an unfinished job failed", () => {
    expect(historyTimelineLabel("review_needed")).toBe("Finished");
    expect(historyTimelineLabel("ripping")).toBe("Finished");
  });
});

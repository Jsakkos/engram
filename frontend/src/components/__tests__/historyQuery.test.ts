import { describe, expect, it } from "vitest";

import { ALL_JOBS_FILTER, buildHistoryQuery } from "../historyQuery";

describe("buildHistoryQuery", () => {
  it("always carries pagination", () => {
    const params = buildHistoryQuery({ page: 2, perPage: 20, filterType: "", filterState: "" });
    expect(params.get("page")).toBe("2");
    expect(params.get("per_page")).toBe("20");
  });

  it("omits empty filters so history keeps its terminal-only default", () => {
    const params = buildHistoryQuery({ page: 1, perPage: 20, filterType: "", filterState: "" });
    expect(params.has("content_type")).toBe(false);
    expect(params.has("state")).toBe(false);
    expect(params.has("include_all_states")).toBe(false);
  });

  it("passes a concrete state filter straight through", () => {
    const params = buildHistoryQuery({
      page: 1,
      perPage: 20,
      filterType: "tv",
      filterState: "review_needed",
    });
    expect(params.get("content_type")).toBe("tv");
    expect(params.get("state")).toBe("review_needed");
    expect(params.has("include_all_states")).toBe(false);
  });

  it("translates the all-jobs sentinel into include_all_states", () => {
    const params = buildHistoryQuery({
      page: 1,
      perPage: 20,
      filterType: "",
      filterState: ALL_JOBS_FILTER,
    });
    expect(params.get("include_all_states")).toBe("true");
    expect(params.has("state")).toBe(false);
  });
});

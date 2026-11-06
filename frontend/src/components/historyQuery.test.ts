import { describe, expect, it } from "vitest";
import { ALL_JOBS_FILTER, buildHistoryQuery, buildStatsQuery } from "./historyQuery";

describe("buildStatsQuery", () => {
  it("sends no filters for the default view", () => {
    expect(buildStatsQuery({ filterType: "", filterState: "" }).toString()).toBe("");
  });

  it("carries the same filters as the table, without paging", () => {
    const table = buildHistoryQuery({ page: 3, perPage: 20, filterType: "tv", filterState: "failed" });
    const stats = buildStatsQuery({ filterType: "tv", filterState: "failed" });
    table.delete("page");
    table.delete("per_page");
    expect(stats.toString()).toBe(table.toString());
  });

  it("maps All jobs to include_all_states", () => {
    const stats = buildStatsQuery({ filterType: "", filterState: ALL_JOBS_FILTER });
    expect(stats.get("include_all_states")).toBe("true");
    expect(stats.has("state")).toBe(false);
  });
});

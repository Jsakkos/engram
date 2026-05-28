import { describe, it, expect } from "vitest";
import { buildNavItems } from "../navigation";
import { ROUTES, routeExists } from "../../config/routes";

/**
 * Guards the class of bug behind the `/review` black screen: a nav link
 * pointing at a path no route handles. Because `routeExists` uses React
 * Router's own `matchPath`, a link resolving here means it resolves at runtime.
 */
describe("top-nav route integrity", () => {
  it("every visible nav destination resolves to a mounted route", () => {
    const items = buildNavItems({ firstReviewJobId: 7, reviewCount: 2, contributionPending: 1 });
    for (const item of items.filter((i) => i.show !== false)) {
      expect(
        routeExists(item.to),
        `nav "${item.label}" → "${item.to}" must resolve to a mounted route`,
      ).toBe(true);
    }
  });

  it("REVIEW deep-links to the first job awaiting review", () => {
    const review = buildNavItems({ firstReviewJobId: 42 }).find((i) => i.label === "REVIEW");
    expect(review?.to).toBe("/review/42");
    expect(routeExists(review!.to)).toBe(true);
  });

  it("REVIEW falls back to the dashboard — never a bare /review — when nothing needs review", () => {
    const review = buildNavItems().find((i) => i.label === "REVIEW");
    // Bare "/review" renders nothing under the dynamic-segment route; that was
    // the original black-screen bug. The fallback must be the dashboard.
    expect(review?.to).not.toBe(ROUTES.REVIEW);
    expect(review?.to).toBe(ROUTES.HOME);
  });

  it("routeExists rejects a path with no matching route", () => {
    expect(routeExists("/nope")).toBe(false);
  });
});

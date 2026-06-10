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

  it("REVIEW is disabled — never a bare /review link — when nothing needs review", () => {
    const review = buildNavItems().find((i) => i.label === "REVIEW");
    // Bare "/review" renders nothing under the dynamic-segment route; that was
    // the original black-screen bug. And linking the dashboard instead gave two
    // tabs the same destination (duplicate React keys) plus silent navigation.
    // The tab is inert until a review exists; `to` stays resolvable for safety.
    expect(review?.disabled).toBe(true);
    expect(review?.disabledHint).toMatch(/no jobs awaiting review/i);
    expect(review?.to).not.toBe(ROUTES.REVIEW);
    expect(routeExists(review!.to)).toBe(true);
  });

  it("REVIEW is enabled when a job awaits review", () => {
    const review = buildNavItems({ firstReviewJobId: 6, reviewCount: 1 }).find(
      (i) => i.label === "REVIEW",
    );
    expect(review?.disabled).toBeFalsy();
  });

  it("labels are unique so list keys cannot collide", () => {
    const labels = buildNavItems({}).map((i) => i.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("routeExists rejects a path with no matching route", () => {
    expect(routeExists("/nope")).toBe(false);
  });
});

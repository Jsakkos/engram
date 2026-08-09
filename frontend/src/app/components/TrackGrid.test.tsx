import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrackGrid } from "./TrackGrid";
import type { Track } from "./DiscCard";

function makeTrack(overrides: Partial<Track> = {}): Track {
  return {
    id: "1",
    title: "Hammers and Veils",
    duration: "43:51",
    state: "matched",
    progress: 100,
    ...overrides,
  };
}

describe("TrackGrid — provenance rendering", () => {
  it("full-file engram match: shows confidence, FULL-FILE tag, Engram icon chip, no votes", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            finalMatch: "S02E18",
            finalMatchConfidence: 0.93,
            matchSource: "engram",
            matchMethod: "full_file",
          }),
        ]}
      />,
    );
    // The episode span renders "→ S02E18" as one node, so match a substring.
    expect(screen.getByText(/S02E18/)).toBeInTheDocument();
    expect(screen.getByText("93%")).toBeInTheDocument();
    expect(screen.getByText("FULL-FILE")).toBeInTheDocument();
    expect(screen.getByTestId("source-badge-engram")).toBeInTheDocument();
  });

  it("chunk-vote engram match: shows votes and no FULL-FILE tag", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "The Bracebridge Dinner",
            finalMatch: "S02E17",
            finalMatchConfidence: 0.71,
            finalMatchVotes: 3,
            finalMatchTargetVotes: 10,
            matchSource: "engram",
            matchMethod: "chunk_vote",
          }),
        ]}
      />,
    );
    expect(screen.getByText("71%")).toBeInTheDocument();
    expect(screen.getByText("3/10")).toBeInTheDocument();
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
  });

  it("discdb match: text chip, confidence, no method tag", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "Nick and Nora",
            finalMatch: "S02E05",
            finalMatchConfidence: 0.99,
            matchSource: "discdb",
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("source-badge-discdb")).toBeInTheDocument();
    expect(screen.getByText("99%")).toBeInTheDocument();
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
  });

  it("matched track with missing match_source still gets an Engram chip", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ finalMatch: "S02E19", finalMatchConfidence: 0.9 })]}
      />,
    );
    expect(screen.getByTestId("source-badge-engram")).toBeInTheDocument();
  });

  it("review track with a stale match_source shows no provider chip or method tag", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "Run Away, Little Boy",
            state: "review",
            finalMatch: "S02E09",
            finalMatchConfidence: 0.42,
            matchSource: "engram",
            matchMethod: "full_file",
            videoResolution: "1080p",
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId("source-badge-engram")).not.toBeInTheDocument();
    // The resolution chip forces the badge row to render, but no FULL-FILE tag
    // should leak onto a non-matched track.
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
  });

  it("chromaprint match: renders the engram_chromaprint icon chip with the fingerprint node", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "Presenting Lorelai Gilmore",
            finalMatch: "S02E06",
            finalMatchConfidence: 0.95,
            matchSource: "engram_chromaprint",
            matchMethod: "chunk_vote",
          }),
        ]}
      />,
    );
    const chip = screen.getByTestId("source-badge-engram_chromaprint");
    expect(chip).toBeInTheDocument();
    // The fingerprint variant draws a filled center node (a magenta <circle>).
    const node = chip.querySelector("circle");
    expect(node).not.toBeNull();
    expect(node!.getAttribute("fill")).toBe("#ff3d7f");
  });
});

describe("TrackGrid — skip affordance", () => {
  it("offers SKIP for a pending track", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "7", state: "pending" })]}
        onSkipTrack={() => {}}
      />,
    );
    expect(screen.getByTestId("skip-track-7")).toBeInTheDocument();
  });

  it("does NOT offer SKIP for a queued track — it is already ripped to disk", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "8", state: "queued" })]}
        onSkipTrack={() => {}}
      />,
    );
    expect(screen.queryByTestId("skip-track-8")).not.toBeInTheDocument();
  });

  it("still offers UN-SKIP for a skipped track", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ id: "9", state: "skipped" })]}
        onUnskipTrack={() => {}}
      />,
    );
    expect(screen.getByTestId("unskip-track-9")).toBeInTheDocument();
  });
});

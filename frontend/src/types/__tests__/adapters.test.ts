import { describe, it, expect } from "vitest";
import {
  mapJobStateToDiscState,
  mapTitleStateToTrackState,
  transformJobToDiscData,
} from "../adapters";
import type { Job, DiscTitle, JobState, TitleState } from "../index";

// ---------------------------------------------------------------------------
// mapJobStateToDiscState
// ---------------------------------------------------------------------------

describe("mapJobStateToDiscState", () => {
  const cases: [JobState, string][] = [
    ["idle", "idle"],
    ["identifying", "scanning"],
    ["review_needed", "scanning"],
    ["ripping", "ripping"],
    ["matching", "ripping"],
    ["organizing", "ripping"],
    ["completed", "completed"],
    ["failed", "error"],
  ];

  it.each(cases)("maps %s → %s", (input, expected) => {
    expect(mapJobStateToDiscState(input)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// mapTitleStateToTrackState
// ---------------------------------------------------------------------------

describe("mapTitleStateToTrackState", () => {
  const cases: [TitleState, string][] = [
    ["pending", "pending"],
    ["ripping", "ripping"],
    ["matching", "matching"],
    ["matched", "matched"],
    ["review", "matched"],
    ["completed", "matched"],
    ["failed", "failed"],
  ];

  it.each(cases)("maps %s → %s", (input, expected) => {
    expect(mapTitleStateToTrackState(input)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// transformJobToDiscData
// ---------------------------------------------------------------------------

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    drive_id: "D:",
    volume_label: "TEST_DISC",
    content_type: "tv",
    state: "ripping",
    current_speed: "2.1x",
    eta_seconds: 120,
    progress_percent: 45,
    current_title: 2,
    total_titles: 5,
    error_message: null,
    ...overrides,
  };
}

function makeTitle(overrides: Partial<DiscTitle> = {}): DiscTitle {
  return {
    id: 10,
    job_id: 1,
    title_index: 0,
    duration_seconds: 2400,
    file_size_bytes: 1_000_000_000,
    chapter_count: 10,
    is_selected: true,
    output_filename: null,
    matched_episode: null,
    match_confidence: 0,
    state: "pending",
    ...overrides,
  };
}

describe("transformJobToDiscData", () => {
  it("transforms a TV job correctly", () => {
    const job = makeJob({ content_type: "tv", detected_title: "Test Show" });
    const titles = [
      makeTitle({ title_index: 0, state: "ripping" }),
      makeTitle({ id: 11, title_index: 1, state: "pending" }),
    ];

    const result = transformJobToDiscData(job, titles);

    expect(result.id).toBe("1");
    expect(result.title).toBe("Test Show");
    expect(result.mediaType).toBe("tv");
    expect(result.state).toBe("ripping");
    expect(result.progress).toBe(45);
    expect(result.tracks).toHaveLength(2);
  });

  it("transforms a movie job correctly", () => {
    const job = makeJob({
      content_type: "movie",
      detected_title: "Inception",
      state: "completed",
    });
    const titles = [
      makeTitle({
        state: "completed",
        matched_episode: "Inception",
        match_confidence: 1.0,
      }),
    ];

    const result = transformJobToDiscData(job, titles);

    expect(result.mediaType).toBe("movie");
    expect(result.state).toBe("completed");
    expect(result.tracks).toHaveLength(1);
  });

  it("falls back to volume_label when detected_title is missing", () => {
    const job = makeJob({ detected_title: undefined });
    const result = transformJobToDiscData(job, []);

    expect(result.title).toBe("TEST_DISC");
  });

  it("handles unknown content_type", () => {
    const job = makeJob({ content_type: "unknown" });
    const result = transformJobToDiscData(job, []);

    expect(result.mediaType).toBe("unknown");
  });
});

// ---------------------------------------------------------------------------
// formatDuration (via Track.duration in transformJobToDiscData)
// ---------------------------------------------------------------------------

describe("formatDuration (via track transform)", () => {
  it("formats 0 seconds", () => {
    const job = makeJob();
    const title = makeTitle({ duration_seconds: 0 });
    const result = transformJobToDiscData(job, [title]);
    expect(result.tracks![0].duration).toBe("0:00");
  });

  it("formats 90 seconds as 1:30", () => {
    const job = makeJob();
    const title = makeTitle({ duration_seconds: 90 });
    const result = transformJobToDiscData(job, [title]);
    expect(result.tracks![0].duration).toBe("1:30");
  });

  it("formats 3661 seconds as 1:01:01", () => {
    const job = makeJob();
    const title = makeTitle({ duration_seconds: 3661 });
    const result = transformJobToDiscData(job, [title]);
    expect(result.tracks![0].duration).toBe("1:01:01");
  });
});

// ---------------------------------------------------------------------------
// extractMatchCandidates (via Track.matchCandidates in transformJobToDiscData)
// ---------------------------------------------------------------------------

describe("extractMatchCandidates (via track transform)", () => {
  it("extracts runner_ups from match_details JSON", () => {
    const matchDetails = JSON.stringify({
      score: 0.95,
      vote_count: 4,
      runner_ups: [
        { episode: "S01E02", score: 0.8, vote_count: 3, target_votes: 5 },
        { episode: "S01E03", score: 0.5, vote_count: 2, target_votes: 5 },
      ],
    });

    const job = makeJob();
    const title = makeTitle({
      state: "matched",
      matched_episode: "S01E01",
      match_confidence: 0.95,
      match_details: matchDetails,
    });
    const result = transformJobToDiscData(job, [title]);
    const track = result.tracks![0];

    expect(track.matchCandidates).toBeDefined();
    expect(track.matchCandidates).toHaveLength(2);
    expect(track.matchCandidates![0].episode).toBe("S01E02");
    expect(track.matchCandidates![0].confidence).toBe(0.8);
  });

  it("returns undefined when no match_details", () => {
    const job = makeJob();
    const title = makeTitle({ match_details: null });
    const result = transformJobToDiscData(job, [title]);
    expect(result.tracks![0].matchCandidates).toBeUndefined();
  });
});

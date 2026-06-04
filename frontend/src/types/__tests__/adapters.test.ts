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
    ["review_needed", "review_needed"],
    ["ripping", "ripping"],
    ["matching", "matching"],
    ["organizing", "organizing"],
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
    ["queued", "queued"],
    ["matching", "matching"],
    ["matched", "matched"],
    ["review", "review"],
    ["completed", "completed"],
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

  it("renders a queued track as idle (state 'queued', progress 0)", () => {
    // A queued track is on disk waiting for a match slot — it must read as idle,
    // not as work-in-progress, even though it may carry a stale confidence value.
    const job = makeJob({ content_type: "tv", state: "matching" });
    const title = makeTitle({ state: "queued", match_confidence: 0.5 });
    const track = transformJobToDiscData(job, [title]).tracks![0];

    expect(track.state).toBe("queued");
    expect(track.progress).toBe(0);
  });

  it("carries conflict_status through to the disc view model", () => {
    const job = makeJob({
      state: "matching",
      conflict_status: "Resolving episode conflicts — pass 2 of 3",
    });
    const result = transformJobToDiscData(job, []);

    expect(result.conflictStatus).toBe("Resolving episode conflicts — pass 2 of 3");
  });

  it("leaves conflictStatus undefined when absent", () => {
    const result = transformJobToDiscData(makeJob(), []);
    expect(result.conflictStatus).toBeUndefined();
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

  it("prefers calibrated confidence over raw score for runner_ups", () => {
    // Backend now ships both raw `score` (small cosine) and calibrated
    // `confidence` (reviewer-facing). The UI must show the calibrated value.
    const matchDetails = JSON.stringify({
      score: 0.18,
      confidence: 0.92,
      vote_count: 8,
      runner_ups: [
        { episode: "S01E13", score: 0.18, confidence: 0.92, vote_count: 8, target_votes: 10 },
        { episode: "S01E07", score: 0.0, confidence: 0.0, vote_count: 0, target_votes: 10 },
      ],
    });
    const title = makeTitle({
      state: "matched",
      matched_episode: "S01E13",
      match_confidence: 0.92,
      match_details: matchDetails,
    });
    const track = transformJobToDiscData(makeJob(), [title]).tracks![0];

    expect(track.matchCandidates![0].confidence).toBe(0.92); // not 0.18
    expect(track.matchCandidates![1].confidence).toBe(0); // zero-vote loser stays 0
  });

  it("uses calibrated confidence for the headline finalMatchConfidence", () => {
    const matchDetails = JSON.stringify({
      score: 0.165,
      confidence: 0.9,
      vote_count: 8,
      target_votes: 10,
    });
    const title = makeTitle({
      state: "matched",
      matched_episode: "S01E13",
      match_confidence: 0.9,
      match_details: matchDetails,
    });
    const track = transformJobToDiscData(makeJob(), [title]).tracks![0];

    expect(track.finalMatchConfidence).toBe(0.9); // not the raw 0.165
  });
});

// ---------------------------------------------------------------------------
// transformDiscTitleToTrack — match provenance
// ---------------------------------------------------------------------------

describe("track provenance mapping", () => {
  function track(title: Partial<DiscTitle>) {
    return transformJobToDiscData(makeJob({ state: "matching" }), [makeTitle(title)])
      .tracks![0];
  }

  it("chunk-vote match: confidence + votes from match_details, method chunk_vote", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E17",
      match_source: "engram",
      match_confidence: 0.71,
      match_details: JSON.stringify({ score: 0.71, vote_count: 3, target_votes: 10 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.71);
    expect(t.finalMatchVotes).toBe(3);
    expect(t.finalMatchTargetVotes).toBe(10);
    expect(t.matchMethod).toBe("chunk_vote");
  });

  it("full-file fallback: confidence from column, no votes, method full_file", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E18",
      match_source: "engram",
      match_confidence: 0.93,
      match_details: JSON.stringify({ method: "full_transcription", score: 0.93 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.93);
    expect(t.finalMatchVotes).toBeUndefined();
    expect(t.matchMethod).toBe("full_file");
  });

  it("discdb match: confidence from column, no method (carries its own chip)", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E05",
      match_source: "discdb",
      match_confidence: 0.99,
      match_details: JSON.stringify({ source: "discdb", episode: "S02E05" }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.99);
    expect(t.finalMatchVotes).toBeUndefined();
    expect(t.matchMethod).toBeUndefined();
  });

  it("manual match: confidence from column even with no match_details", () => {
    const t = track({
      state: "completed",
      matched_episode: "S02E07",
      match_source: "user",
      match_confidence: 1.0,
      match_details: null,
    });
    expect(t.finalMatchConfidence).toBeCloseTo(1.0);
    expect(t.matchMethod).toBeUndefined();
  });

  it("review best-guess: column is 0, so confidence falls back to match_details", () => {
    const t = track({
      state: "review",
      matched_episode: "S02E09",
      match_source: null,
      match_confidence: 0,
      match_details: JSON.stringify({ score: 0.58, vote_count: 2, target_votes: 10 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.58);
  });

  it("review low-confidence: non-zero column wins, method still derived", () => {
    const t = track({
      state: "review",
      matched_episode: "S02E09",
      match_source: "engram",
      match_confidence: 0.42,
      match_details: JSON.stringify({ score: 0.42, confidence: 0.42, vote_count: 2, target_votes: 10 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.42);
    expect(t.matchMethod).toBe("chunk_vote");
  });
});

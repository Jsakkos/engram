import { describe, it, expect } from "vitest";
import type {
  Job,
  DiscTitle,
  JobUpdate,
  TitleUpdate,
  TitlesDiscovered,
  SubtitleEvent,
} from "../../types";

/**
 * Tests for the job management logic extracted from useJobManagement.
 *
 * Since the hook depends on React state + WebSocket, we test the
 * pure data-merging logic that the hook performs.
 */

// ---------------------------------------------------------------------------
// Helpers: replicate the merge logic from useJobManagement
// ---------------------------------------------------------------------------

function mergeJobUpdate(jobs: Job[], message: JobUpdate): Job[] {
  const exists = jobs.some((j) => j.id === message.job_id);
  if (exists) {
    return jobs.map((job) =>
      job.id === message.job_id ? { ...job, ...message } : job,
    );
  }
  return jobs; // unknown job — would trigger fetchJobsAndTitles in real hook
}

function mergeTitleUpdate(
  titlesMap: Record<number, DiscTitle[]>,
  message: TitleUpdate,
): Record<number, DiscTitle[]> {
  return {
    ...titlesMap,
    [message.job_id]:
      titlesMap[message.job_id]?.map((title) =>
        title.id === message.title_id ? { ...title, ...message } : title,
      ) || [],
  };
}

function mergeTitlesDiscovered(
  titlesMap: Record<number, DiscTitle[]>,
  message: TitlesDiscovered,
): Record<number, DiscTitle[]> {
  return {
    ...titlesMap,
    [message.job_id]: message.titles as DiscTitle[],
  };
}

function mergeSubtitleEvent(jobs: Job[], message: SubtitleEvent): Job[] {
  return jobs.map((job) =>
    job.id === message.job_id
      ? {
          ...job,
          subtitle_status: message.status,
          subtitles_downloaded: message.downloaded,
          subtitles_total: message.total,
          subtitles_failed: message.failed_count,
        }
      : job,
  );
}

function checkAllTerminal(titles: DiscTitle[]): boolean {
  const terminalStates = ["matched", "completed", "review", "failed"];
  return (
    titles.length > 0 && titles.every((t) => terminalStates.includes(t.state))
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeJob(id: number, overrides: Partial<Job> = {}): Job {
  return {
    id,
    drive_id: "D:",
    volume_label: `TEST_${id}`,
    content_type: "tv",
    state: "ripping",
    current_speed: "1.5x",
    eta_seconds: 300,
    progress_percent: 30,
    current_title: 1,
    total_titles: 4,
    error_message: null,
    ...overrides,
  };
}

function makeTitle(
  id: number,
  jobId: number,
  overrides: Partial<DiscTitle> = {},
): DiscTitle {
  return {
    id,
    job_id: jobId,
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("job_update merging", () => {
  it("merges partial update into existing job", () => {
    const jobs = [makeJob(1), makeJob(2)];
    const update: JobUpdate = {
      type: "job_update",
      job_id: 1,
      state: "matching",
      progress_percent: 80,
      current_speed: "3.0x",
      eta_seconds: 60,
      error_message: null,
    };

    const result = mergeJobUpdate(jobs, update);

    expect(result[0].state).toBe("matching");
    expect(result[0].progress_percent).toBe(80);
    // Job 2 should be unchanged
    expect(result[1].state).toBe("ripping");
  });

  it("leaves jobs unchanged for unknown job_id", () => {
    const jobs = [makeJob(1)];
    const update: JobUpdate = {
      type: "job_update",
      job_id: 999,
      state: "completed",
      progress_percent: 100,
      current_speed: "0x",
      eta_seconds: 0,
      error_message: null,
    };

    const result = mergeJobUpdate(jobs, update);
    expect(result).toEqual(jobs);
  });
});

describe("title_update merging", () => {
  it("targets the correct title in the correct job", () => {
    const titlesMap: Record<number, DiscTitle[]> = {
      1: [makeTitle(10, 1), makeTitle(11, 1)],
      2: [makeTitle(20, 2)],
    };

    const update: TitleUpdate = {
      type: "title_update",
      job_id: 1,
      title_id: 11,
      state: "matched",
      matched_episode: "S01E02",
      match_confidence: 0.95,
    };

    const result = mergeTitleUpdate(titlesMap, update);

    // Title 11 should be updated
    expect(result[1][1].state).toBe("matched");
    expect(result[1][1].matched_episode).toBe("S01E02");
    // Title 10 should be unchanged
    expect(result[1][0].state).toBe("pending");
    // Job 2 titles unchanged
    expect(result[2][0].state).toBe("pending");
  });
});

describe("all terminal state detection", () => {
  it("returns true when all titles are terminal", () => {
    const titles = [
      makeTitle(1, 1, { state: "matched" }),
      makeTitle(2, 1, { state: "completed" }),
      makeTitle(3, 1, { state: "failed" }),
    ];
    expect(checkAllTerminal(titles)).toBe(true);
  });

  it("returns false when some titles are still active", () => {
    const titles = [
      makeTitle(1, 1, { state: "matched" }),
      makeTitle(2, 1, { state: "matching" }),
    ];
    expect(checkAllTerminal(titles)).toBe(false);
  });

  it("returns false for empty array", () => {
    expect(checkAllTerminal([])).toBe(false);
  });
});

describe("titles_discovered merging", () => {
  it("replaces entire title list for a job", () => {
    const titlesMap: Record<number, DiscTitle[]> = {
      1: [makeTitle(10, 1)],
    };

    const message: TitlesDiscovered = {
      type: "titles_discovered",
      job_id: 1,
      titles: [
        { id: 20, title_index: 0, duration_seconds: 1320, file_size_bytes: 500000, chapter_count: 5 },
        { id: 21, title_index: 1, duration_seconds: 1380, file_size_bytes: 500000, chapter_count: 5 },
      ],
      content_type: "tv",
      detected_title: "Test Show",
      detected_season: 1,
    };

    const result = mergeTitlesDiscovered(titlesMap, message);
    expect(result[1]).toHaveLength(2);
    expect(result[1][0].id).toBe(20);
  });
});

describe("subtitle_event merging", () => {
  it("updates subtitle fields on the correct job", () => {
    const jobs = [makeJob(1), makeJob(2)];
    const event: SubtitleEvent = {
      type: "subtitle_event",
      job_id: 1,
      status: "downloading",
      downloaded: 3,
      total: 8,
      failed_count: 1,
    };

    const result = mergeSubtitleEvent(jobs, event);

    expect(result[0].subtitle_status).toBe("downloading");
    expect(result[0].subtitles_downloaded).toBe(3);
    expect(result[0].subtitles_total).toBe(8);
    expect(result[0].subtitles_failed).toBe(1);
    // Job 2 unchanged
    expect(result[1].subtitle_status).toBeUndefined();
  });
});

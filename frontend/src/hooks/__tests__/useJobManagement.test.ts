import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type {
  Job,
  DiscTitle,
  JobUpdate,
  TitleUpdate,
  TitlesDiscovered,
  SubtitleEvent,
  WebSocketMessage,
  FingerprintDisclosureRequiredMessage,
} from "../../types";

// ---------------------------------------------------------------------------
// Mocks for the hook-level integration tests below.
// useWebSocket is mocked so we can drive onOpen / message listeners manually,
// and toast is mocked so we can assert error surfacing without a DOM portal.
// ---------------------------------------------------------------------------

const toastErrorMock = vi.fn();
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastErrorMock(...args) },
}));

let capturedOnOpen: (() => void) | undefined;
let capturedListener: ((msg: WebSocketMessage) => void) | undefined;

vi.mock("../useWebSocket", () => ({
  useWebSocket: (
    _url: string,
    options?: { onOpen?: () => void },
  ) => {
    capturedOnOpen = options?.onOpen;
    return {
      isConnected: true,
      sendMessage: vi.fn(),
      addMessageListener: (listener: (msg: WebSocketMessage) => void) => {
        capturedListener = listener;
        return () => {
          capturedListener = undefined;
        };
      },
    };
  },
}));

// Imported after the mocks so the hook picks up the mocked useWebSocket.
import { useJobManagement } from "../../app/hooks/useJobManagement";

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

// ---------------------------------------------------------------------------
// Hook integration tests: fetch error surfacing + reconnect resync.
// These exercise the real useJobManagement hook with a stubbed fetch and a
// mocked useWebSocket.
// ---------------------------------------------------------------------------

function okJson(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function errResponse(status = 500): Response {
  return {
    ok: false,
    status,
    statusText: "Server Error",
    json: async () => ({}),
    text: async () => "boom",
  } as Response;
}

describe("useJobManagement hook integration", () => {
  beforeEach(() => {
    toastErrorMock.mockClear();
    capturedOnOpen = undefined;
    capturedListener = undefined;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces an error and does not corrupt state when /api/jobs is not ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue(errResponse(503));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJobManagement(false));

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalled();
    });

    // State stays a valid empty list — never corrupted by the failed fetch.
    expect(result.current.jobs).toEqual([]);
    expect(result.current.titlesMap).toEqual({});
  });

  it("re-runs fetchJobsAndTitles on reconnect (onOpen) to resync", async () => {
    const job = makeJob(1);
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.endsWith("/api/jobs")) return Promise.resolve(okJson([job]));
      if (urlStr.includes("/titles")) return Promise.resolve(okJson([]));
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJobManagement(false));

    // Initial mount fetch resolves.
    await waitFor(() => {
      expect(result.current.jobs).toHaveLength(1);
    });

    const jobsCallsAfterMount = fetchMock.mock.calls.filter((c) =>
      String(c[0]).endsWith("/api/jobs"),
    ).length;

    // The very first onOpen is the initial connect and is intentionally skipped;
    // a SECOND onOpen represents a reconnect and must trigger a resync.
    expect(typeof capturedOnOpen).toBe("function");
    act(() => {
      capturedOnOpen?.(); // initial connect (skipped)
    });
    act(() => {
      capturedOnOpen?.(); // reconnect (should resync)
    });

    await waitFor(() => {
      const jobsCallsNow = fetchMock.mock.calls.filter((c) =>
        String(c[0]).endsWith("/api/jobs"),
      ).length;
      expect(jobsCallsNow).toBeGreaterThan(jobsCallsAfterMount);
    });

    // Sanity: the listener was registered so WS messages would be handled.
    expect(typeof capturedListener).toBe("function");
  });

  it("hard-reloads on reconnect when the backend reports a different version", async () => {
    // Swap window.location for a stub that records reload() (jsdom's real
    // reload is unimplemented). delete-then-assign is the jsdom-safe override.
    const realLocation = window.location;
    const reloadMock = vi.fn();
    // @ts-expect-error — overriding the non-writable location for the test.
    delete window.location;
    // @ts-expect-error — install a minimal stub the hook can read + call.
    window.location = { protocol: "http:", host: "localhost:5173", reload: reloadMock };

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.includes("/api/updates/status"))
        return Promise.resolve(okJson({ current_version: `${__APP_VERSION__}-next` }));
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useJobManagement(false));

    // First onOpen = initial connect (skipped); second = reconnect (version check).
    act(() => {
      capturedOnOpen?.();
    });
    await act(async () => {
      capturedOnOpen?.();
    });

    await waitFor(() => {
      expect(reloadMock).toHaveBeenCalled();
    });

    // @ts-expect-error — restore the real location for other tests.
    window.location = realLocation;
  });

  it("does NOT reload on reconnect when the version matches", async () => {
    const realLocation = window.location;
    const reloadMock = vi.fn();
    // @ts-expect-error — overriding the non-writable location for the test.
    delete window.location;
    // @ts-expect-error — install a minimal stub the hook can read + call.
    window.location = { protocol: "http:", host: "localhost:5173", reload: reloadMock };

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.includes("/api/updates/status"))
        return Promise.resolve(okJson({ current_version: __APP_VERSION__ }));
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useJobManagement(false));

    act(() => {
      capturedOnOpen?.();
    });
    await act(async () => {
      capturedOnOpen?.();
    });

    // Wait until the status check has actually run, then assert no reload.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/updates/status")),
      ).toBe(true);
    });
    expect(reloadMock).not.toHaveBeenCalled();

    // @ts-expect-error — restore the real location for other tests.
    window.location = realLocation;
  });

  it("seeds updateStatus (incl. is_frozen) from /api/updates/status on mount", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.includes("/api/updates/status"))
        return Promise.resolve(
          okJson({
            state: "ready",
            current_version: __APP_VERSION__,
            latest_version: "9.9.9",
            release_url: "https://example.com",
            is_frozen: true,
          }),
        );
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJobManagement(false));

    await waitFor(() => {
      expect(result.current.updateStatus?.state).toBe("ready");
    });
    // Regression: is_frozen must survive the REST seed. It was dropped on the WS
    // push, defaulting to false and hiding the "Restart now" button on frozen builds.
    expect(result.current.updateStatus?.is_frozen).toBe(true);
    expect(result.current.updateStatus?.latest_version).toBe("9.9.9");
  });
});

// ---------------------------------------------------------------------------
// fingerprint_disclosure_required WS event — surfaces disclosure state.
// ---------------------------------------------------------------------------

describe("fingerprint_disclosure_required WS handling", () => {
  beforeEach(() => {
    toastErrorMock.mockClear();
    capturedOnOpen = undefined;
    capturedListener = undefined;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces disclosure when the WS event fires", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.endsWith("/api/jobs")) return Promise.resolve(okJson([]));
      if (urlStr.includes("/titles")) return Promise.resolve(okJson([]));
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJobManagement(false));

    // Wait for the initial fetch to complete and listener to be registered.
    await waitFor(() => {
      expect(typeof capturedListener).toBe("function");
    });

    expect(result.current.disclosure).toBeNull();

    const msg: FingerprintDisclosureRequiredMessage = {
      type: "fingerprint_disclosure_required",
      pending_count: 2,
      pseudonym: "p-123",
      server_url: "https://fp.example.com/v1",
    };

    act(() => {
      capturedListener!(msg as WebSocketMessage);
    });

    expect(result.current.disclosure?.pending_count).toBe(2);
    expect(result.current.disclosure?.pseudonym).toBe("p-123");
    expect(result.current.disclosure?.server_url).toBe("https://fp.example.com/v1");
  });

  it("clears disclosure when clearDisclosure is called", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.endsWith("/api/jobs")) return Promise.resolve(okJson([]));
      if (urlStr.includes("/titles")) return Promise.resolve(okJson([]));
      return Promise.resolve(okJson([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useJobManagement(false));

    await waitFor(() => {
      expect(typeof capturedListener).toBe("function");
    });

    const msg: FingerprintDisclosureRequiredMessage = {
      type: "fingerprint_disclosure_required",
      pending_count: 3,
      pseudonym: "p-456",
      server_url: "https://fp.example.com/v1",
    };

    act(() => {
      capturedListener!(msg as WebSocketMessage);
    });

    expect(result.current.disclosure).not.toBeNull();

    act(() => {
      result.current.clearDisclosure();
    });

    expect(result.current.disclosure).toBeNull();
  });
});

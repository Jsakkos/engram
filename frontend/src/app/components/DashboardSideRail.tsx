import { useEffect, useMemo, useRef, useState } from "react";
import type { DiscTitle, Job, JobState, TitleState } from "../../types";
import { SvBar, SvBarChart, SvLabel, SvPanel, sv } from "./synapse";

interface ActivityEvent {
  id: string;
  ts: number;
  level: "info" | "warn" | "error" | "ok";
  subject: string;
  message: string;
}

interface Props {
  jobs: Job[];
  titlesMap: Record<number, DiscTitle[]>;
}

const ACTIVE_JOB_STATES: JobState[] = ["identifying", "ripping", "matching", "organizing"];
const TERMINAL_TITLE_STATES: TitleState[] = ["matched", "completed", "review", "failed"];

/** Parse "4.5x (20.3 M/s)" → 20.3. Returns 0 for unparseable inputs. */
function parseMbPerSec(speedStr: string | undefined): number {
  if (!speedStr) return 0;
  const m = speedStr.match(/([\d.]+)\s*M\/s/);
  return m ? parseFloat(m[1]) : 0;
}

function formatBytes(bytes: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "—";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}

const LEVEL_COLOR: Record<ActivityEvent["level"], string> = {
  info: sv.cyan,
  warn: sv.yellow,
  error: sv.red,
  ok: sv.green,
};

/**
 * Right-column dashboard side rail.
 *
 * Two render modes, switched on `stats.active`:
 *   - **≤1 active job** → compact mode. The card already owns progress %, speed,
 *     bytes, and track count, so the rail shrinks to a thin status strip plus
 *     unique signals only: throughput sparkline + activity log.
 *   - **≥2 active jobs** → aggregate mode. Restores the big % readout and
 *     System · status grid, but switches the percent source from a single
 *     focusedJob to titles-completed-across-all-jobs (so it isn't a literal
 *     duplicate of any card).
 *
 * Throughput tracks the *focused* job in both modes (most-recently-started
 * ripping/matching/organizing/identifying) — a uniquely rail-only signal.
 */
export function DashboardSideRail({ jobs, titlesMap }: Props) {
  // Pick the focused job: first ripping, then matching, then identifying.
  const focusedJob = useMemo<Job | null>(() => {
    const order: JobState[] = ["ripping", "matching", "organizing", "identifying"];
    for (const state of order) {
      const j = jobs.find((j) => j.state === state);
      if (j) return j;
    }
    return null;
  }, [jobs]);

  const mbPerSec = parseMbPerSec(focusedJob?.current_speed);

  // Rolling 60-sample throughput buffer (one sample per second).
  const [throughput, setThroughput] = useState<number[]>([]);
  const throughputRef = useRef(throughput);
  throughputRef.current = throughput;

  useEffect(() => {
    const id = setInterval(() => {
      setThroughput((prev) => {
        const next = [...prev, mbPerSec].slice(-60);
        return next;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [mbPerSec]);

  // Reset throughput when focused job changes
  const focusedJobIdRef = useRef<number | null>(null);
  useEffect(() => {
    const id = focusedJob?.id ?? null;
    if (focusedJobIdRef.current !== id) {
      focusedJobIdRef.current = id;
      setThroughput([]);
    }
  }, [focusedJob?.id]);

  // Activity log: derive from state-change diff vs previous tick
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const prevJobsRef = useRef<Map<number, JobState>>(new Map());
  const prevTitlesRef = useRef<Map<number, TitleState>>(new Map());

  useEffect(() => {
    const newEvents: ActivityEvent[] = [];
    const now = Date.now();

    // Job state transitions
    const currentJobs = new Map<number, JobState>();
    for (const job of jobs) {
      currentJobs.set(job.id, job.state);
      const prev = prevJobsRef.current.get(job.id);
      if (prev && prev !== job.state) {
        const subject = `JOB ${job.id}`;
        const titleStr = job.detected_title || job.volume_label || "unknown";
        const level: ActivityEvent["level"] =
          job.state === "failed" ? "error" :
          job.state === "completed" ? "ok" :
          job.state === "review_needed" ? "warn" :
          "info";
        newEvents.push({
          id: `${job.id}-${job.state}-${now}`,
          ts: now,
          level,
          subject,
          message: `${job.state.toUpperCase()} · ${titleStr}`,
        });
      }
    }
    prevJobsRef.current = currentJobs;

    // Title state transitions
    const currentTitles = new Map<number, TitleState>();
    Object.values(titlesMap).flat().forEach((title) => {
      currentTitles.set(title.id, title.state);
      const prev = prevTitlesRef.current.get(title.id);
      if (prev && prev !== title.state) {
        const level: ActivityEvent["level"] =
          title.state === "failed" ? "error" :
          title.state === "matched" || title.state === "completed" ? "ok" :
          title.state === "review" ? "warn" :
          "info";
        newEvents.push({
          id: `t${title.id}-${title.state}-${now}`,
          ts: now,
          level,
          subject: `T${String(title.title_index).padStart(2, "0")}`,
          message:
            title.state === "matched" && title.matched_episode
              ? `MATCHED · ${title.matched_episode}`
              : `${title.state.toUpperCase()}`,
        });
      }
    });
    prevTitlesRef.current = currentTitles;

    if (newEvents.length > 0) {
      setEvents((prev) => [...prev, ...newEvents].slice(-12));
    }
  }, [jobs, titlesMap]);

  const visibleEvents = events.slice(-6).reverse();

  // Aggregate stats across all jobs
  const stats = useMemo(() => {
    let active = 0;
    let completed = 0;
    let review = 0;
    let titlesDone = 0;
    let titlesTotal = 0;
    let bytesRipped = 0;
    let bytesTotal = 0;
    for (const j of jobs) {
      if (ACTIVE_JOB_STATES.includes(j.state)) active++;
      else if (j.state === "completed") completed++;
      else if (j.state === "review_needed") review++;
      const ts = titlesMap[j.id] ?? [];
      titlesTotal += ts.length;
      titlesDone += ts.filter((t) => TERMINAL_TITLE_STATES.includes(t.state)).length;
      if (ACTIVE_JOB_STATES.includes(j.state)) {
        const jobTotal = ts.reduce((s, t) => s + (t.file_size_bytes || t.expected_size_bytes || 0), 0);
        bytesTotal += jobTotal;
        bytesRipped += (j.progress_percent / 100) * jobTotal;
      }
    }
    return { active, completed, review, titlesDone, titlesTotal, bytesRipped, bytesTotal };
  }, [jobs, titlesMap]);

  const aggregatePercent =
    stats.titlesTotal > 0 ? (stats.titlesDone / stats.titlesTotal) * 100 : 0;
  const aggregateRatio = aggregatePercent / 100;
  const isAggregateMode = stats.active >= 2;

  return (
    <aside
      data-testid="sv-side-rail"
      data-mode={isAggregateMode ? "aggregate" : "compact"}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        position: "sticky",
        top: 14,
      }}
    >
      {isAggregateMode ? (
        // ── Aggregate mode (2+ jobs): titles-based percent across all active jobs ─
        <SvPanel pad={18} testid="sv-side-rail-progress">
          <SvLabel>Aggregate · {stats.active} jobs</SvLabel>
          <div
            data-testid="sv-side-rail-numeric"
            style={{
              fontFamily: sv.display,
              fontSize: 64,
              fontWeight: 700,
              letterSpacing: "0.04em",
              color: sv.cyan,
              textShadow: `0 0 24px ${sv.cyan}55`,
              lineHeight: 1,
              marginTop: 14,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {aggregatePercent.toFixed(0)}
            <span style={{ fontSize: 28, marginLeft: 4, color: sv.cyanDim }}>%</span>
          </div>
          <div style={{ marginTop: 14 }}>
            <SvBar value={aggregateRatio} height={4} />
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              marginTop: 10,
              fontFamily: sv.mono,
              fontSize: 11,
              letterSpacing: "0.06em",
              color: sv.inkDim,
            }}
          >
            <span data-testid="sv-side-rail-tracks">
              {stats.titlesDone}/{stats.titlesTotal} tracks
            </span>
            <span data-testid="sv-side-rail-bytes">
              {formatBytes(stats.bytesRipped)} / {formatBytes(stats.bytesTotal)}
            </span>
          </div>
        </SvPanel>
      ) : (
        // ── Compact mode (0–1 jobs): thin status strip, no duplicated facts ──
        <SvPanel pad={14} testid="sv-side-rail-active-strip">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
            }}
          >
            <SvLabel>{stats.active === 1 ? "Active · operation" : "Idle · awaiting"}</SvLabel>
            <span
              style={{
                fontFamily: sv.mono,
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.20em",
                textTransform: "uppercase",
                color: stats.active === 1 ? sv.cyanHi : sv.inkFaint,
                textShadow: stats.active === 1 ? `0 0 8px ${sv.cyan}66` : undefined,
              }}
              data-testid="sv-side-rail-active-count"
            >
              {stats.active === 1 ? "› streaming" : "› no signal"}
            </span>
          </div>
        </SvPanel>
      )}

      {/* Throughput sparkline — unique to the rail (never on the card) */}
      <SvPanel pad={18} testid="sv-side-rail-throughput">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <SvLabel>Throughput · 60s</SvLabel>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 9,
              letterSpacing: "0.22em",
              color: sv.inkFaint,
              textTransform: "uppercase",
            }}
          >
            MB/s
          </span>
        </div>
        <div style={{ marginTop: 14 }}>
          {/* min=50 MB/s anchors the chart's vertical scale: a steady ~24 MB/s
              ripping stream renders as ~half-height bars (variance visible),
              and spikes above 50 MB/s still grow the chart upward. Without
              this floor, auto-scale collapses every bar to 100% of the
              observed max, producing a flat wall of identical bars. */}
          <SvBarChart values={throughput} accent="cyan" height={70} min={50} />
        </div>
      </SvPanel>

      {/* System · status — only in aggregate mode (2+ jobs) */}
      {isAggregateMode && (
        <SvPanel pad={18} testid="sv-side-rail-stats">
          <SvLabel>System · status</SvLabel>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 10,
              marginTop: 14,
            }}
          >
            <Stat label="Active" value={stats.active} accent={sv.magenta} />
            <Stat label="Done" value={stats.completed} accent={sv.green} />
            <Stat label="Review" value={stats.review} accent={sv.yellow} />
          </div>
        </SvPanel>
      )}

      {/* Activity log — unique to the rail */}
      <SvPanel pad={18} testid="sv-side-rail-log" style={{ flex: 1, minHeight: 180 }}>
        <SvLabel>Activity · log</SvLabel>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            marginTop: 14,
            fontFamily: sv.mono,
            fontSize: 10,
            letterSpacing: "0.04em",
          }}
        >
          {visibleEvents.length === 0 ? (
            <span
              style={{
                color: sv.inkFaint,
                fontSize: 9,
                letterSpacing: "0.22em",
                textTransform: "uppercase",
              }}
            >
              › idle · awaiting events
            </span>
          ) : (
            visibleEvents.map((e) => (
              <div
                key={e.id}
                data-testid="sv-side-rail-log-entry"
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto auto 1fr",
                  gap: 8,
                  alignItems: "baseline",
                }}
              >
                <span style={{ color: sv.inkFaint }}>{formatTime(e.ts)}</span>
                <span style={{ color: LEVEL_COLOR[e.level], fontWeight: 600 }}>
                  {e.subject}
                </span>
                <span style={{ color: sv.inkDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {e.message}
                </span>
              </div>
            ))
          )}
        </div>
      </SvPanel>
    </aside>
  );
}

function Stat({ label, value, accent }: { label: string; value: string | number; accent: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontFamily: sv.mono,
          fontSize: 9,
          letterSpacing: "0.22em",
          color: sv.inkFaint,
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: sv.display,
          fontSize: 24,
          fontWeight: 700,
          color: accent,
          letterSpacing: "0.04em",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
    </div>
  );
}

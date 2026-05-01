/**
 * LibraryPage — Synapse v2 poster grid of completed archives.
 *
 * Data source: GET /api/jobs/history → filter to state === 'completed'.
 * No new backend endpoint; the library is derived from the existing
 * job history API. Posters use 2-letter initials from detected_title
 * with rotating cyan / magenta / yellow gradient backgrounds.
 */

import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { Plus } from "lucide-react";
import { SvAtmosphere, SvPanel, SvLabel, SvTopBar, SvStatusBar, sv } from "../app/components/synapse";
import { FEATURES } from "../config/constants";

interface HistoryJob {
  id: number;
  volume_label: string;
  content_type: string;
  state: string;
  detected_title: string | null;
  detected_season: number | null;
  total_titles: number;
  created_at: string | null;
  completed_at: string | null;
}

type Filter = "all" | "movie" | "tv";

const POSTER_PALETTE = [
  { bg: sv.cyan, text: sv.cyanHi, glow: sv.cyan },
  { bg: sv.magenta, text: sv.magentaHi, glow: sv.magenta },
  { bg: sv.yellow, text: sv.amber, glow: sv.yellow },
];

function paletteFor(seed: string) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  return POSTER_PALETTE[hash % POSTER_PALETTE.length];
}

function initialsOf(title?: string | null, fallback?: string): string {
  const source = (title || fallback || "??").trim();
  if (!source) return "??";
  const parts = source.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function isRecent(completedAt: string | null): boolean {
  if (!completedAt) return false;
  const t = new Date(completedAt).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t < 24 * 3600 * 1000;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" });
}

export default function LibraryPage() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<HistoryJob[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isConnected] = useState(true); // Library is read-only; live status not relevant

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/jobs/history?limit=200&state_filter=completed");
        if (!res.ok) return;
        const data: HistoryJob[] = await res.json();
        if (!cancelled) setJobs(data);
      } catch {
        // Non-fatal — empty library is a valid state
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const completed = useMemo(() => jobs.filter((j) => j.state === "completed"), [jobs]);
  const filtered = useMemo(() => {
    if (filter === "all") return completed;
    return completed.filter((j) => j.content_type === filter);
  }, [completed, filter]);

  const stats = useMemo(() => {
    const byType = { movie: 0, tv: 0 };
    completed.forEach((j) => {
      if (j.content_type === "movie") byType.movie++;
      else if (j.content_type === "tv") byType.tv++;
    });
    return { total: completed.length, ...byType };
  }, [completed]);

  const navItems = [
    { label: "DASHBOARD", to: "/" },
    { label: "REVIEW", to: "/review" },
    { label: "LIBRARY", to: "/library" },
    { label: "HISTORY", to: "/history" },
    { label: "CONTRIBUTE", to: "/contribute", show: FEATURES.DISCDB },
  ];

  return (
    <SvAtmosphere>
      <SvTopBar
        isConnected={isConnected}
        version={__APP_VERSION__}
        navItems={navItems}
        onSettingsClick={() => navigate("/")}
      />

      <div style={{ padding: "28px 28px 80px", flex: 1 }}>
        {/* Header strip */}
        <div style={{ marginBottom: 24, display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <SvLabel>Library</SvLabel>
            <h1
              style={{
                fontFamily: sv.display,
                fontSize: 36,
                fontWeight: 700,
                letterSpacing: "0.04em",
                color: sv.cyanHi,
                textShadow: `0 0 16px ${sv.cyan}55`,
                margin: "8px 0 0",
              }}
              data-testid="sv-library-title"
            >
              {isLoading ? "Loading…" : `${stats.total} title${stats.total === 1 ? "" : "s"} archived`}
            </h1>
          </div>

          {/* Filter pills */}
          <div style={{ display: "flex", gap: 8 }}>
            {(["all", "movie", "tv"] as const).map((f) => {
              const active = filter === f;
              const counts = { all: stats.total, movie: stats.movie, tv: stats.tv };
              const labels = { all: "ALL", movie: "MOVIES", tv: "TV" };
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  data-testid={`sv-library-filter-${f}`}
                  data-active={active ? "true" : "false"}
                  style={{
                    padding: "6px 14px",
                    fontFamily: sv.mono,
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: "0.20em",
                    textTransform: "uppercase",
                    color: active ? sv.cyanHi : sv.inkDim,
                    background: active ? "rgba(94,234,212,0.10)" : "transparent",
                    border: `1px solid ${active ? sv.lineHi : sv.line}`,
                    cursor: "pointer",
                  }}
                >
                  {labels[f]} · {counts[f]}
                </button>
              );
            })}
          </div>
        </div>

        {/* Poster grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 16,
          }}
          data-testid="sv-library-grid"
        >
          {filtered.map((job, i) => (
            <PosterCard key={job.id} job={job} index={i} onClick={() => navigate(`/history/${job.id}`)} />
          ))}
          <AddCard />
        </div>

        {!isLoading && filtered.length === 0 && (
          <div
            style={{
              marginTop: 60,
              textAlign: "center",
              fontFamily: sv.mono,
              fontSize: 12,
              letterSpacing: "0.20em",
              color: sv.inkFaint,
              textTransform: "uppercase",
            }}
          >
            <span style={{ color: sv.cyan }}>›</span>{" "}
            {filter === "all"
              ? "No archives yet — insert a disc to start your library"
              : `No ${filter === "movie" ? "movies" : "TV shows"} archived yet`}
          </div>
        )}
      </div>

      <SvStatusBar
        activeCount={0}
        completedCount={stats.total}
        isConnected={isConnected}
        version={__APP_VERSION__}
        driveLabel="LIBRARY VIEW"
      />
    </SvAtmosphere>
  );
}

function PosterCard({
  job,
  index,
  onClick,
}: {
  job: HistoryJob;
  index: number;
  onClick: () => void;
}) {
  const initials = initialsOf(job.detected_title, job.volume_label);
  const palette = paletteFor(job.detected_title || job.volume_label);
  const recent = isRecent(job.completed_at);
  const isMovie = job.content_type === "movie";

  return (
    <motion.button
      onClick={onClick}
      data-testid="sv-library-card"
      data-content-type={job.content_type}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index, 12) * 0.04 }}
      whileHover={{ y: -2 }}
      style={{
        position: "relative",
        padding: 0,
        background: "transparent",
        border: "none",
        cursor: "pointer",
        textAlign: "left",
      }}
    >
      <SvPanel pad={0} accent={`${palette.bg}55`} style={{ overflow: "hidden" }}>
        {/* Poster — 2:3 aspect with grid overlay + giant initials */}
        <div
          style={{
            position: "relative",
            aspectRatio: "2 / 3",
            background: `linear-gradient(135deg, ${palette.bg}33, ${sv.bg1} 70%)`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
          }}
        >
          {/* Grid overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage:
                "linear-gradient(rgba(94,234,212,0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(94,234,212,0.12) 1px, transparent 1px)",
              backgroundSize: "20px 20px",
              opacity: 0.6,
              pointerEvents: "none",
            }}
          />

          {/* Initials */}
          <span
            style={{
              fontFamily: sv.display,
              fontSize: 88,
              fontWeight: 700,
              letterSpacing: "0.04em",
              color: palette.text,
              textShadow: `0 0 24px ${palette.glow}88`,
              position: "relative",
            }}
          >
            {initials}
          </span>

          {/* Type pill */}
          <span
            style={{
              position: "absolute",
              top: 8,
              left: 8,
              padding: "2px 8px",
              fontFamily: sv.mono,
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.20em",
              color: isMovie ? sv.magenta : sv.cyan,
              background: "rgba(10,14,24,0.85)",
              border: `1px solid ${isMovie ? sv.magenta : sv.cyan}55`,
            }}
          >
            {(job.content_type || "?").toUpperCase()}
          </span>

          {/* NEW badge */}
          {recent && (
            <span
              style={{
                position: "absolute",
                top: 8,
                right: 8,
                padding: "2px 8px",
                fontFamily: sv.mono,
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.20em",
                color: sv.bg0,
                background: sv.green,
                boxShadow: `0 0 8px ${sv.green}88`,
              }}
            >
              NEW
            </span>
          )}
        </div>

        {/* Footer — title / metadata */}
        <div style={{ padding: 12, borderTop: `1px solid ${sv.line}` }}>
          <div
            style={{
              fontFamily: sv.display,
              fontSize: 14,
              fontWeight: 600,
              letterSpacing: "0.04em",
              color: sv.ink,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {job.detected_title || job.volume_label}
          </div>
          <div
            className="sv-tnum"
            style={{
              fontFamily: sv.mono,
              fontSize: 10,
              letterSpacing: "0.10em",
              color: sv.inkDim,
              marginTop: 4,
            }}
          >
            {job.content_type === "tv" && job.detected_season != null
              ? `S${String(job.detected_season).padStart(2, "0")} · `
              : ""}
            {job.total_titles} {job.total_titles === 1 ? "title" : "titles"} · {formatDate(job.completed_at)}
          </div>
        </div>
      </SvPanel>
    </motion.button>
  );
}

function AddCard() {
  return (
    <div
      style={{
        position: "relative",
        aspectRatio: "2 / 3",
        border: `1px dashed ${sv.lineMid}`,
        background: "rgba(10,14,24,0.4)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
      }}
      data-testid="sv-library-add-card"
    >
      <Plus size={36} color={sv.inkFaint} />
      <span
        style={{
          fontFamily: sv.mono,
          fontSize: 10,
          letterSpacing: "0.20em",
          color: sv.inkFaint,
          textTransform: "uppercase",
          textAlign: "center",
          padding: "0 16px",
        }}
      >
        Insert disc to add
      </span>
    </div>
  );
}

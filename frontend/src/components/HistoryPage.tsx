import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Film,
  Tv,
  Clock,
  BarChart3,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

interface HistoryJob {
  id: number;
  volume_label: string;
  content_type: string;
  state: string;
  detected_title: string | null;
  detected_season: number | null;
  error_message: string | null;
  classification_source: string;
  total_titles: number;
  created_at: string | null;
  completed_at: string | null;
  cleared_at: string | null;
}

interface Stats {
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  tv_count: number;
  movie_count: number;
  total_titles_ripped: number;
  avg_processing_seconds: number | null;
  common_errors: { message: string; count: number }[];
  recent_jobs: HistoryJob[];
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatCard({
  label,
  value,
  icon,
  color,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="border-2 border-transparent bg-black/80 p-4"
      style={{
        borderImage: `linear-gradient(135deg, ${color}66, ${color}33) 1`,
        boxShadow: `0 0 15px ${color}22`,
      }}
    >
      <div className="flex items-center gap-3">
        <div style={{ color }} className="opacity-80">
          {icon}
        </div>
        <div>
          <div
            className="text-2xl font-bold font-mono"
            style={{ color, textShadow: `0 0 8px ${color}88` }}
          >
            {value}
          </div>
          <div className="text-xs text-slate-400 font-mono uppercase tracking-wider">
            {label}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

export default function HistoryPage() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [history, setHistory] = useState<HistoryJob[]>([]);
  const [page, setPage] = useState(1);
  const [filterType, setFilterType] = useState<string>("");
  const [filterState, setFilterState] = useState<string>("");
  const [hasMore, setHasMore] = useState(true);
  const perPage = 20;

  useEffect(() => {
    fetch("/api/jobs/stats")
      .then((r) => r.json())
      .then(setStats)
      .catch(() => {});
  }, []);

  const fetchHistory = useCallback(() => {
    const params = new URLSearchParams({
      page: String(page),
      per_page: String(perPage),
    });
    if (filterType) params.set("content_type", filterType);
    if (filterState) params.set("state", filterState);

    fetch(`/api/jobs/history?${params}`)
      .then((r) => r.json())
      .then((data: HistoryJob[]) => {
        setHistory(data);
        setHasMore(data.length === perPage);
      })
      .catch(() => {});
  }, [page, filterType, filterState]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  return (
    <div className="min-h-screen bg-black">
      {/* Grid background */}
      <div className="fixed inset-0 opacity-10 pointer-events-none">
        <div
          className="h-full w-full"
          style={{
            backgroundImage: `
              linear-gradient(rgba(6, 182, 212, 0.3) 1px, transparent 1px),
              linear-gradient(90deg, rgba(6, 182, 212, 0.3) 1px, transparent 1px)
            `,
            backgroundSize: "50px 50px",
          }}
        />
      </div>

      {/* Header */}
      <div
        className="border-b-2 border-cyan-500/30 backdrop-blur-xl bg-black/80 sticky top-0 z-10"
        style={{
          boxShadow:
            "0 0 20px rgba(6, 182, 212, 0.2), 0 0 40px rgba(236, 72, 153, 0.1)",
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate("/")}
              className="text-cyan-400 hover:text-cyan-300 transition-colors"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
            <div className="flex items-center gap-3">
              <BarChart3
                className="w-6 h-6 text-cyan-400"
                style={{
                  filter: "drop-shadow(0 0 8px rgba(6, 182, 212, 0.8))",
                }}
              />
              <h1
                className="text-xl sm:text-2xl font-bold text-cyan-400 tracking-wider font-mono uppercase"
                style={{
                  textShadow:
                    "0 0 10px rgba(6, 182, 212, 1), 0 0 30px rgba(6, 182, 212, 0.6)",
                }}
              >
                Job History & Analytics
              </h1>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-8">
        {/* Stats Grid */}
        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard
              label="Total Jobs"
              value={stats.total_jobs}
              icon={<BarChart3 className="w-5 h-5" />}
              color="#06b6d4"
            />
            <StatCard
              label="Completed"
              value={stats.completed_jobs}
              icon={<CheckCircle2 className="w-5 h-5" />}
              color="#10b981"
            />
            <StatCard
              label="Failed"
              value={stats.failed_jobs}
              icon={<XCircle className="w-5 h-5" />}
              color="#ef4444"
            />
            <StatCard
              label="TV Shows"
              value={stats.tv_count}
              icon={<Tv className="w-5 h-5" />}
              color="#f59e0b"
            />
            <StatCard
              label="Movies"
              value={stats.movie_count}
              icon={<Film className="w-5 h-5" />}
              color="#ec4899"
            />
            <StatCard
              label="Avg Time"
              value={
                stats.avg_processing_seconds
                  ? formatDuration(stats.avg_processing_seconds)
                  : "—"
              }
              icon={<Clock className="w-5 h-5" />}
              color="#8b5cf6"
            />
          </div>
        )}

        {/* Common Errors */}
        {stats && stats.common_errors.length > 0 && (
          <div className="border-2 border-red-500/30 bg-black/80 p-4">
            <h2 className="text-sm font-mono font-bold text-red-400 uppercase tracking-wider mb-3">
              &gt; Common Errors
            </h2>
            <div className="space-y-2">
              {stats.common_errors.map((err, i) => (
                <div
                  key={i}
                  className="flex items-start gap-3 text-xs font-mono"
                >
                  <span className="text-red-400 font-bold min-w-[2rem] text-right">
                    x{err.count}
                  </span>
                  <span className="text-slate-400 break-all">
                    {err.message}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs font-mono text-slate-500 uppercase tracking-wider">
            Filter:
          </span>
          <select
            value={filterType}
            onChange={(e) => {
              setFilterType(e.target.value);
              setPage(1);
            }}
            className="bg-black border-2 border-slate-700 text-slate-300 font-mono text-xs px-3 py-1.5 focus:border-cyan-500/50 outline-none"
          >
            <option value="">All Types</option>
            <option value="tv">TV</option>
            <option value="movie">Movie</option>
          </select>
          <select
            value={filterState}
            onChange={(e) => {
              setFilterState(e.target.value);
              setPage(1);
            }}
            className="bg-black border-2 border-slate-700 text-slate-300 font-mono text-xs px-3 py-1.5 focus:border-cyan-500/50 outline-none"
          >
            <option value="">All States</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>

        {/* History Table */}
        <div className="border-2 border-cyan-500/20 bg-black/80 overflow-x-auto">
          <table className="w-full text-xs sm:text-sm font-mono">
            <thead>
              <tr className="border-b-2 border-cyan-500/20 text-left">
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold">
                  Title
                </th>
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold hidden sm:table-cell">
                  Type
                </th>
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold">
                  State
                </th>
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold hidden md:table-cell">
                  Titles
                </th>
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold hidden lg:table-cell">
                  Source
                </th>
                <th className="px-4 py-3 text-cyan-400 uppercase tracking-wider font-bold hidden sm:table-cell">
                  Date
                </th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-8 text-center text-slate-500"
                  >
                    No archived jobs found
                  </td>
                </tr>
              ) : (
                history.map((job) => (
                  <tr
                    key={job.id}
                    className="border-b border-slate-800 hover:bg-cyan-500/5 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <div className="text-slate-200">
                        {job.detected_title || job.volume_label}
                      </div>
                      {job.detected_title && (
                        <div className="text-[10px] text-slate-500">
                          {job.volume_label}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 hidden sm:table-cell">
                      <span
                        className={`uppercase ${
                          job.content_type === "tv"
                            ? "text-amber-400"
                            : job.content_type === "movie"
                              ? "text-magenta-400"
                              : "text-slate-500"
                        }`}
                      >
                        {job.content_type}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {job.state === "completed" ? (
                        <span className="text-green-400 flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3" /> OK
                        </span>
                      ) : (
                        <span className="text-red-400 flex items-center gap-1">
                          <XCircle className="w-3 h-3" /> FAIL
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400 hidden md:table-cell">
                      {job.total_titles}
                    </td>
                    <td className="px-4 py-3 text-slate-500 hidden lg:table-cell">
                      {job.classification_source}
                    </td>
                    <td className="px-4 py-3 text-slate-500 hidden sm:table-cell">
                      {formatDate(job.completed_at || job.created_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="flex items-center gap-1 px-3 py-2 font-mono text-xs uppercase tracking-wider border-2 border-slate-700 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="w-4 h-4" /> Prev
          </button>
          <span className="text-xs font-mono text-slate-500">
            Page {page}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="flex items-center gap-1 px-3 py-2 font-mono text-xs uppercase tracking-wider border-2 border-slate-700 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

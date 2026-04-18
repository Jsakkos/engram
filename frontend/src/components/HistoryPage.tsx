import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
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
  Bug,
  X,
  Copy,
  Database,
  AlertTriangle,
  Disc3,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { FEATURES } from "../config/constants";

interface HistoryJob {
  id: number;
  volume_label: string;
  content_type: string;
  state: string;
  detected_title: string | null;
  detected_season: number | null;
  error_message: string | null;
  classification_source: string;
  classification_confidence: number;
  total_titles: number;
  content_hash: string | null;
  discdb_slug: string | null;
  disc_number: number;
  tmdb_id: number | null;
  created_at: string | null;
  completed_at: string | null;
  cleared_at: string | null;
}

interface JobDetailTitle {
  id: number;
  job_id: number;
  title_index: number;
  duration_seconds: number;
  file_size_bytes: number;
  chapter_count: number;
  is_selected: boolean;
  output_filename: string | null;
  matched_episode: string | null;
  match_confidence: number;
  state: string;
  video_resolution: string | null;
  edition: string | null;
  organized_from: string | null;
  organized_to: string | null;
  is_extra: boolean;
}

interface JobDetail {
  id: number;
  volume_label: string;
  drive_id: string;
  content_type: string;
  state: string;
  detected_title: string | null;
  detected_season: number | null;
  disc_number: number;
  error_message: string | null;
  review_reason: string | null;
  classification_source: string;
  classification_confidence: number;
  tmdb_id: number | null;
  tmdb_name: string | null;
  is_ambiguous_movie: boolean;
  content_hash: string | null;
  discdb_slug: string | null;
  discdb_disc_slug: string | null;
  discdb_mappings: Array<{
    index: number;
    title_type: string;
    episode_title: string;
    season: number | null;
    episode: number | null;
    duration_seconds: number;
    size_bytes: number;
  }> | null;
  created_at: string | null;
  completed_at: string | null;
  cleared_at: string | null;
  subtitle_status: string | null;
  subtitles_downloaded: number;
  subtitles_total: number;
  subtitles_failed: number;
  staging_path: string | null;
  final_path: string | null;
  titles: JobDetailTitle[];
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

function formatTitleDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "\u2014";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateShort(iso: string | null): string {
  if (!iso) return "\u2014";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
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
      className="rounded-lg border border-cyan-500/20 bg-navy-800/80 p-4"
      style={{
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

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "#10b981" : pct >= 50 ? "#f59e0b" : "#ef4444";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-navy-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-mono" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}

function TitleStateBadge({ state }: { state: string }) {
  const config: Record<string, { color: string; label: string }> = {
    completed: { color: "text-green-400", label: "OK" },
    failed: { color: "text-red-400", label: "FAIL" },
    matched: { color: "text-cyan-400", label: "MATCHED" },
    review: { color: "text-amber-400", label: "REVIEW" },
    pending: { color: "text-slate-500", label: "PENDING" },
    ripping: { color: "text-magenta-400", label: "RIPPING" },
    matching: { color: "text-violet-400", label: "MATCHING" },
  };
  const c = config[state] || { color: "text-slate-500", label: state.toUpperCase() };
  return <span className={`text-[10px] font-mono uppercase ${c.color}`}>{c.label}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="text-slate-500 hover:text-cyan-400 transition-colors"
      title="Copy to clipboard"
    >
      {copied ? (
        <CheckCircle2 className="w-3 h-3 text-green-400" />
      ) : (
        <Copy className="w-3 h-3" />
      )}
    </button>
  );
}

function JobDetailPanel({
  detail,
  loading,
  onClose,
}: {
  detail: JobDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onClose]);

  // Close on Escape key
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <motion.div
      ref={panelRef}
      initial={{ x: "100%" }}
      animate={{ x: 0 }}
      exit={{ x: "100%" }}
      transition={{ type: "spring", damping: 25, stiffness: 300 }}
      className="fixed top-0 right-0 h-full w-full sm:w-[560px] bg-navy-900 border-l border-cyan-500/20 z-50 overflow-y-auto"
      style={{
        boxShadow: "-4px 0 30px rgba(6, 182, 212, 0.1)",
      }}
    >
      {/* Panel Header */}
      <div className="sticky top-0 z-10 bg-navy-900/95 backdrop-blur-sm border-b border-cyan-500/20 px-5 py-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-mono font-bold text-cyan-400 uppercase tracking-wider">
            &gt; Job Detail
          </h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-cyan-400 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
        </div>
      ) : detail ? (
        <div className="px-5 py-4 space-y-5">
          {/* Title & Status */}
          <div>
            <h3 className="text-lg font-bold text-slate-100">
              {detail.detected_title || detail.volume_label}
            </h3>
            <div className="flex items-center gap-2 mt-1">
              <span
                className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${
                  detail.content_type === "tv"
                    ? "text-amber-400 border-amber-400/30"
                    : detail.content_type === "movie"
                      ? "text-magenta-400 border-magenta-400/30"
                      : "text-slate-500 border-slate-500/30"
                }`}
              >
                {detail.content_type}
              </span>
              <span
                className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${
                  detail.state === "completed"
                    ? "text-green-400 border-green-400/30"
                    : "text-red-400 border-red-400/30"
                }`}
              >
                {detail.state}
              </span>
              {detail.detected_season && (
                <span className="text-[10px] font-mono text-slate-400">
                  Season {detail.detected_season}
                </span>
              )}
              {detail.disc_number > 1 && (
                <span className="text-[10px] font-mono text-slate-400">
                  Disc {detail.disc_number}
                </span>
              )}
            </div>
            {detail.detected_title && (
              <div className="text-[10px] font-mono text-slate-500 mt-1">
                {detail.volume_label} on {detail.drive_id}
              </div>
            )}
          </div>

          {/* Error Details */}
          {detail.error_message && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
              <div className="text-[10px] font-mono font-bold text-red-400 uppercase tracking-wider mb-2">
                <AlertTriangle className="w-3 h-3 inline mr-1" />
                Error
              </div>
              <pre className="text-xs font-mono text-red-300 whitespace-pre-wrap break-all max-h-40 overflow-y-auto">
                {detail.error_message}
              </pre>
            </div>
          )}

          {/* Processing Timeline */}
          <div>
            <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
              &gt; Timeline
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-xs font-mono">
                <span className="text-slate-500 w-20">Created</span>
                <ArrowRight className="w-3 h-3 text-cyan-500/40" />
                <span className="text-slate-300">{formatDateShort(detail.created_at)}</span>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <span className="text-slate-500 w-20">
                  {detail.state === "completed" ? "Completed" : "Failed"}
                </span>
                <ArrowRight className="w-3 h-3 text-cyan-500/40" />
                <span className="text-slate-300">{formatDateShort(detail.completed_at)}</span>
              </div>
              {detail.created_at && detail.completed_at && (
                <div className="flex items-center gap-2 text-xs font-mono">
                  <span className="text-slate-500 w-20">Duration</span>
                  <ArrowRight className="w-3 h-3 text-cyan-500/40" />
                  <span className="text-cyan-400">
                    {formatDuration(
                      (new Date(detail.completed_at).getTime() -
                        new Date(detail.created_at).getTime()) /
                        1000
                    )}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Classification */}
          <div>
            <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
              &gt; Classification
            </div>
            <div className="space-y-2 rounded-lg border border-cyan-500/10 bg-navy-800/60 p-3">
              <div className="flex justify-between items-center text-xs font-mono">
                <span className="text-slate-400">Source</span>
                <span className="text-slate-200">{detail.classification_source}</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-xs font-mono text-slate-400">Confidence</span>
                <ConfidenceBar value={detail.classification_confidence} />
              </div>
              {detail.tmdb_id && (
                <div className="flex justify-between items-center text-xs font-mono">
                  <span className="text-slate-400">TMDB</span>
                  <span className="text-slate-200">
                    {detail.tmdb_name || `ID ${detail.tmdb_id}`}
                  </span>
                </div>
              )}
              {detail.is_ambiguous_movie && (
                <div className="text-[10px] font-mono text-amber-400">
                  Ambiguous movie (multiple possible main features)
                </div>
              )}
              {detail.review_reason && (
                <div className="flex justify-between items-start text-xs font-mono">
                  <span className="text-slate-400">Review Reason</span>
                  <span className="text-amber-400 text-right max-w-[60%]">
                    {detail.review_reason}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* TheDiscDB */}
          {FEATURES.DISCDB && (
            <div>
              <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
                <Database className="w-3 h-3 inline mr-1" />
                TheDiscDB
              </div>
              <div className="rounded-lg border border-cyan-500/10 bg-navy-800/60 p-3 space-y-2">
                {detail.content_hash ? (
                  <>
                    <div className="flex justify-between items-center text-xs font-mono">
                      <span className="text-slate-400">Content Hash</span>
                      <div className="flex items-center gap-1.5">
                        <code className="text-cyan-300 text-[10px]">
                          {detail.content_hash.slice(0, 16)}...
                        </code>
                        <CopyButton text={detail.content_hash} />
                      </div>
                    </div>
                    {detail.discdb_slug && (
                      <div className="flex justify-between items-center text-xs font-mono">
                        <span className="text-slate-400">Title</span>
                        <span className="text-slate-200">{detail.discdb_slug}</span>
                      </div>
                    )}
                    {detail.discdb_disc_slug && (
                      <div className="flex justify-between items-center text-xs font-mono">
                        <span className="text-slate-400">Disc</span>
                        <span className="text-slate-200">{detail.discdb_disc_slug}</span>
                      </div>
                    )}
                    {!detail.discdb_slug && (
                      <div className="text-[10px] font-mono text-amber-400">
                        Disc fingerprint computed but not found in TheDiscDB
                      </div>
                    )}
                  </>
                ) : (
                  <div className="text-[10px] font-mono text-slate-500">
                    No disc fingerprint available (scan may have failed before computation)
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Subtitle Info */}
          {detail.subtitle_status && (
            <div>
              <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
                &gt; Subtitles
              </div>
              <div className="rounded-lg border border-cyan-500/10 bg-navy-800/60 p-3 text-xs font-mono space-y-1">
                <div className="flex justify-between">
                  <span className="text-slate-400">Status</span>
                  <span className="text-slate-200">{detail.subtitle_status}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Downloaded</span>
                  <span className="text-slate-200">
                    {detail.subtitles_downloaded}/{detail.subtitles_total}
                    {detail.subtitles_failed > 0 && (
                      <span className="text-red-400 ml-1">({detail.subtitles_failed} failed)</span>
                    )}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Track Breakdown */}
          <div>
            <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
              <Disc3 className="w-3 h-3 inline mr-1" />
              Tracks ({detail.titles.length})
            </div>
            {detail.titles.length > 0 ? (
              <div className="space-y-1.5">
                {detail.titles.map((t) => (
                  <div
                    key={t.id}
                    className="rounded border border-cyan-500/10 bg-navy-800/60 px-3 py-2"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-slate-500">
                          #{t.title_index}
                        </span>
                        <span className="text-xs font-mono text-slate-300">
                          {formatTitleDuration(t.duration_seconds)}
                        </span>
                        <span className="text-[10px] font-mono text-slate-500">
                          {formatBytes(t.file_size_bytes)}
                        </span>
                        {t.video_resolution && (
                          <span className="text-[10px] font-mono text-violet-400">
                            {t.video_resolution}
                          </span>
                        )}
                      </div>
                      <TitleStateBadge state={t.state} />
                    </div>
                    {(t.matched_episode || t.edition || t.is_extra) && (
                      <div className="flex items-center gap-2 mt-1">
                        {t.matched_episode && (
                          <span className="text-[10px] font-mono text-cyan-400">
                            {t.matched_episode}
                          </span>
                        )}
                        {t.edition && (
                          <span className="text-[10px] font-mono text-amber-400">
                            {t.edition}
                          </span>
                        )}
                        {t.is_extra && (
                          <span className="text-[10px] font-mono text-slate-500">extra</span>
                        )}
                        {t.match_confidence > 0 && (
                          <span className="text-[10px] font-mono text-slate-500">
                            ({Math.round(t.match_confidence * 100)}% match)
                          </span>
                        )}
                      </div>
                    )}
                    {t.organized_to && (
                      <div className="text-[10px] font-mono text-slate-500 mt-1 truncate">
                        {t.organized_to}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs font-mono text-slate-500 rounded border border-cyan-500/10 bg-navy-800/60 px-3 py-4 text-center">
                No tracks found (scan may have failed before disc analysis)
              </div>
            )}
          </div>

          {/* Paths */}
          {(detail.staging_path || detail.final_path) && (
            <div>
              <div className="text-[10px] font-mono font-bold text-cyan-400 uppercase tracking-wider mb-2">
                &gt; Paths
              </div>
              <div className="rounded-lg border border-cyan-500/10 bg-navy-800/60 p-3 text-xs font-mono space-y-1">
                {detail.staging_path && (
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-400 shrink-0">Staging</span>
                    <span className="text-slate-500 truncate">{detail.staging_path}</span>
                  </div>
                )}
                {detail.final_path && (
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-400 shrink-0">Library</span>
                    <span className="text-slate-500 truncate">{detail.final_path}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Bug Report for this specific job */}
          <div className="pt-2 border-t border-cyan-500/10">
            <a
              href={`/api/diagnostics/report?job_id=${detail.id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-xs font-mono text-slate-500 hover:text-red-400 transition-colors"
              onClick={async (e) => {
                e.preventDefault();
                try {
                  const resp = await fetch(`/api/diagnostics/report?job_id=${detail.id}`);
                  if (resp.ok) {
                    const data = await resp.json();
                    window.open(data.github_url, "_blank");
                  }
                } catch {
                  // silently fail
                }
              }}
            >
              <Bug className="w-3 h-3" />
              Report bug for this job
            </a>
          </div>
        </div>
      ) : null}
    </motion.div>
  );
}

export default function HistoryPage() {
  const navigate = useNavigate();
  const { jobId: urlJobId } = useParams<{ jobId: string }>();
  const [stats, setStats] = useState<Stats | null>(null);
  const [history, setHistory] = useState<HistoryJob[]>([]);
  const [page, setPage] = useState(1);
  const [filterType, setFilterType] = useState<string>("");
  const [filterState, setFilterState] = useState<string>("");
  const [hasMore, setHasMore] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(
    urlJobId ? parseInt(urlJobId, 10) : null
  );
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
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

  // Fetch job detail when a job is selected
  useEffect(() => {
    if (!selectedJobId) {
      setJobDetail(null);
      return;
    }
    setDetailLoading(true);
    fetch(`/api/jobs/${selectedJobId}/detail`)
      .then((r) => {
        if (!r.ok) throw new Error("Not found");
        return r.json();
      })
      .then((data: JobDetail) => {
        setJobDetail(data);
      })
      .catch(() => {
        setJobDetail(null);
      })
      .finally(() => {
        setDetailLoading(false);
      });
  }, [selectedJobId]);

  const handleRowClick = (jobId: number) => {
    if (jobId === selectedJobId) {
      setSelectedJobId(null);
      navigate("/history", { replace: true });
    } else {
      setSelectedJobId(jobId);
      navigate(`/history/${jobId}`, { replace: true });
    }
  };

  const handleCloseDetail = useCallback(() => {
    setSelectedJobId(null);
    navigate("/history", { replace: true });
  }, [navigate]);

  const handleReportBug = async () => {
    setReportLoading(true);
    try {
      const resp = await fetch("/api/diagnostics/report");
      if (!resp.ok) throw new Error("Failed to generate report");
      const data = await resp.json();
      window.open(data.github_url, "_blank");
    } catch {
      alert("Could not generate bug report. Is the backend running?");
    } finally {
      setReportLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-navy-900 circuit-bg">
      {/* Header */}
      <div
        className="border-b border-cyan-500/20 backdrop-blur-xl bg-navy-900/80 sticky top-0 z-10"
        style={{
          boxShadow:
            "0 0 20px rgba(6, 182, 212, 0.2), 0 0 40px rgba(236, 72, 153, 0.1)",
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4">
          <div className="flex items-center justify-between">
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
            <button
              onClick={handleReportBug}
              disabled={reportLoading}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-red-500/30 text-red-400 hover:border-red-500/60 hover:bg-red-500/10 transition-all font-mono text-xs uppercase tracking-wider disabled:opacity-50"
            >
              <Bug className="w-4 h-4" />
              <span className="hidden sm:inline">
                {reportLoading ? "Generating..." : "Report Bug"}
              </span>
            </button>
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
                  : "\u2014"
              }
              icon={<Clock className="w-5 h-5" />}
              color="#8b5cf6"
            />
          </div>
        )}

        {/* Common Errors */}
        {stats && stats.common_errors.length > 0 && (
          <div className="rounded-lg border border-red-500/30 bg-navy-800/80 p-4">
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
            className="bg-navy-800 border border-cyan-500/20 rounded-md text-slate-300 font-mono text-xs px-3 py-1.5 focus:border-cyan-500/50 outline-none"
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
            className="bg-navy-800 border border-cyan-500/20 rounded-md text-slate-300 font-mono text-xs px-3 py-1.5 focus:border-cyan-500/50 outline-none"
          >
            <option value="">All States</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>

        {/* History Table */}
        <div className="rounded-lg border border-cyan-500/20 bg-navy-800/80 overflow-x-auto">
          <table className="w-full text-xs sm:text-sm font-mono">
            <thead>
              <tr className="border-b border-cyan-500/20 text-left">
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
                    No completed or failed jobs yet
                  </td>
                </tr>
              ) : (
                history.map((job) => (
                  <tr
                    key={job.id}
                    onClick={() => handleRowClick(job.id)}
                    className={`border-b border-navy-700/50 hover:bg-cyan-500/5 transition-colors cursor-pointer ${
                      selectedJobId === job.id ? "bg-cyan-500/10" : ""
                    }`}
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
            className="flex items-center gap-1 px-3 py-2 rounded-md font-mono text-xs uppercase tracking-wider border border-cyan-500/20 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="w-4 h-4" /> Prev
          </button>
          <span className="text-xs font-mono text-slate-500">
            Page {page}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="flex items-center gap-1 px-3 py-2 rounded-md font-mono text-xs uppercase tracking-wider border border-cyan-500/20 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Detail Panel Overlay */}
      <AnimatePresence>
        {selectedJobId && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/40 z-40"
            />
            <JobDetailPanel
              detail={jobDetail}
              loading={detailLoading}
              onClose={handleCloseDetail}
            />
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

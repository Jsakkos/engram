import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowLeft,
  Upload,
  SkipForward,
  Package,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Send,
  ExternalLink,
  Link2,
  Unlink,
  CheckCircle2,
} from "lucide-react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "../app/components/ui/tooltip";
import EnhanceWizard, { type TitleInfo } from "./EnhanceWizard";

interface ContributionJob {
  id: number;
  volume_label: string;
  content_type: string;
  detected_title: string | null;
  detected_season: number | null;
  content_hash: string | null;
  completed_at: string | null;
  export_status: "pending" | "exported" | "skipped" | "submitted";
  submitted_at: string | null;
  contribute_url: string | null;
  release_group_id: string | null;
  upc_code: string | null;
  asin: string | null;
  release_date: string | null;
}

interface ContributionStats {
  pending: number;
  exported: number;
  skipped: number;
  submitted: number;
}

interface Config {
  discdb_contributions_enabled: boolean;
  discdb_contribution_tier: number;
  discdb_export_path: string;
  discdb_api_key_set: boolean;
  discdb_api_url: string;
}

// Generate a consistent color for a release group UUID
function releaseGroupColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = id.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 70%, 60%)`;
}

export default function ContributePage() {
  const [jobs, setJobs] = useState<ContributionJob[]>([]);
  const [stats, setStats] = useState<ContributionStats>({
    pending: 0,
    exported: 0,
    skipped: 0,
    submitted: 0,
  });
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedJob, setExpandedJob] = useState<number | null>(null);
  const [detailsExpanded, setDetailsExpanded] = useState<Set<number>>(new Set());
  const [titleCache, setTitleCache] = useState<Map<number, TitleInfo[]>>(new Map());
  const [actionInProgress, setActionInProgress] = useState<number | null>(null);
  const [selectedJobs, setSelectedJobs] = useState<Set<number>>(new Set());
  const [groupSubmitting, setGroupSubmitting] = useState<string | null>(null);
  const [groupResult, setGroupResult] = useState<{
    submitted: number;
    failed: number;
    results?: unknown[];
    contribute_url?: string | null;
    error?: string;
  } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [titleErrors, setTitleErrors] = useState<Set<number>>(new Set());

  const fetchData = useCallback(async () => {
    try {
      const [jobsRes, statsRes, configRes] = await Promise.all([
        fetch("/api/contributions"),
        fetch("/api/contributions/stats"),
        fetch("/api/config"),
      ]);
      if (jobsRes.ok) setJobs(await jobsRes.json());
      if (statsRes.ok) setStats(await statsRes.json());
      if (configRes.ok) {
        const data = await configRes.json();
        setConfig({
          discdb_contributions_enabled: data.discdb_contributions_enabled,
          discdb_contribution_tier: data.discdb_contribution_tier,
          discdb_export_path: data.discdb_export_path,
          discdb_api_key_set: data.discdb_api_key_set,
          discdb_api_url: data.discdb_api_url,
        });
      }
    } catch (error) {
      console.error("Failed to load contribution data:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const showError = (msg: string) => {
    setActionError(msg);
    setTimeout(() => setActionError(null), 5000);
  };

  const handleExport = async (jobId: number) => {
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/export`, { method: "POST" });
      if (res.ok) {
        await fetchData();
      } else {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || "Export failed");
      }
    } catch {
      showError("Network error during export");
    } finally {
      setActionInProgress(null);
    }
  };

  const handleSkip = async (jobId: number) => {
    if (!window.confirm("Skip this disc? It won't appear in the contribution queue.")) return;
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/skip`, { method: "POST" });
      if (res.ok) {
        await fetchData();
      } else {
        showError("Failed to skip job");
      }
    } catch {
      showError("Network error");
    } finally {
      setActionInProgress(null);
    }
  };

  const fetchTitles = useCallback(async (jobId: number) => {
    if (titleCache.has(jobId)) return titleCache.get(jobId)!;
    try {
      const res = await fetch(`/api/jobs/${jobId}/titles`);
      if (res.ok) {
        const data = await res.json();
        const titles: TitleInfo[] = data.map((t: Record<string, unknown>) => ({
          id: t.id as number,
          title_index: t.title_index as number,
          duration_seconds: t.duration_seconds as number,
          matched_episode: (t.matched_episode as string) || null,
          match_source: (t.match_source as string) || null,
          match_confidence: (t.match_confidence as number) || 0,
          is_extra: (t.is_extra as boolean) || false,
          extra_description: (t.extra_description as string) || null,
        }));
        setTitleCache((prev) => new Map(prev).set(jobId, titles));
        return titles;
      }
      setTitleErrors((prev) => new Set(prev).add(jobId));
    } catch (error) {
      console.error("Failed to fetch titles:", error);
      setTitleErrors((prev) => new Set(prev).add(jobId));
    }
    return [];
  }, [titleCache]);

  const toggleDetails = async (jobId: number) => {
    setDetailsExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
        fetchTitles(jobId);
      }
      return next;
    });
  };

  const formatDuration = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const handleSubmit = async (jobId: number) => {
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/submit`, { method: "POST" });
      if (res.ok) {
        await fetchData();
      } else {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || data.error || "Submission failed");
      }
    } catch {
      showError("Network error during submission");
    } finally {
      setActionInProgress(null);
    }
  };

  const handleExportAll = async () => {
    const pending = jobs.filter((j) => j.export_status === "pending");
    for (const job of pending) {
      await handleExport(job.id);
    }
  };

  const handleGroupSelected = async () => {
    const ids = Array.from(selectedJobs);
    if (ids.length < 2) return;
    try {
      const res = await fetch("/api/contributions/release-group", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: ids }),
      });
      if (res.ok) {
        setSelectedJobs(new Set());
        await fetchData();
      } else {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || "Failed to create release group");
      }
    } catch {
      showError("Network error creating release group");
    }
  };

  const handleSubmitGroup = async (releaseGroupId: string) => {
    setGroupSubmitting(releaseGroupId);
    setGroupResult(null);
    try {
      const res = await fetch(`/api/contributions/release-group/${releaseGroupId}/submit`, {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok) {
        setGroupResult(data);
        await fetchData();
      } else {
        setGroupResult({ submitted: 0, failed: 0, results: [], error: data.detail || "Submission failed" });
      }
    } catch (error) {
      console.error("Failed to submit group:", error);
      setGroupResult({ submitted: 0, failed: 0, results: [], error: "Network error" });
    } finally {
      setGroupSubmitting(null);
    }
  };

  const handleUngroup = async (jobId: number) => {
    if (!window.confirm("Remove this disc from its release group?")) return;
    try {
      const res = await fetch(`/api/contributions/${jobId}/release-group`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ release_group_id: null }),
      });
      if (res.ok) {
        await fetchData();
      } else {
        showError("Failed to remove from release group");
      }
    } catch {
      showError("Network error");
    }
  };

  const toggleSelection = (jobId: number) => {
    setSelectedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-navy-900 circuit-bg flex items-center justify-center">
        <div className="text-cyan-400 font-mono animate-pulse">Loading...</div>
      </div>
    );
  }

  // Show setup prompt when contributions are disabled
  if (config && !config.discdb_contributions_enabled) {
    return (
      <div className="min-h-screen bg-navy-900 circuit-bg relative overflow-hidden">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
          <Link
            to="/"
            className="inline-flex items-center gap-2 text-slate-500 hover:text-cyan-400 font-mono text-sm mb-8 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> Dashboard
          </Link>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-center py-16"
          >
            <Package className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <h2 className="text-xl font-bold text-cyan-400 font-mono mb-3">
              CONTRIBUTIONS DISABLED
            </h2>
            <p className="text-slate-400 font-mono text-sm max-w-lg mx-auto mb-6">
              Help grow TheDiscDB by sharing disc metadata from your rips. Enable contributions in
              Settings to get started.
            </p>
            <p className="text-slate-500 font-mono text-xs">
              Go to Settings &gt; TheDiscDB Contributions to enable
            </p>
          </motion.div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-navy-900 circuit-bg relative overflow-hidden">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 pb-20">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <Link
              to="/"
              className="inline-flex items-center gap-2 text-slate-500 hover:text-cyan-400 font-mono text-sm mb-3 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" /> Dashboard
            </Link>
            <h1 className="text-2xl font-bold text-cyan-400 font-mono tracking-wider">
              CONTRIBUTE TO THEDISCDB
            </h1>
            <p className="text-slate-500 font-mono text-xs mt-1">
              Share disc metadata to help others identify their discs automatically
            </p>
          </div>

          {/* Stats badges */}
          <div className="flex items-center gap-3">
            <div className="text-center">
              <div className="text-lg font-bold text-amber-400 font-mono">{stats.pending}</div>
              <div className="text-xs text-slate-500 font-mono">Pending</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-green-400 font-mono">{stats.exported}</div>
              <div className="text-xs text-slate-500 font-mono">Exported</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-cyan-400 font-mono">{stats.submitted}</div>
              <div className="text-xs text-slate-500 font-mono">Submitted</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-slate-500 font-mono">{stats.skipped}</div>
              <div className="text-xs text-slate-500 font-mono">Skipped</div>
            </div>
          </div>
        </div>

        {/* Bulk actions */}
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          {stats.pending > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={handleExportAll}
                  className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider text-cyan-400 border border-cyan-500/40 rounded-md hover:bg-cyan-500/10 transition-all"
                >
                  Export All Pending ({stats.pending})
                </button>
              </TooltipTrigger>
              <TooltipContent>Export all pending discs at once</TooltipContent>
            </Tooltip>
          )}
          {selectedJobs.size >= 2 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={handleGroupSelected}
                  className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider text-magenta-400 border border-magenta-500/40 rounded-md hover:bg-magenta-500/10 transition-all flex items-center gap-2"
                >
                  <Link2 className="w-3.5 h-3.5" /> Group Selected ({selectedJobs.size})
                </button>
              </TooltipTrigger>
              <TooltipContent>Link selected discs as a multi-disc set</TooltipContent>
            </Tooltip>
          )}

          {/* Submit Group buttons for each release group with exported jobs */}
          {config?.discdb_api_key_set &&
            (() => {
              const groups = new Map<string, ContributionJob[]>();
              for (const job of jobs) {
                if (job.release_group_id) {
                  const existing = groups.get(job.release_group_id) || [];
                  existing.push(job);
                  groups.set(job.release_group_id, existing);
                }
              }
              return Array.from(groups.entries())
                .filter(([, groupJobs]) =>
                  groupJobs.every((j) => j.export_status === "exported") && groupJobs.length >= 2
                )
                .map(([groupId, groupJobs]) => (
                  <button
                    key={groupId}
                    onClick={() => handleSubmitGroup(groupId)}
                    disabled={groupSubmitting === groupId}
                    className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider text-cyan-400 border border-cyan-500/40 rounded-md hover:bg-cyan-500/10 transition-all flex items-center gap-2 disabled:opacity-50"
                  >
                    <Send className="w-3.5 h-3.5" />
                    {groupSubmitting === groupId
                      ? `Submitting ${groupJobs.length} discs...`
                      : `Submit Group (${groupJobs.length} discs)`}
                  </button>
                ));
            })()}
        </div>

        {/* Action error banner */}
        {actionError && (
          <div className="mb-6 px-4 py-3 border border-red-500/30 rounded-md bg-red-500/5">
            <p className="text-red-400 font-mono text-xs">{actionError}</p>
          </div>
        )}

        {/* Group submission result */}
        {groupResult && (
          <div className={`mb-6 px-4 py-3 border rounded-md ${groupResult.error ? "border-red-500/30 bg-red-500/5" : "border-cyan-500/30 bg-cyan-500/5"}`}>
            <p className={`font-mono text-xs ${groupResult.error ? "text-red-400" : "text-cyan-400"}`}>
              {groupResult.error
                ? `Batch submit failed: ${groupResult.error}`
                : `Batch submit complete: ${groupResult.submitted} submitted, ${groupResult.failed} failed.`}
            </p>
            {groupResult.contribute_url && (
              <a
                href={groupResult.contribute_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-cyan-400 font-mono text-xs mt-2 hover:underline"
              >
                <ExternalLink className="w-3 h-3" /> Continue on TheDiscDB
              </a>
            )}
          </div>
        )}

        {/* API key warning */}
        {config && !config.discdb_api_key_set && (
          <div className="mb-6 px-4 py-3 border border-amber-500/30 rounded-md bg-amber-500/5">
            <p className="text-amber-400 font-mono text-xs">
              No TheDiscDB API key configured. You can export locally, but submission requires an API
              key. Click the gear icon in the header, then go to TheDiscDB Contributions.
            </p>
          </div>
        )}

        {/* Job list */}
        {jobs.length === 0 ? (
          <div className="text-center py-16">
            <Package className="w-12 h-12 text-slate-600 mx-auto mb-3" />
            <p className="text-slate-500 font-mono text-sm">
              No completed jobs to contribute yet
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {/* Table header */}
            <div className="grid grid-cols-[auto_auto_1fr_auto_auto_auto_auto] gap-4 px-4 py-2 text-xs font-mono font-bold text-slate-600 uppercase tracking-wider border-b border-navy-600">
              <span className="w-5" />
              <span className="w-5" />
              <span>Title</span>
              <span>Type</span>
              <span>Hash</span>
              <span>Status</span>
              <span>Actions</span>
            </div>

            <AnimatePresence mode="popLayout">
              {jobs.map((job) => (
                <motion.div
                  key={job.id}
                  layout
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="border border-navy-600/50 rounded-md bg-navy-800/40"
                >
                  {/* Main row */}
                  <div className="grid grid-cols-[auto_auto_1fr_auto_auto_auto_auto] gap-4 items-center px-4 py-3 font-mono text-sm">
                    {/* Checkbox */}
                    <input
                      type="checkbox"
                      checked={selectedJobs.has(job.id)}
                      onChange={() => toggleSelection(job.id)}
                      className="w-4 h-4 accent-cyan-400"
                    />

                    {/* Details expand */}
                    <button
                      onClick={() => toggleDetails(job.id)}
                      className="text-slate-500 hover:text-cyan-400 transition-colors"
                    >
                      <ChevronRight
                        className={`w-4 h-4 transition-transform ${
                          detailsExpanded.has(job.id) ? "rotate-90" : ""
                        }`}
                      />
                    </button>

                    {/* Title */}
                    <div className="flex items-center gap-2">
                      {job.release_group_id && (
                        <span
                          className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                          style={{ backgroundColor: releaseGroupColor(job.release_group_id) }}
                          title={`Release group: ${job.release_group_id.slice(0, 8)}...`}
                        />
                      )}
                      <span className="text-slate-300">
                        {job.detected_title || job.volume_label}
                      </span>
                      {job.detected_season && (
                        <span className="text-slate-500 text-xs">
                          Season {job.detected_season}
                        </span>
                      )}
                    </div>

                    {/* Type */}
                    <span
                      className={`text-xs font-bold uppercase ${
                        job.content_type === "movie"
                          ? "text-magenta-400"
                          : job.content_type === "tv"
                            ? "text-cyan-400"
                            : "text-slate-500"
                      }`}
                    >
                      {job.content_type}
                    </span>

                    {/* Hash */}
                    <span className="text-xs text-slate-600 font-mono">
                      {job.content_hash ? job.content_hash.slice(0, 8) + "..." : "N/A"}
                    </span>

                    {/* Status */}
                    <span
                      className={`text-xs font-bold uppercase px-2 py-0.5 rounded ${
                        job.export_status === "submitted"
                          ? "text-cyan-300 bg-cyan-500/10 border border-cyan-500/20"
                          : job.export_status === "exported"
                            ? "text-green-400 bg-green-500/10 border border-green-500/20"
                            : job.export_status === "skipped"
                              ? "text-slate-400 bg-slate-500/10 border border-slate-500/20"
                              : "text-amber-400 bg-amber-500/10 border border-amber-500/20"
                      }`}
                    >
                      {job.export_status}
                    </span>

                    {/* Actions */}
                    <div className="flex items-center gap-1">
                      {job.export_status === "pending" && (
                        <>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                onClick={() => handleExport(job.id)}
                                disabled={actionInProgress === job.id}
                                className="text-xs text-cyan-400 border border-cyan-500/30 px-2.5 py-1 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-1"
                              >
                                <Upload className="w-3 h-3" /> Export
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>Generate disc metadata for local review</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                onClick={() => {
                                  setExpandedJob(expandedJob === job.id ? null : job.id);
                                  if (expandedJob !== job.id) fetchTitles(job.id);
                                }}
                                className="text-xs text-magenta-400 border border-magenta-500/30 px-2.5 py-1 rounded hover:bg-magenta-500/10 flex items-center gap-1"
                              >
                                {expandedJob === job.id ? (
                                  <ChevronUp className="w-3 h-3" />
                                ) : (
                                  <ChevronDown className="w-3 h-3" />
                                )}
                                Enhance
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>Add UPC, ASIN, cover art, and extras info</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                onClick={() => handleSkip(job.id)}
                                disabled={actionInProgress === job.id}
                                className="text-xs text-slate-500 border border-slate-500/30 px-2.5 py-1 rounded hover:bg-slate-500/10 disabled:opacity-50 flex items-center gap-1"
                              >
                                <SkipForward className="w-3 h-3" /> Skip
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>Mark this disc as not for contribution</TooltipContent>
                          </Tooltip>
                        </>
                      )}
                      {job.export_status === "exported" && (
                        <>
                          {config?.discdb_api_key_set && (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <button
                                  onClick={() => handleSubmit(job.id)}
                                  disabled={actionInProgress === job.id}
                                  className="text-xs text-cyan-400 border border-cyan-500/30 px-2.5 py-1 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-1"
                                >
                                  <Send className="w-3 h-3" /> Submit
                                </button>
                              </TooltipTrigger>
                              <TooltipContent>Send disc metadata to TheDiscDB</TooltipContent>
                            </Tooltip>
                          )}
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                onClick={() => {
                                  setExpandedJob(expandedJob === job.id ? null : job.id);
                                  if (expandedJob !== job.id) fetchTitles(job.id);
                                }}
                                className="text-xs text-magenta-400 border border-magenta-500/30 px-2.5 py-1 rounded hover:bg-magenta-500/10 flex items-center gap-1"
                              >
                                {expandedJob === job.id ? (
                                  <ChevronUp className="w-3 h-3" />
                                ) : (
                                  <ChevronDown className="w-3 h-3" />
                                )}
                                Enhance
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>Add UPC, ASIN, cover art, and extras info</TooltipContent>
                          </Tooltip>
                        </>
                      )}
                      {job.export_status === "submitted" && job.contribute_url && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <a
                              href={job.contribute_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-cyan-400 border border-cyan-500/30 px-2.5 py-1 rounded hover:bg-cyan-500/10 flex items-center gap-1"
                            >
                              <ExternalLink className="w-3 h-3" /> Continue on TheDiscDB
                            </a>
                          </TooltipTrigger>
                          <TooltipContent>Complete this contribution on TheDiscDB</TooltipContent>
                        </Tooltip>
                      )}
                      {job.export_status === "submitted" && !job.contribute_url && (
                        <span className="text-xs text-cyan-300 flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3" /> Submitted
                        </span>
                      )}
                      {job.release_group_id && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              onClick={() => handleUngroup(job.id)}
                              className="text-xs text-slate-500 border border-slate-500/30 px-2 py-1 rounded hover:bg-slate-500/10 flex items-center gap-1"
                            >
                              <Unlink className="w-3 h-3" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent>Remove from release group</TooltipContent>
                        </Tooltip>
                      )}
                    </div>
                  </div>

                  {/* Expandable title details */}
                  <AnimatePresence>
                    {detailsExpanded.has(job.id) && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden border-t border-navy-600/50"
                      >
                        <div className="px-4 py-3 bg-navy-900/30">
                          {/* Enhanced metadata */}
                          {(job.upc_code || job.asin || job.release_date) && (
                            <div className="flex gap-4 mb-3 text-xs font-mono text-slate-400">
                              {job.upc_code && (
                                <span>UPC: <span className="text-slate-300">{job.upc_code}</span></span>
                              )}
                              {job.asin && (
                                <span>ASIN: <span className="text-slate-300">{job.asin}</span></span>
                              )}
                              {job.release_date && (
                                <span>Released: <span className="text-slate-300">{job.release_date}</span></span>
                              )}
                            </div>
                          )}
                          {titleCache.has(job.id) ? (
                            <table className="w-full text-xs font-mono">
                              <thead>
                                <tr className="text-slate-600 uppercase">
                                  <th className="text-left py-1 pr-3">#</th>
                                  <th className="text-left py-1 pr-3">Episode</th>
                                  <th className="text-left py-1 pr-3">Duration</th>
                                  <th className="text-left py-1 pr-3">Source</th>
                                  <th className="text-left py-1 pr-3">Conf.</th>
                                  <th className="text-left py-1">Type</th>
                                </tr>
                              </thead>
                              <tbody>
                                {titleCache.get(job.id)!.map((t) => (
                                  <tr key={t.id} className="text-slate-400 border-t border-navy-700/30">
                                    <td className="py-1 pr-3 text-slate-600">{t.title_index}</td>
                                    <td className="py-1 pr-3 text-slate-300">
                                      {t.matched_episode || "\u2014"}
                                    </td>
                                    <td className="py-1 pr-3">{formatDuration(t.duration_seconds)}</td>
                                    <td className="py-1 pr-3">
                                      {t.match_source && (
                                        <span className={`text-xs uppercase ${
                                          t.match_source === "discdb" ? "text-blue-400"
                                          : t.match_source === "engram" ? "text-purple-400"
                                          : "text-green-400"
                                        }`}>
                                          {t.match_source}
                                        </span>
                                      )}
                                    </td>
                                    <td className="py-1 pr-3">
                                      {t.match_confidence > 0
                                        ? `${Math.round(t.match_confidence * 100)}%`
                                        : "\u2014"}
                                    </td>
                                    <td className="py-1">
                                      {t.is_extra ? (
                                        <span className="text-amber-400">
                                          Extra{t.extra_description ? `: ${t.extra_description}` : ""}
                                        </span>
                                      ) : (
                                        <span className="text-slate-500">Episode</span>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : titleErrors.has(job.id) ? (
                            <span className="text-red-400 text-xs">Failed to load titles</span>
                          ) : (
                            <span className="text-slate-600 text-xs">Loading titles...</span>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Enhance wizard panel */}
                  <AnimatePresence>
                    {expandedJob === job.id && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden border-t border-navy-600/50"
                      >
                        <div className="px-4 py-3 bg-navy-900/50">
                          <EnhanceWizard
                            job={job}
                            titles={titleCache.get(job.id) || []}
                            onSave={() => {
                              setExpandedJob(null);
                              setTitleCache((prev) => {
                                const next = new Map(prev);
                                next.delete(job.id);
                                return next;
                              });
                              fetchData();
                            }}
                            onCancel={() => setExpandedJob(null)}
                          />
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}

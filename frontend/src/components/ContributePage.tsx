import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { ArrowLeft, Upload, SkipForward, Package, ChevronDown, ChevronUp } from "lucide-react";

interface ContributionJob {
  id: number;
  volume_label: string;
  content_type: string;
  detected_title: string | null;
  detected_season: number | null;
  content_hash: string | null;
  completed_at: string | null;
  export_status: "pending" | "exported" | "skipped";
}

interface ContributionStats {
  pending: number;
  exported: number;
  skipped: number;
}

interface Config {
  discdb_contributions_enabled: boolean;
  discdb_contribution_tier: number;
  discdb_export_path: string;
}

export default function ContributePage() {
  const [jobs, setJobs] = useState<ContributionJob[]>([]);
  const [stats, setStats] = useState<ContributionStats>({ pending: 0, exported: 0, skipped: 0 });
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedJob, setExpandedJob] = useState<number | null>(null);
  const [upcInput, setUpcInput] = useState("");
  const [actionInProgress, setActionInProgress] = useState<number | null>(null);

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

  const handleExport = async (jobId: number) => {
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/export`, { method: "POST" });
      if (res.ok) await fetchData();
    } finally {
      setActionInProgress(null);
    }
  };

  const handleSkip = async (jobId: number) => {
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/skip`, { method: "POST" });
      if (res.ok) await fetchData();
    } finally {
      setActionInProgress(null);
    }
  };

  const handleEnhance = async (jobId: number) => {
    setActionInProgress(jobId);
    try {
      const res = await fetch(`/api/contributions/${jobId}/enhance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upc_code: upcInput || null }),
      });
      if (res.ok) {
        setExpandedJob(null);
        setUpcInput("");
        await fetchData();
      }
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
              Help grow TheDiscDB by sharing disc metadata from your rips.
              Enable contributions in Settings to get started.
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
              <div className="text-lg font-bold text-slate-500 font-mono">{stats.skipped}</div>
              <div className="text-xs text-slate-500 font-mono">Skipped</div>
            </div>
          </div>
        </div>

        {/* Bulk action */}
        {stats.pending > 0 && (
          <div className="mb-6">
            <button
              onClick={handleExportAll}
              className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider text-cyan-400 border border-cyan-500/40 rounded-md hover:bg-cyan-500/10 transition-all"
            >
              Export All Pending ({stats.pending})
            </button>
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
            <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 px-4 py-2 text-xs font-mono font-bold text-slate-600 uppercase tracking-wider border-b border-navy-600">
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
                  <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 items-center px-4 py-3 font-mono text-sm">
                    {/* Title */}
                    <div>
                      <span className="text-slate-300">
                        {job.detected_title || job.volume_label}
                      </span>
                      {job.detected_season && (
                        <span className="text-slate-500 text-xs ml-2">
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
                        job.export_status === "exported"
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
                          <button
                            onClick={() => handleExport(job.id)}
                            disabled={actionInProgress === job.id}
                            className="text-xs text-cyan-400 border border-cyan-500/30 px-2.5 py-1 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-1"
                          >
                            <Upload className="w-3 h-3" /> Export
                          </button>
                          <button
                            onClick={() =>
                              setExpandedJob(expandedJob === job.id ? null : job.id)
                            }
                            className="text-xs text-magenta-400 border border-magenta-500/30 px-2.5 py-1 rounded hover:bg-magenta-500/10 flex items-center gap-1"
                          >
                            {expandedJob === job.id ? (
                              <ChevronUp className="w-3 h-3" />
                            ) : (
                              <ChevronDown className="w-3 h-3" />
                            )}
                            Enhance
                          </button>
                          <button
                            onClick={() => handleSkip(job.id)}
                            disabled={actionInProgress === job.id}
                            className="text-xs text-slate-500 border border-slate-500/30 px-2.5 py-1 rounded hover:bg-slate-500/10 disabled:opacity-50 flex items-center gap-1"
                          >
                            <SkipForward className="w-3 h-3" /> Skip
                          </button>
                        </>
                      )}
                      {job.export_status === "exported" && (
                        <button
                          onClick={() =>
                            setExpandedJob(expandedJob === job.id ? null : job.id)
                          }
                          className="text-xs text-magenta-400 border border-magenta-500/30 px-2.5 py-1 rounded hover:bg-magenta-500/10 flex items-center gap-1"
                        >
                          {expandedJob === job.id ? (
                            <ChevronUp className="w-3 h-3" />
                          ) : (
                            <ChevronDown className="w-3 h-3" />
                          )}
                          Enhance
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Enhance panel (tier 3) */}
                  <AnimatePresence>
                    {expandedJob === job.id && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden border-t border-navy-600/50"
                      >
                        <div className="px-4 py-3 bg-navy-900/50">
                          <p className="text-xs text-slate-500 font-mono mb-3">
                            Add extra information for a full TheDiscDB contribution:
                          </p>
                          <div className="flex items-center gap-3">
                            <div className="flex-1 max-w-xs">
                              <label className="text-xs text-slate-400 font-mono block mb-1">
                                UPC Code
                              </label>
                              <input
                                type="text"
                                value={upcInput}
                                onChange={(e) => setUpcInput(e.target.value)}
                                placeholder="e.g., 883929123456"
                                className="w-full px-3 py-1.5 bg-navy-800 border border-navy-600 rounded text-sm text-slate-300 font-mono placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none"
                              />
                            </div>
                            <button
                              onClick={() => handleEnhance(job.id)}
                              disabled={actionInProgress === job.id}
                              className="mt-5 px-4 py-1.5 font-mono font-bold text-xs uppercase text-magenta-400 border border-magenta-500/40 rounded hover:bg-magenta-500/10 disabled:opacity-50"
                            >
                              Submit
                            </button>
                          </div>
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

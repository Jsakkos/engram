import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import {
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
import {
  SvActionButton,
  SvAtmosphere,
  SvBadge,
  type SvBadgeState,
  SvLabel,
  SvNotice,
  SvPageHeader,
  SvPanel,
  sv,
} from "../app/components/synapse";
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

const STATUS_BADGE: Record<ContributionJob["export_status"], { state: SvBadgeState; label: string }> = {
  submitted: { state: "matched",  label: "SUBMITTED" },
  exported:  { state: "complete", label: "EXPORTED"  },
  skipped:   { state: "queued",   label: "SKIPPED"   },
  pending:   { state: "warn",     label: "PENDING"   },
};

const TYPE_TONE: Record<string, string> = {
  movie: sv.magenta,
  tv: sv.cyan,
};

const SOURCE_TONE: Record<string, string> = {
  discdb: "#60a5fa",
  engram: sv.purple,
  user: sv.green,
};

export default function ContributePage() {
  const navigate = useNavigate();
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
      <SvAtmosphere>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 12,
              letterSpacing: "0.20em",
              textTransform: "uppercase",
              color: sv.cyan,
              animation: "svPulse 1.2s ease-in-out infinite",
            }}
          >
            › LOADING
          </span>
        </div>
      </SvAtmosphere>
    );
  }

  // Show setup prompt when contributions are disabled
  if (config && !config.discdb_contributions_enabled) {
    return (
      <SvAtmosphere>
        <SvPageHeader
          title="Contribute to TheDiscDB"
          onBack={() => navigate("/")}
        />
        <div style={{ maxWidth: 720, margin: "0 auto", padding: "64px 24px" }}>
          <SvPanel pad={32}>
            <div style={{ textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
              <Package size={56} color={sv.inkFaint} />
              <h2
                style={{
                  margin: 0,
                  fontFamily: sv.display,
                  fontSize: 22,
                  fontWeight: 700,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  color: sv.cyanHi,
                  textShadow: `0 0 12px ${sv.cyan}55`,
                }}
              >
                Contributions disabled
              </h2>
              <p
                style={{
                  margin: 0,
                  fontFamily: sv.mono,
                  fontSize: 12,
                  letterSpacing: "0.06em",
                  color: sv.inkDim,
                  maxWidth: 480,
                  lineHeight: 1.5,
                }}
              >
                Help grow TheDiscDB by sharing disc metadata from your rips. Enable contributions in
                Settings to get started.
              </p>
              <p
                style={{
                  margin: 0,
                  fontFamily: sv.mono,
                  fontSize: 10,
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: sv.inkFaint,
                }}
              >
                › Settings → TheDiscDB Contributions
              </p>
            </div>
          </SvPanel>
        </div>
      </SvAtmosphere>
    );
  }

  // Bulk action: pre-compute group buttons
  const groups = new Map<string, ContributionJob[]>();
  for (const job of jobs) {
    if (job.release_group_id) {
      const existing = groups.get(job.release_group_id) || [];
      existing.push(job);
      groups.set(job.release_group_id, existing);
    }
  }
  const submittableGroups = Array.from(groups.entries()).filter(
    ([, groupJobs]) => groupJobs.every((j) => j.export_status === "exported") && groupJobs.length >= 2,
  );

  return (
    <SvAtmosphere>
      <SvPageHeader
        title="Contribute to TheDiscDB"
        subtitle="› Share disc metadata to help others identify their discs automatically"
        onBack={() => navigate("/")}
      />

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 24px 80px" }}>
        {/* Stats row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 12,
            marginBottom: 24,
          }}
        >
          <StatCard label="Pending"   value={stats.pending}   accent={sv.amber}   />
          <StatCard label="Exported"  value={stats.exported}  accent={sv.green}   />
          <StatCard label="Submitted" value={stats.submitted} accent={sv.cyan}    />
          <StatCard label="Skipped"   value={stats.skipped}   accent={sv.inkDim}  />
        </div>

        {/* Bulk actions */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 16 }}>
          {stats.pending > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <SvActionButton tone="cyan" onClick={handleExportAll}>
                  Export all pending ({stats.pending})
                </SvActionButton>
              </TooltipTrigger>
              <TooltipContent>Export all pending discs at once</TooltipContent>
            </Tooltip>
          )}
          {selectedJobs.size >= 2 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <SvActionButton tone="magenta" onClick={handleGroupSelected}>
                  <Link2 size={12} /> Group selected ({selectedJobs.size})
                </SvActionButton>
              </TooltipTrigger>
              <TooltipContent>Link selected discs as a multi-disc set</TooltipContent>
            </Tooltip>
          )}
          {config?.discdb_api_key_set &&
            submittableGroups.map(([groupId, groupJobs]) => (
              <SvActionButton
                key={groupId}
                tone="cyan"
                onClick={() => handleSubmitGroup(groupId)}
                disabled={groupSubmitting === groupId}
              >
                <Send size={12} />
                {groupSubmitting === groupId
                  ? `Submitting ${groupJobs.length} discs…`
                  : `Submit group (${groupJobs.length} discs)`}
              </SvActionButton>
            ))}
        </div>

        {/* Banners */}
        {actionError && (
          <div style={{ marginBottom: 16 }}>
            <SvNotice tone="error">{actionError}</SvNotice>
          </div>
        )}
        {groupResult && (
          <div style={{ marginBottom: 16 }}>
            <SvNotice tone={groupResult.error ? "error" : "info"}>
              <div>
                {groupResult.error
                  ? `Batch submit failed: ${groupResult.error}`
                  : `Batch submit complete: ${groupResult.submitted} submitted, ${groupResult.failed} failed.`}
              </div>
              {groupResult.contribute_url && (
                <a
                  href={groupResult.contribute_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    marginTop: 6,
                    color: sv.cyan,
                    fontFamily: sv.mono,
                    fontSize: 11,
                    letterSpacing: "0.06em",
                  }}
                >
                  <ExternalLink size={12} /> Continue on TheDiscDB
                </a>
              )}
            </SvNotice>
          </div>
        )}
        {config && !config.discdb_api_key_set && (
          <div style={{ marginBottom: 16 }}>
            <SvNotice tone="warn">
              No TheDiscDB API key configured. You can export locally, but submission requires an API
              key. Click the gear icon in the header, then go to TheDiscDB Contributions.
            </SvNotice>
          </div>
        )}

        {/* Job list */}
        {jobs.length === 0 ? (
          <SvPanel pad={48}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
              <Package size={40} color={sv.inkFaint} />
              <p
                style={{
                  margin: 0,
                  fontFamily: sv.mono,
                  fontSize: 12,
                  letterSpacing: "0.06em",
                  color: sv.inkDim,
                }}
              >
                No completed jobs to contribute yet
              </p>
            </div>
          </SvPanel>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {/* Column headings */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "auto auto 1fr auto auto auto auto",
                columnGap: 16,
                alignItems: "center",
                padding: "8px 16px",
                fontFamily: sv.mono,
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.22em",
                textTransform: "uppercase",
                color: sv.inkFaint,
                borderBottom: `1px solid ${sv.line}`,
              }}
            >
              <span style={{ width: 18 }} />
              <span style={{ width: 18 }} />
              <span>Title</span>
              <span>Type</span>
              <span>Hash</span>
              <span>Status</span>
              <span>Actions</span>
            </div>

            <AnimatePresence mode="popLayout">
              {jobs.map((job) => {
                const status = STATUS_BADGE[job.export_status];
                return (
                  <motion.div
                    key={job.id}
                    layout
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    style={{
                      background: sv.bg1,
                      border: `1px solid ${sv.line}`,
                    }}
                  >
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "auto auto 1fr auto auto auto auto",
                        columnGap: 16,
                        alignItems: "center",
                        padding: "10px 16px",
                        fontFamily: sv.mono,
                        fontSize: 12,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedJobs.has(job.id)}
                        onChange={() => toggleSelection(job.id)}
                        style={{ width: 14, height: 14, accentColor: sv.cyan }}
                        aria-label={`Select disc ${job.detected_title || job.volume_label}`}
                      />
                      <button
                        type="button"
                        onClick={() => toggleDetails(job.id)}
                        aria-label="Toggle title details"
                        style={{
                          width: 18,
                          height: 18,
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "transparent",
                          border: 0,
                          color: sv.inkFaint,
                          cursor: "pointer",
                          transition: "color 120ms",
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = sv.cyan; }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = sv.inkFaint; }}
                      >
                        <ChevronRight
                          size={14}
                          style={{
                            transform: detailsExpanded.has(job.id) ? "rotate(90deg)" : "none",
                            transition: "transform 150ms",
                          }}
                        />
                      </button>

                      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                        {job.release_group_id && (
                          <span
                            style={{
                              width: 10,
                              height: 10,
                              borderRadius: "50%",
                              flexShrink: 0,
                              background: releaseGroupColor(job.release_group_id),
                              boxShadow: `0 0 6px ${releaseGroupColor(job.release_group_id)}88`,
                            }}
                            title={`Release group: ${job.release_group_id.slice(0, 8)}…`}
                          />
                        )}
                        <span
                          style={{
                            color: sv.ink,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {job.detected_title || job.volume_label}
                        </span>
                        {job.detected_season != null && (
                          <span style={{ color: sv.inkFaint, fontSize: 10 }}>Season {job.detected_season}</span>
                        )}
                      </div>

                      <span
                        style={{
                          color: TYPE_TONE[job.content_type] ?? sv.inkFaint,
                          fontWeight: 700,
                          fontSize: 10,
                          textTransform: "uppercase",
                          letterSpacing: "0.20em",
                        }}
                      >
                        {job.content_type}
                      </span>

                      <span style={{ color: sv.inkFaint, fontSize: 11 }}>
                        {job.content_hash ? `${job.content_hash.slice(0, 8)}…` : "N/A"}
                      </span>

                      <SvBadge state={status.state} size="sm" dot={false}>{status.label}</SvBadge>

                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        {job.export_status === "pending" && (
                          <>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <SvActionButton
                                  tone="cyan"
                                  size="sm"
                                  onClick={() => handleExport(job.id)}
                                  disabled={actionInProgress === job.id}
                                >
                                  <Upload size={11} /> Export
                                </SvActionButton>
                              </TooltipTrigger>
                              <TooltipContent>Generate disc metadata for local review</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <SvActionButton
                                  tone="magenta"
                                  size="sm"
                                  onClick={() => {
                                    setExpandedJob(expandedJob === job.id ? null : job.id);
                                    if (expandedJob !== job.id) fetchTitles(job.id);
                                  }}
                                >
                                  {expandedJob === job.id ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                  Enhance
                                </SvActionButton>
                              </TooltipTrigger>
                              <TooltipContent>Add UPC, ASIN, cover art, and extras info</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <SvActionButton
                                  tone="neutral"
                                  size="sm"
                                  onClick={() => handleSkip(job.id)}
                                  disabled={actionInProgress === job.id}
                                >
                                  <SkipForward size={11} /> Skip
                                </SvActionButton>
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
                                  <SvActionButton
                                    tone="cyan"
                                    size="sm"
                                    onClick={() => handleSubmit(job.id)}
                                    disabled={actionInProgress === job.id}
                                  >
                                    <Send size={11} /> Submit
                                  </SvActionButton>
                                </TooltipTrigger>
                                <TooltipContent>Send disc metadata to TheDiscDB</TooltipContent>
                              </Tooltip>
                            )}
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <SvActionButton
                                  tone="magenta"
                                  size="sm"
                                  onClick={() => {
                                    setExpandedJob(expandedJob === job.id ? null : job.id);
                                    if (expandedJob !== job.id) fetchTitles(job.id);
                                  }}
                                >
                                  {expandedJob === job.id ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                  Enhance
                                </SvActionButton>
                              </TooltipTrigger>
                              <TooltipContent>Add UPC, ASIN, cover art, and extras info</TooltipContent>
                            </Tooltip>
                          </>
                        )}
                        {job.export_status === "submitted" && job.contribute_url && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <SvActionButton
                                tone="cyan"
                                size="sm"
                                href={job.contribute_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <ExternalLink size={11} /> Continue on TheDiscDB
                              </SvActionButton>
                            </TooltipTrigger>
                            <TooltipContent>Complete this contribution on TheDiscDB</TooltipContent>
                          </Tooltip>
                        )}
                        {job.export_status === "submitted" && !job.contribute_url && (
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                              fontFamily: sv.mono,
                              fontSize: 10,
                              letterSpacing: "0.18em",
                              color: sv.cyanHi,
                            }}
                          >
                            <CheckCircle2 size={11} /> Submitted
                          </span>
                        )}
                        {job.release_group_id && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <SvActionButton
                                tone="neutral"
                                size="sm"
                                onClick={() => handleUngroup(job.id)}
                                ariaLabel="Remove from release group"
                              >
                                <Unlink size={11} />
                              </SvActionButton>
                            </TooltipTrigger>
                            <TooltipContent>Remove from release group</TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    </div>

                    {/* Title detail expansion */}
                    <AnimatePresence>
                      {detailsExpanded.has(job.id) && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          style={{ overflow: "hidden", borderTop: `1px solid ${sv.line}` }}
                        >
                          <div style={{ padding: "12px 16px", background: sv.bg2 }}>
                            {(job.upc_code || job.asin || job.release_date) && (
                              <div
                                style={{
                                  display: "flex",
                                  flexWrap: "wrap",
                                  gap: 16,
                                  marginBottom: 12,
                                  fontFamily: sv.mono,
                                  fontSize: 11,
                                  color: sv.inkDim,
                                }}
                              >
                                {job.upc_code && (
                                  <span>UPC: <span style={{ color: sv.ink }}>{job.upc_code}</span></span>
                                )}
                                {job.asin && (
                                  <span>ASIN: <span style={{ color: sv.ink }}>{job.asin}</span></span>
                                )}
                                {job.release_date && (
                                  <span>Released: <span style={{ color: sv.ink }}>{job.release_date}</span></span>
                                )}
                              </div>
                            )}
                            {titleCache.has(job.id) ? (
                              <TitleTable titles={titleCache.get(job.id)!} formatDuration={formatDuration} />
                            ) : titleErrors.has(job.id) ? (
                              <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.red }}>Failed to load titles</span>
                            ) : (
                              <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint }}>Loading titles…</span>
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
                          style={{ overflow: "hidden", borderTop: `1px solid ${sv.line}` }}
                        >
                          <div style={{ padding: "12px 16px", background: sv.bg2 }}>
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
                );
              })}
            </AnimatePresence>
          </div>
        )}
      </div>
    </SvAtmosphere>
  );
}

function StatCard({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <SvPanel pad={14} accent={`${accent}33`}>
      <SvLabel>{label}</SvLabel>
      <div
        style={{
          marginTop: 8,
          fontFamily: sv.display,
          fontSize: 28,
          fontWeight: 700,
          color: accent,
          letterSpacing: "0.04em",
          fontVariantNumeric: "tabular-nums",
          textShadow: `0 0 10px ${accent}55`,
          lineHeight: 1,
        }}
      >
        {value}
      </div>
    </SvPanel>
  );
}

function TitleTable({
  titles,
  formatDuration,
}: {
  titles: TitleInfo[];
  formatDuration: (s: number) => string;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: sv.mono, fontSize: 11 }}>
      <thead>
        <tr>
          {["#", "Episode", "Duration", "Source", "Conf.", "Type"].map((h) => (
            <th
              key={h}
              style={{
                textAlign: "left",
                padding: "6px 12px 6px 0",
                color: sv.inkFaint,
                fontWeight: 700,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                fontSize: 9,
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {titles.map((t) => (
          <tr key={t.id} style={{ borderTop: `1px solid ${sv.line}` }}>
            <td style={{ padding: "6px 12px 6px 0", color: sv.inkFaint }}>{t.title_index}</td>
            <td style={{ padding: "6px 12px 6px 0", color: sv.ink }}>{t.matched_episode || "—"}</td>
            <td style={{ padding: "6px 12px 6px 0", color: sv.inkDim }}>{formatDuration(t.duration_seconds)}</td>
            <td style={{ padding: "6px 12px 6px 0" }}>
              {t.match_source && (
                <SvBadge size="sm" tone={SOURCE_TONE[t.match_source] ?? sv.inkDim}>{t.match_source}</SvBadge>
              )}
            </td>
            <td style={{ padding: "6px 12px 6px 0", color: sv.inkDim }}>
              {t.match_confidence > 0 ? `${Math.round(t.match_confidence * 100)}%` : "—"}
            </td>
            <td style={{ padding: "6px 0" }}>
              {t.is_extra ? (
                <span style={{ color: sv.amber }}>
                  Extra{t.extra_description ? `: ${t.extra_description}` : ""}
                </span>
              ) : (
                <span style={{ color: sv.inkFaint }}>Episode</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

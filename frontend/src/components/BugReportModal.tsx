import { useState, useEffect, useCallback } from "react";
import type { CSSProperties, ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Bug, X, Copy, Check, ArrowRight, Loader2, Download } from "lucide-react";
import { SvPanel, SvLabel, SvNotice, sv } from "../app/components/synapse";
import { apiFetch, apiFetchBlob } from "../api/client";

interface BugReport {
  app_version: string;
  python_version: string;
  os: string;
  makemkv_version: string;
  ffmpeg_version: string;
  job: {
    id: number;
    volume_label: string;
    content_type: string;
    state: string;
    error: string | null;
    created_at: string | null;
    completed_at: string | null;
  } | null;
  recent_errors: string[];
  config: Record<string, string | number | boolean>;
  github_url: string;
  markdown: string;
  // Bundle-preview hints (present when a job_id was supplied).
  bundle_available?: boolean;
  has_scan_log?: boolean;
  coverage_seasons?: number;
  tmdb_cached?: boolean;
}

interface BugReportModalProps {
  open: boolean;
  onClose: () => void;
  /** When set, the report includes context for this specific job. */
  jobId?: number;
}

// GitHub silently truncates issues opened via a prefilled `?body=` URL beyond
// roughly 8 KB. Past this threshold we steer the user toward copy-and-paste.
const URL_LENGTH_LIMIT = 7500;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SvLabel size={10}>{title}</SvLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{children}</div>
    </div>
  );
}

function KvRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
      <span
        style={{
          flexShrink: 0,
          width: 120,
          fontFamily: sv.mono,
          fontSize: 11,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: sv.inkFaint,
        }}
      >
        {label}
      </span>
      <span
        style={{
          minWidth: 0,
          fontFamily: sv.mono,
          fontSize: 12,
          color: sv.cyanHi,
          wordBreak: "break-word",
        }}
      >
        {value}
      </span>
    </div>
  );
}

export default function BugReportModal({ open, onClose, jobId }: BugReportModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<BugReport | null>(null);
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!open) {
      // Reset so the next open re-fetches fresh diagnostics.
      setReport(null);
      setError(null);
      setCopied(false);
      setDownloading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const url =
      jobId != null
        ? `/api/diagnostics/report?job_id=${jobId}`
        : "/api/diagnostics/report";

    apiFetch<BugReport>(url)
      .then((data) => {
        if (!cancelled) setReport(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load report");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, jobId]);

  const handleCopy = useCallback(async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report.markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Could not copy to clipboard.");
    }
  }, [report]);

  const handleDownloadBundle = useCallback(async () => {
    if (jobId == null) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await apiFetchBlob(`/api/diagnostics/report/${jobId}/bundle`);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = `engram-bug-report-job-${jobId}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to download bundle.");
    } finally {
      setDownloading(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const tooLargeForUrl = report != null && report.github_url.length > URL_LENGTH_LIMIT;

  const buttonBase: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    padding: "10px 16px",
    fontFamily: sv.mono,
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.18em",
    textTransform: "uppercase",
    cursor: "pointer",
    transition: "all 0.18s",
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="bug-report-title"
        >
          <motion.div
            className="absolute inset-0"
            style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            onClick={onClose}
          />

          <motion.div
            className="relative w-full max-w-2xl"
            initial={{ opacity: 0, scale: 0.94, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.94, y: 20 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
          >
            <SvPanel
              glow
              pad={0}
              style={{
                background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                boxShadow: `0 0 40px ${sv.red}26, inset 0 0 30px ${sv.red}0a`,
                maxHeight: "85vh",
                display: "flex",
                flexDirection: "column",
              }}
              data-testid="bug-report-modal"
            >
              {/* Header */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "20px 24px",
                  borderBottom: `1px solid ${sv.line}`,
                }}
              >
                <Bug size={20} color={sv.red} style={{ filter: `drop-shadow(0 0 6px ${sv.red}99)` }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <h2
                    id="bug-report-title"
                    style={{
                      fontFamily: sv.display,
                      fontWeight: 700,
                      fontSize: 16,
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                      color: sv.cyanHi,
                      margin: 0,
                    }}
                  >
                    Diagnostic Report
                  </h2>
                  <p
                    style={{
                      margin: "4px 0 0",
                      fontFamily: sv.mono,
                      fontSize: 11,
                      letterSpacing: "0.06em",
                      color: sv.inkFaint,
                    }}
                  >
                    Exactly what will be attached to your bug report — review before sending.
                  </p>
                </div>
                <button
                  onClick={onClose}
                  aria-label="Close"
                  style={{
                    display: "inline-flex",
                    background: "transparent",
                    border: "none",
                    color: sv.inkDim,
                    cursor: "pointer",
                  }}
                >
                  <X size={18} />
                </button>
              </div>

              {/* Body (scrolls) */}
              <div
                style={{
                  padding: 24,
                  overflowY: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 20,
                }}
              >
                {loading && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      color: sv.inkDim,
                      fontFamily: sv.mono,
                      fontSize: 12,
                    }}
                  >
                    <Loader2 size={16} className="animate-spin" />
                    Collecting diagnostics…
                  </div>
                )}

                {error && <SvNotice tone="error">{error}</SvNotice>}

                {report && (
                  <>
                    <Section title="Environment">
                      <KvRow label="Engram" value={report.app_version} />
                      <KvRow label="OS" value={report.os} />
                      <KvRow label="Python" value={report.python_version} />
                    </Section>

                    <Section title="Tools">
                      <KvRow label="MakeMKV" value={report.makemkv_version} />
                      <KvRow label="FFmpeg" value={report.ffmpeg_version} />
                    </Section>

                    {report.job && (
                      <Section title="Job Context">
                        <KvRow label="ID" value={report.job.id} />
                        <KvRow label="Label" value={report.job.volume_label} />
                        <KvRow label="Type" value={report.job.content_type} />
                        <KvRow label="State" value={report.job.state} />
                        {report.job.error && <KvRow label="Error" value={report.job.error} />}
                      </Section>
                    )}

                    {report.bundle_available && (
                      <Section title="Bundle Contents">
                        <KvRow label="Job logs" value="Scoped to this job" />
                        <KvRow label="Disc & tracks" value="Full per-track detail" />
                        <KvRow
                          label="Cache"
                          value={`${report.coverage_seasons ?? 0} season(s) recorded · TMDB ${
                            report.tmdb_cached ? "cached" : "not cached"
                          }`}
                        />
                        <KvRow
                          label="Scan log"
                          value={report.has_scan_log ? "Included" : "Not present"}
                        />
                      </Section>
                    )}

                    <Section title="Config">
                      {Object.entries(report.config).map(([k, v]) => (
                        <KvRow key={k} label={k} value={String(v)} />
                      ))}
                    </Section>

                    <Section title="Recent Errors">
                      <pre
                        style={{
                          margin: 0,
                          maxHeight: 180,
                          overflow: "auto",
                          background: sv.bg0,
                          border: `1px solid ${sv.lineMid}`,
                          padding: 12,
                          fontFamily: sv.mono,
                          fontSize: 11,
                          lineHeight: 1.5,
                          color: sv.inkDim,
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                        }}
                      >
                        {report.recent_errors.length > 0
                          ? report.recent_errors.join("\n")
                          : "No recent errors logged."}
                      </pre>
                    </Section>

                    {tooLargeForUrl && (
                      <SvNotice tone="warn">
                        This report is large — the prefilled GitHub link may be truncated. Copy the
                        report and paste it into a new issue instead.
                      </SvNotice>
                    )}
                  </>
                )}
              </div>

              {/* Footer actions */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                  gap: 12,
                  padding: "16px 24px",
                  borderTop: `1px solid ${sv.line}`,
                }}
              >
                {jobId != null && (
                  <button
                    onClick={handleDownloadBundle}
                    disabled={!report || downloading}
                    data-testid="bug-report-download"
                    style={{
                      ...buttonBase,
                      marginRight: "auto",
                      color: sv.cyanHi,
                      border: `1px solid ${sv.cyan}`,
                      background: `${sv.cyan}1f`,
                      boxShadow: `0 0 14px ${sv.cyan}4d`,
                      opacity: report && !downloading ? 1 : 0.5,
                      cursor: report && !downloading ? "pointer" : "not-allowed",
                    }}
                  >
                    {downloading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Download size={14} />
                    )}
                    {downloading ? "Bundling…" : "Download Bundle"}
                  </button>
                )}

                <button
                  onClick={handleCopy}
                  disabled={!report}
                  style={{
                    ...buttonBase,
                    color: copied ? sv.green : sv.cyan,
                    border: `1px solid ${copied ? sv.green : sv.cyan}80`,
                    background: tooLargeForUrl ? `${sv.cyan}1f` : "transparent",
                    boxShadow: tooLargeForUrl ? `0 0 14px ${sv.cyan}4d` : "none",
                    opacity: report ? 1 : 0.4,
                    cursor: report ? "pointer" : "not-allowed",
                  }}
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? "Copied" : "Copy Report"}
                </button>

                <button
                  onClick={() => report && window.open(report.github_url, "_blank", "noopener")}
                  disabled={!report}
                  style={{
                    ...buttonBase,
                    color: sv.red,
                    border: `1px solid ${sv.red}80`,
                    background: tooLargeForUrl ? "transparent" : `${sv.red}1f`,
                    boxShadow: tooLargeForUrl ? "none" : `0 0 14px ${sv.red}4d`,
                    opacity: report ? 1 : 0.4,
                    cursor: report ? "pointer" : "not-allowed",
                  }}
                >
                  Open GitHub Issue
                  <ArrowRight size={14} />
                </button>
              </div>
            </SvPanel>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

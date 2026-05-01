import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import {
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
import { SvActionButton, SvAtmosphere, SvBadge, type SvBadgeState, SvBarChart, SvLabel, SvNotice, SvPageHeader, SvPanel, sv } from "../app/components/synapse";

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
  daily_throughput?: number[];
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
    >
      <SvPanel pad={14} accent={`${color}33`}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ color, filter: `drop-shadow(0 0 6px ${color}99)`, display: "inline-flex" }}>
            {icon}
          </span>
          <div>
            <div
              style={{
                fontFamily: sv.display,
                fontSize: 24,
                fontWeight: 700,
                color,
                letterSpacing: "0.04em",
                textShadow: `0 0 8px ${color}66`,
                lineHeight: 1,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {value}
            </div>
            <div
              style={{
                marginTop: 4,
                fontFamily: sv.mono,
                fontSize: 9,
                letterSpacing: "0.22em",
                textTransform: "uppercase",
                color: sv.inkFaint,
              }}
            >
              {label}
            </div>
          </div>
        </div>
      </SvPanel>
    </motion.div>
  );
}

/** Synapse-styled select dropdown. */
function SvSelect({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        background: sv.bg0,
        border: `1px solid ${sv.lineMid}`,
        color: sv.ink,
        fontFamily: sv.mono,
        fontSize: 11,
        letterSpacing: "0.06em",
        padding: "6px 10px",
        outline: "none",
        cursor: "pointer",
        transition: "border-color 120ms",
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; }}
      onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value} style={{ background: sv.bg1, color: sv.ink }}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/** Key/value row used inside Classification / Subtitles / Paths panels. */
function KvRow({
  label,
  value,
  valueColor,
  alignTop,
  truncate,
}: {
  label: string;
  value: string;
  valueColor?: string;
  alignTop?: boolean;
  truncate?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: alignTop ? "flex-start" : "center",
        justifyContent: "space-between",
        gap: 12,
        fontFamily: sv.mono,
        fontSize: 11,
      }}
    >
      <span style={{ color: sv.inkDim, flexShrink: 0 }}>{label}</span>
      <span
        style={{
          color: valueColor ?? sv.ink,
          textAlign: "right",
          minWidth: 0,
          maxWidth: alignTop ? "60%" : undefined,
          overflow: truncate ? "hidden" : undefined,
          textOverflow: truncate ? "ellipsis" : undefined,
          whiteSpace: truncate ? "nowrap" : undefined,
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** Single-line timeline entry: `Created  ›  2026-04-30 12:34:56` */
function TimelineRow({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: sv.mono, fontSize: 11 }}>
      <span style={{ color: sv.inkFaint, width: 88 }}>{label}</span>
      <ArrowRight size={11} color={`${sv.cyan}88`} />
      <span style={{ color: accent ?? sv.ink }}>{value}</span>
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? sv.green : pct >= 50 ? sv.amber : sv.red;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 3, background: sv.bg3, position: "relative" }}>
        <div
          style={{
            position: "absolute",
            inset: "0 auto 0 0",
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}, ${color}cc)`,
            boxShadow: `0 0 6px ${color}66`,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span
        className="sv-tnum"
        style={{ fontFamily: sv.mono, fontSize: 11, fontWeight: 700, color }}
      >
        {pct}%
      </span>
    </div>
  );
}

function TitleStateBadge({ state }: { state: string }) {
  const map: Record<string, { state: SvBadgeState; label: string }> = {
    completed: { state: "complete", label: "OK" },
    failed:    { state: "error",    label: "FAIL" },
    matched:   { state: "matched",  label: "MATCHED" },
    review:    { state: "review",   label: "REVIEW" },
    pending:   { state: "queued",   label: "PENDING" },
    ripping:   { state: "ripping",  label: "RIPPING" },
    matching:  { state: "matching", label: "MATCHING" },
  };
  const m = map[state] ?? { state: "idle" as SvBadgeState, label: state.toUpperCase() };
  return <SvBadge state={m.state} size="sm" dot={false}>{m.label}</SvBadge>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      title="Copy to clipboard"
      style={{
        display: "inline-flex",
        alignItems: "center",
        background: "transparent",
        border: 0,
        padding: 2,
        color: copied ? sv.green : sv.inkFaint,
        cursor: "pointer",
        transition: "color 120ms",
      }}
      onMouseEnter={(e) => { if (!copied) e.currentTarget.style.color = sv.cyan; }}
      onMouseLeave={(e) => { if (!copied) e.currentTarget.style.color = sv.inkFaint; }}
    >
      {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />}
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
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        height: "100vh",
        width: "min(560px, 100vw)",
        background: sv.bg1,
        borderLeft: `1px solid ${sv.lineMid}`,
        boxShadow: `-4px 0 30px ${sv.cyan}1a`,
        zIndex: 50,
        overflowY: "auto",
      }}
    >
      {/* Panel header — sticky, mirrors SvPageHeader vocabulary */}
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          background: "rgba(10, 14, 24, 0.92)",
          backdropFilter: "blur(8px)",
          borderBottom: `1px solid ${sv.lineMid}`,
          padding: "14px 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontFamily: sv.mono,
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: "0.20em",
            textTransform: "uppercase",
            color: sv.cyanHi,
          }}
        >
          › Job detail
        </h2>
        <button
          onClick={onClose}
          aria-label="Close detail panel"
          style={{
            width: 28,
            height: 28,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            background: "transparent",
            border: `1px solid ${sv.line}`,
            color: sv.inkDim,
            cursor: "pointer",
            transition: "border-color 120ms, color 120ms",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = sv.cyan;
            e.currentTarget.style.color = sv.cyanHi;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = sv.line;
            e.currentTarget.style.color = sv.inkDim;
          }}
        >
          <X size={14} />
        </button>
      </div>

      {loading ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 256 }}>
          <Loader2 size={22} color={sv.cyan} className="animate-spin" />
        </div>
      ) : detail ? (
        <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Title & Status */}
          <div>
            <h3
              style={{
                margin: 0,
                fontFamily: sv.display,
                fontSize: 18,
                fontWeight: 700,
                letterSpacing: "0.04em",
                color: sv.ink,
              }}
            >
              {detail.detected_title || detail.volume_label}
            </h3>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
              <SvBadge
                size="sm"
                tone={detail.content_type === "tv" ? sv.amber : detail.content_type === "movie" ? sv.magenta : sv.inkFaint}
              >
                {detail.content_type}
              </SvBadge>
              <SvBadge
                size="sm"
                state={detail.state === "completed" ? "complete" : "error"}
                dot={false}
              >
                {detail.state}
              </SvBadge>
              {detail.detected_season != null && (
                <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim }}>
                  Season {detail.detected_season}
                </span>
              )}
              {detail.disc_number > 1 && (
                <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim }}>
                  Disc {detail.disc_number}
                </span>
              )}
            </div>
            {detail.detected_title && (
              <div style={{ marginTop: 6, fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
                {detail.volume_label} on {detail.drive_id}
              </div>
            )}
          </div>

          {/* Error Details */}
          {detail.error_message && (
            <SvNotice tone="error" icon={<AlertTriangle size={14} />}>
              <div style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", textTransform: "uppercase", color: sv.red, marginBottom: 6 }}>
                Error
              </div>
              <pre
                style={{
                  margin: 0,
                  fontFamily: sv.mono,
                  fontSize: 11,
                  color: `${sv.red}cc`,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  maxHeight: 160,
                  overflowY: "auto",
                }}
              >
                {detail.error_message}
              </pre>
            </SvNotice>
          )}

          {/* Processing Timeline */}
          <div>
            <SvLabel>Timeline</SvLabel>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
              <TimelineRow label="Created" value={formatDateShort(detail.created_at)} />
              <TimelineRow
                label={detail.state === "completed" ? "Completed" : "Failed"}
                value={formatDateShort(detail.completed_at)}
              />
              {detail.created_at && detail.completed_at && (
                <TimelineRow
                  label="Duration"
                  value={formatDuration(
                    (new Date(detail.completed_at).getTime() -
                      new Date(detail.created_at).getTime()) /
                      1000
                  )}
                  accent={sv.cyan}
                />
              )}
            </div>
          </div>

          {/* Classification */}
          <div>
            <SvLabel>Classification</SvLabel>
            <div style={{ marginTop: 8 }}>
              <SvPanel pad={12}>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <KvRow label="Source" value={detail.classification_source} />
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>Confidence</span>
                    <ConfidenceBar value={detail.classification_confidence} />
                  </div>
                  {detail.tmdb_id && (
                    <KvRow label="TMDB" value={detail.tmdb_name || `ID ${detail.tmdb_id}`} />
                  )}
                  {detail.is_ambiguous_movie && (
                    <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.amber }}>
                      Ambiguous movie (multiple possible main features)
                    </span>
                  )}
                  {detail.review_reason && (
                    <KvRow label="Review reason" value={detail.review_reason} valueColor={sv.amber} alignTop />
                  )}
                </div>
              </SvPanel>
            </div>
          </div>

          {/* TheDiscDB */}
          {FEATURES.DISCDB && (
            <div>
              <SvLabel>
                <Database size={11} style={{ marginRight: 4 }} />
                TheDiscDB
              </SvLabel>
              <div style={{ marginTop: 8 }}>
                <SvPanel pad={12}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {detail.content_hash ? (
                      <>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontFamily: sv.mono, fontSize: 11 }}>
                          <span style={{ color: sv.inkDim }}>Content hash</span>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <code style={{ fontFamily: sv.mono, fontSize: 10, color: sv.cyanHi }}>
                              {detail.content_hash.slice(0, 16)}…
                            </code>
                            <CopyButton text={detail.content_hash} />
                          </div>
                        </div>
                        {detail.discdb_slug && <KvRow label="Title" value={detail.discdb_slug} />}
                        {detail.discdb_disc_slug && <KvRow label="Disc" value={detail.discdb_disc_slug} />}
                        {!detail.discdb_slug && (
                          <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.amber }}>
                            Disc fingerprint computed but not found in TheDiscDB
                          </span>
                        )}
                      </>
                    ) : (
                      <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
                        No disc fingerprint available (scan may have failed before computation)
                      </span>
                    )}
                  </div>
                </SvPanel>
              </div>
            </div>
          )}

          {/* Subtitle Info */}
          {detail.subtitle_status && (
            <div>
              <SvLabel>Subtitles</SvLabel>
              <div style={{ marginTop: 8 }}>
                <SvPanel pad={12}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: sv.mono, fontSize: 11 }}>
                    <KvRow label="Status" value={detail.subtitle_status} />
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: sv.inkDim }}>Downloaded</span>
                      <span style={{ color: sv.ink }}>
                        {detail.subtitles_downloaded}/{detail.subtitles_total}
                        {detail.subtitles_failed > 0 && (
                          <span style={{ color: sv.red, marginLeft: 4 }}>
                            ({detail.subtitles_failed} failed)
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                </SvPanel>
              </div>
            </div>
          )}

          {/* Track Breakdown */}
          <div>
            <SvLabel>
              <Disc3 size={11} style={{ marginRight: 4 }} />
              Tracks ({detail.titles.length})
            </SvLabel>
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              {detail.titles.length > 0 ? (
                detail.titles.map((t) => (
                  <div
                    key={t.id}
                    style={{
                      padding: "8px 12px",
                      background: sv.bg2,
                      border: `1px solid ${sv.line}`,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
                        <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>#{t.title_index}</span>
                        <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.ink }}>
                          {formatTitleDuration(t.duration_seconds)}
                        </span>
                        <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
                          {formatBytes(t.file_size_bytes)}
                        </span>
                        {t.video_resolution && (
                          <SvBadge size="sm" tone={sv.purple}>{t.video_resolution}</SvBadge>
                        )}
                      </div>
                      <TitleStateBadge state={t.state} />
                    </div>
                    {(t.matched_episode || t.edition || t.is_extra) && (
                      <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        {t.matched_episode && (
                          <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.cyan }}>
                            {t.matched_episode}
                          </span>
                        )}
                        {t.edition && (
                          <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.amber }}>
                            {t.edition}
                          </span>
                        )}
                        {t.is_extra && (
                          <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>extra</span>
                        )}
                        {t.match_confidence > 0 && (
                          <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
                            ({Math.round(t.match_confidence * 100)}% match)
                          </span>
                        )}
                      </div>
                    )}
                    {t.organized_to && (
                      <div
                        style={{
                          marginTop: 4,
                          fontFamily: sv.mono,
                          fontSize: 10,
                          color: sv.inkFaint,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {t.organized_to}
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <div
                  style={{
                    padding: "16px 12px",
                    textAlign: "center",
                    background: sv.bg2,
                    border: `1px solid ${sv.line}`,
                    fontFamily: sv.mono,
                    fontSize: 11,
                    color: sv.inkFaint,
                  }}
                >
                  No tracks found (scan may have failed before disc analysis)
                </div>
              )}
            </div>
          </div>

          {/* Paths */}
          {(detail.staging_path || detail.final_path) && (
            <div>
              <SvLabel>Paths</SvLabel>
              <div style={{ marginTop: 8 }}>
                <SvPanel pad={12}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: sv.mono, fontSize: 11 }}>
                    {detail.staging_path && <KvRow label="Staging" value={detail.staging_path} truncate />}
                    {detail.final_path && <KvRow label="Library" value={detail.final_path} truncate />}
                  </div>
                </SvPanel>
              </div>
            </div>
          )}

          {/* Bug Report for this specific job */}
          <div style={{ paddingTop: 8, borderTop: `1px solid ${sv.line}` }}>
            <a
              href={`/api/diagnostics/report?job_id=${detail.id}`}
              target="_blank"
              rel="noopener noreferrer"
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
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontFamily: sv.mono,
                fontSize: 11,
                letterSpacing: "0.06em",
                color: sv.inkDim,
                textDecoration: "none",
                transition: "color 120ms",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = sv.red; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = sv.inkDim; }}
            >
              <Bug size={12} />
              Report bug for this job
            </a>
          </div>
        </div>
      ) : null}
    </motion.div>
  );
}

/**
 * Right-column stats rail for the History page. Three stacked panels:
 * 1) 2x2 stat grid (Archived/Volume/Matched/Flagged)
 * 2) 14-day throughput sparkline
 * 3) Movies vs TV distribution bars
 */
function HistoryStatsRail({ stats }: { stats: Stats }) {
  const archived = stats.completed_jobs;
  const matched = stats.total_titles_ripped;
  const flagged = stats.failed_jobs;
  const throughput = stats.daily_throughput ?? [];
  const sumThroughput = throughput.reduce((a, b) => a + b, 0);

  const total = stats.movie_count + stats.tv_count;
  const movieRatio = total > 0 ? stats.movie_count / total : 0;
  const tvRatio = total > 0 ? stats.tv_count / total : 0;

  return (
    <aside
      data-testid="sv-history-stats-rail"
      style={{ display: "flex", flexDirection: "column", gap: 14, position: "sticky", top: 14 }}
    >
      {/* 2x2 stat grid — last 14 days */}
      <SvPanel pad={18} testid="sv-history-stats-grid">
        <SvLabel>Last · 14d</SvLabel>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 14,
            marginTop: 14,
          }}
        >
          <RailStat label="Archived" value={archived} sub="discs" accent={sv.cyan} />
          <RailStat label="Volume" value={sumThroughput} sub="14d total" accent={sv.green} />
          <RailStat label="Matched" value={matched} sub="titles" accent={sv.cyanHi} />
          <RailStat label="Flagged" value={flagged} sub="manual" accent={sv.yellow} />
        </div>
      </SvPanel>

      {/* 14-day throughput sparkline */}
      <SvPanel pad={18} testid="sv-history-stats-throughput">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <SvLabel>Throughput · 14d</SvLabel>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 9,
              letterSpacing: "0.22em",
              color: sv.inkFaint,
              textTransform: "uppercase",
            }}
          >
            jobs/day
          </span>
        </div>
        <div style={{ marginTop: 14 }}>
          <SvBarChart values={throughput} accent="cyan" height={70} />
        </div>
      </SvPanel>

      {/* Distribution */}
      <SvPanel pad={18} testid="sv-history-stats-distribution">
        <SvLabel>Distribution</SvLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 14 }}>
          <DistRow label="Movies" count={stats.movie_count} ratio={movieRatio} accent={sv.cyan} />
          <DistRow label="TV seasons" count={stats.tv_count} ratio={tvRatio} accent={sv.magenta} />
        </div>
      </SvPanel>
    </aside>
  );
}

function RailStat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number;
  sub?: string;
  accent: string;
}) {
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
          fontSize: 28,
          fontWeight: 700,
          color: accent,
          letterSpacing: "0.04em",
          fontVariantNumeric: "tabular-nums",
          lineHeight: 1,
        }}
      >
        {value}
      </span>
      {sub && (
        <span
          style={{
            fontFamily: sv.mono,
            fontSize: 9,
            letterSpacing: "0.16em",
            color: sv.inkFaint,
            textTransform: "uppercase",
          }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

function DistRow({
  label,
  count,
  ratio,
  accent,
}: {
  label: string;
  count: number;
  ratio: number;
  accent: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: sv.mono,
          fontSize: 10,
          letterSpacing: "0.14em",
          color: sv.inkDim,
          textTransform: "uppercase",
        }}
      >
        <span>{label}</span>
        <span style={{ color: accent }}>{count}</span>
      </div>
      <div
        style={{
          height: 4,
          background: sv.inkGhost,
          position: "relative",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: "0 auto 0 0",
            width: `${Math.min(100, Math.max(0, ratio * 100))}%`,
            background: `linear-gradient(90deg, ${accent}, ${accent}99)`,
            boxShadow: `0 0 8px ${accent}66`,
            transition: "width 0.3s ease",
          }}
        />
      </div>
    </div>
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
    <SvAtmosphere>
      <SvPageHeader
        title="Job History & Analytics"
        icon={<BarChart3 size={20} />}
        onBack={() => navigate("/")}
        right={
          <button
            onClick={handleReportBug}
            disabled={reportLoading}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              height: 32,
              padding: "0 12px",
              background: sv.bg0,
              border: `1px solid ${sv.red}55`,
              color: sv.red,
              fontFamily: sv.mono,
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.20em",
              textTransform: "uppercase",
              cursor: reportLoading ? "wait" : "pointer",
              opacity: reportLoading ? 0.5 : 1,
              boxShadow: `0 0 8px ${sv.red}33`,
              transition: "border-color 120ms, color 120ms, box-shadow 120ms",
            }}
            onMouseEnter={(e) => {
              if (reportLoading) return;
              e.currentTarget.style.borderColor = sv.red;
              e.currentTarget.style.boxShadow = `0 0 14px ${sv.red}66`;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = `${sv.red}55`;
              e.currentTarget.style.boxShadow = `0 0 8px ${sv.red}33`;
            }}
          >
            <Bug size={14} />
            <span>{reportLoading ? "Generating…" : "Report Bug"}</span>
          </button>
        }
      />

      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 space-y-8">
        <div
          data-testid="sv-history-grid"
          style={{
            display: "grid",
            gridTemplateColumns: stats ? "minmax(0, 1fr) 320px" : "1fr",
            gap: 14,
            alignItems: "start",
          }}
        >
        <div style={{ minWidth: 0 }} className="space-y-8">
        {/* Stats Grid */}
        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Total Jobs"  value={stats.total_jobs}      icon={<BarChart3 size={18} />}    color={sv.cyan} />
            <StatCard label="Completed"   value={stats.completed_jobs}  icon={<CheckCircle2 size={18} />} color={sv.green} />
            <StatCard label="Failed"      value={stats.failed_jobs}     icon={<XCircle size={18} />}      color={sv.red} />
            <StatCard label="TV Shows"    value={stats.tv_count}        icon={<Tv size={18} />}           color={sv.amber} />
            <StatCard label="Movies"      value={stats.movie_count}     icon={<Film size={18} />}         color={sv.magenta} />
            <StatCard
              label="Avg Time"
              value={stats.avg_processing_seconds ? formatDuration(stats.avg_processing_seconds) : "\u2014"}
              icon={<Clock size={18} />}
              color={sv.purple}
            />
          </div>
        )}

        {/* Common Errors */}
        {stats && stats.common_errors.length > 0 && (
          <SvNotice tone="error">
            <div style={{ fontFamily: sv.mono, fontSize: 11, fontWeight: 700, letterSpacing: "0.18em", textTransform: "uppercase", color: sv.red, marginBottom: 8 }}>
              Common errors
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {stats.common_errors.map((err, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 12, fontFamily: sv.mono, fontSize: 11 }}>
                  <span style={{ color: sv.red, fontWeight: 700, minWidth: 32, textAlign: "right" }}>
                    ×{err.count}
                  </span>
                  <span style={{ color: sv.inkDim, wordBreak: "break-all" }}>{err.message}</span>
                </div>
              ))}
            </div>
          </SvNotice>
        )}

        {/* Filters */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <SvLabel>Filter</SvLabel>
          <SvSelect
            value={filterType}
            onChange={(v) => { setFilterType(v); setPage(1); }}
            options={[
              { value: "", label: "All types" },
              { value: "tv", label: "TV" },
              { value: "movie", label: "Movie" },
            ]}
          />
          <SvSelect
            value={filterState}
            onChange={(v) => { setFilterState(v); setPage(1); }}
            options={[
              { value: "", label: "All states" },
              { value: "completed", label: "Completed" },
              { value: "failed", label: "Failed" },
            ]}
          />
        </div>

        {/* History Table */}
        <div style={{ border: `1px solid ${sv.lineMid}`, background: sv.bg1, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: sv.mono, fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${sv.lineMid}` }}>
                {(["Title", "Type", "State", "Titles", "Source", "Date"] as const).map((h, i) => (
                  <th
                    key={h}
                    className={
                      i === 1 ? "hidden sm:table-cell" :
                      i === 3 ? "hidden md:table-cell" :
                      i === 4 ? "hidden lg:table-cell" :
                      i === 5 ? "hidden sm:table-cell" : ""
                    }
                    style={{
                      textAlign: "left",
                      padding: "12px 16px",
                      color: sv.cyan,
                      fontFamily: sv.mono,
                      fontSize: 10,
                      fontWeight: 700,
                      letterSpacing: "0.20em",
                      textTransform: "uppercase",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    style={{
                      padding: "32px 16px",
                      textAlign: "center",
                      color: sv.inkFaint,
                      fontFamily: sv.mono,
                      fontSize: 12,
                    }}
                  >
                    No completed or failed jobs yet
                  </td>
                </tr>
              ) : (
                history.map((job) => {
                  const isSelected = selectedJobId === job.id;
                  const typeColor = job.content_type === "tv" ? sv.amber : job.content_type === "movie" ? sv.magenta : sv.inkFaint;
                  return (
                    <tr
                      key={job.id}
                      onClick={() => handleRowClick(job.id)}
                      style={{
                        borderBottom: `1px solid ${sv.line}`,
                        background: isSelected ? `${sv.cyan}10` : "transparent",
                        cursor: "pointer",
                        transition: "background 120ms",
                      }}
                      onMouseEnter={(e) => {
                        if (!isSelected) e.currentTarget.style.background = `${sv.cyan}06`;
                      }}
                      onMouseLeave={(e) => {
                        if (!isSelected) e.currentTarget.style.background = "transparent";
                      }}
                    >
                      <td style={{ padding: "12px 16px" }}>
                        <div style={{ color: sv.ink }}>{job.detected_title || job.volume_label}</div>
                        {job.detected_title && (
                          <div style={{ fontSize: 10, color: sv.inkFaint, marginTop: 2 }}>{job.volume_label}</div>
                        )}
                      </td>
                      <td className="hidden sm:table-cell" style={{ padding: "12px 16px" }}>
                        <span
                          style={{
                            color: typeColor,
                            textTransform: "uppercase",
                            fontWeight: 700,
                            letterSpacing: "0.18em",
                            fontSize: 11,
                          }}
                        >
                          {job.content_type}
                        </span>
                      </td>
                      <td style={{ padding: "12px 16px" }}>
                        {job.state === "completed" ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: sv.green }}>
                            <CheckCircle2 size={12} /> OK
                          </span>
                        ) : (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: sv.red }}>
                            <XCircle size={12} /> FAIL
                          </span>
                        )}
                      </td>
                      <td className="hidden md:table-cell" style={{ padding: "12px 16px", color: sv.inkDim }}>
                        {job.total_titles}
                      </td>
                      <td className="hidden lg:table-cell" style={{ padding: "12px 16px", color: sv.inkFaint }}>
                        {job.classification_source}
                      </td>
                      <td className="hidden sm:table-cell" style={{ padding: "12px 16px", color: sv.inkFaint }}>
                        {formatDate(job.completed_at || job.created_at)}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <SvActionButton tone="neutral" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
            <ChevronLeft size={12} /> Prev
          </SvActionButton>
          <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint, letterSpacing: "0.06em" }}>
            Page {page}
          </span>
          <SvActionButton tone="neutral" onClick={() => setPage((p) => p + 1)} disabled={!hasMore}>
            Next <ChevronRight size={12} />
          </SvActionButton>
        </div>
        </div>
        {stats && <HistoryStatsRail stats={stats} />}
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
    </SvAtmosphere>
  );
}

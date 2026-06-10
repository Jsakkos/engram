import { useState, useEffect, useRef } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { AlertTriangle, Trash2, LayoutGrid, List, Info, X } from "lucide-react";
import { DiscCard, type DiscData } from "./components/DiscCard";
import { CompactList } from "./components/CompactList";
import { useJobManagement } from "./hooks/useJobManagement";
import { useDiscFilters } from "./hooks/useDiscFilters";
import { useNotifications } from "./hooks/useNotifications";
import { useUpdateSuccessToast } from "./hooks/useUpdateSuccessToast";
import ReviewQueue from "../components/ReviewQueue";
import ConfigWizard from "../components/ConfigWizard";
import NamePromptModal from "../components/NamePromptModal";
import SeasonPromptModal from "../components/SeasonPromptModal";
import ReIdentifyModal from "../components/ReIdentifyModal";
import BugReportModal from "../components/BugReportModal";
import UpdateModal from "../components/UpdateModal";
import { FingerprintDisclosureModal } from "../components/FingerprintDisclosureModal";
import HistoryPage from "../components/HistoryPage";
import ContributePage from "../components/ContributePage";
import { FEATURES } from "../config/constants";
import { ROUTES, reviewPath } from "../config/routes";
import { buildNavItems } from "./navigation";
import { selectPromptJobs } from "./promptSelection";
import type { Job } from "../types";
import { toast } from "sonner";
import { UpdateBanner } from "./components/UpdateBanner";
import { AsrStatusBadge } from "./components/AsrStatusBadge";
import {
  Splash,
  SvAtmosphere,
  SvTopBar,
  SvStatusBar,
  sv,
} from "./components/synapse";
import { DashboardSideRail } from "./components/DashboardSideRail";

type ViewMode = "expanded" | "compact";

/**
 * Shared sv-token button base â€” mono uppercase typography with pointer cursor.
 * Spread first so call-site overrides (padding, colors, fontSize) win.
 */
const svButtonBase: React.CSSProperties = {
  fontFamily: sv.mono,
  letterSpacing: "0.20em",
  textTransform: "uppercase",
  cursor: "pointer",
};

/** Empty-state copy keyed by the active dashboard filter. */
const emptyHeading: Record<"all" | "active" | "completed", string> = {
  active: "â€º No active operations",
  completed: "â€º No completed archives",
  all: "â€º No discs detected",
};

const emptyBody: Record<"all" | "active" | "completed", string> = {
  active: "All operations complete. Insert a disc to start a new job.",
  completed: "No archived media yet. Completed jobs will appear here.",
  all: "Insert a disc into your optical drive to begin archiving.",
};

function MainDashboard() {
  const navigate = useNavigate();
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [namePromptJob, setNamePromptJob] = useState<Job | null>(null);
  const [seasonPromptJob, setSeasonPromptJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("expanded");
  const [platform, setPlatform] = useState<string | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [tmdbConfigured, setTmdbConfigured] = useState(true);
  const [tmdbBannerDismissed, setTmdbBannerDismissed] = useState(false);
  const [contributionPending, setContributionPending] = useState(0);

  // Check for development mock mode
  const DEV_MODE = window.location.search.includes('mock=true');

  // Check if first-run setup is needed + fetch contribution badge count
  const checkSetup = async () => {
    try {
      const response = await fetch('/api/config');
      if (!response.ok) return;
      const data = await response.json();
      if (!data.setup_complete) {
        setShowOnboarding(true);
      }
      // Drive the health banner from the explicit backend boolean, not the
      // redacted ("***"/"") key value, so it can't be fooled by a change to the
      // redaction sentinel (#243).
      setTmdbConfigured(data.tmdb_configured ?? !!data.tmdb_api_key);
      // Fetch contribution stats for nav badge
      if (FEATURES.DISCDB && data.discdb_contributions_enabled) {
        try {
          const statsRes = await fetch('/api/contributions/stats');
          if (statsRes.ok) {
            const stats = await statsRes.json();
            setContributionPending(stats.pending);
          }
        } catch {
          // Non-critical
        }
      }
    } catch {
      // Backend not reachable â€” don't block the UI
    }
  };
  useEffect(() => { checkSetup(); }, []);

  // Detect platform for non-Windows guidance banner
  useEffect(() => {
    const detectPlatform = async () => {
      try {
        const response = await fetch('/api/detect-tools');
        if (!response.ok) return;
        const data = await response.json();
        if (data.platform) {
          setPlatform(data.platform);
        }
      } catch {
        // Backend not reachable â€” don't show banner
      }
    };
    detectPlatform();
  }, []);

  // Job management with WebSocket
  const { jobs, titlesMap, isConnected, updateStatus, cancelJob, advanceJob, clearCompleted, setJobName, reIdentifyJob, disclosure, clearDisclosure } = useJobManagement(DEV_MODE);
  useUpdateSuccessToast(updateStatus);
  const [reIdentifyTarget, setReIdentifyTarget] = useState<Job | null>(null);
  const [bugReportJobId, setBugReportJobId] = useState<number | null>(null);
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [updateDismissed, setUpdateDismissed] = useState(false);

  // Show the full-screen Splash with a "RECONNECTINGâ€¦" label when the
  // WebSocket has been down for >2.5s. The grace period absorbs momentary
  // reconnect blips â€” without it, every brief WS hiccup would flash the
  // splash. Backend-truly-down stays surfaced via the top-bar pill until
  // the grace fires, then the splash takes over.
  const [showOfflineSplash, setShowOfflineSplash] = useState(false);
  useEffect(() => {
    if (isConnected) {
      setShowOfflineSplash(false);
      return;
    }
    const t = window.setTimeout(() => setShowOfflineSplash(true), 2500);
    return () => window.clearTimeout(t);
  }, [isConnected]);

  // Disc filtering and transformation
  const { filter, setFilter, discsData, filteredDiscs, activeCount, completedCount } = useDiscFilters(jobs, titlesMap, DEV_MODE);

  // Browser notifications for job state changes
  useNotifications(jobs);

  // Show name prompt modal for unreadable labels or TV shows where TMDB lookup failed,
  // and the season prompt (#370) when the show is known but the season isn't.
  // Dismissed prompts (Escape / backdrop click) are remembered so the next jobs
  // refresh doesn't immediately re-open them â€” dismissal parks the job in review,
  // it does NOT cancel it.
  const dismissedPromptIdsRef = useRef<Set<number>>(new Set());
  useEffect(() => {
    const { namePromptJob: needsName, seasonPromptJob: needsSeason } = selectPromptJobs(
      jobs,
      dismissedPromptIdsRef.current,
    );
    setNamePromptJob(needsName);
    setSeasonPromptJob(needsSeason);
  }, [jobs]);

  const reviewJobs = jobs.filter((j) => j.state === 'review_needed');
  const navItems = buildNavItems({
    firstReviewJobId: reviewJobs[0]?.id,
    reviewCount: reviewJobs.length,
    contributionPending,
  });

  return (
    <SvAtmosphere ripActive={discsData.some((d) => d.state === "ripping")}>
      {/* Full-screen overlay when WS has been down past the grace period.
          Stays on top of all chrome (z-index 100 inside Splash). */}
      {showOfflineSplash && (
        <Splash
          label="RECONNECTING"
          captionRight={`v${__APP_VERSION__}`}
          atmosphere={false}
        />
      )}
      <SvTopBar
        isConnected={isConnected}
        version={__APP_VERSION__}
        devMode={DEV_MODE}
        navItems={navItems}
        onSettingsClick={() => setShowSettings(true)}
      />

      {/* Filter + view-mode strip */}
      <div
        style={{
          padding: "10px 28px",
          borderBottom: `1px solid ${sv.line}`,
          background: "rgba(10,14,24,0.45)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
        }}
        data-testid="sv-filter-strip"
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {(() => {
            const counts = { all: discsData.length, active: activeCount, completed: completedCount };
            const labels = { all: "ALL", active: "ACTIVE", completed: "DONE" };
            return (["all", "active", "completed"] as const).map((f) => {
              const active = filter === f;
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  data-testid={`sv-filter-${f}`}
                  data-active={active ? "true" : "false"}
                  style={{
                    ...svButtonBase,
                    padding: "6px 14px",
                    fontSize: 10,
                    fontWeight: 600,
                    color: active ? sv.cyanHi : sv.inkDim,
                    background: active ? "rgba(94,234,212,0.10)" : "transparent",
                    border: `1px solid ${active ? sv.lineHi : sv.line}`,
                    transition: "all 0.18s",
                  }}
                >
                  {labels[f]} [{counts[f]}]
                </button>
              );
            });
          })()}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <AsrStatusBadge onOpenSettings={() => setShowSettings(true)} />
          {/* View mode toggle */}
          <div style={{ display: "inline-flex", border: `1px solid ${sv.line}` }}>
            <button
              onClick={() => setViewMode("expanded")}
              title="Expanded view"
              data-testid="sv-view-expanded"
              style={{
                padding: 6,
                background: viewMode === "expanded" ? "rgba(94,234,212,0.10)" : "transparent",
                color: viewMode === "expanded" ? sv.cyanHi : sv.inkFaint,
                border: "none",
                cursor: "pointer",
                display: "flex",
              }}
            >
              <LayoutGrid size={16} />
            </button>
            <button
              onClick={() => setViewMode("compact")}
              title="Compact view"
              data-testid="sv-view-compact"
              style={{
                padding: 6,
                background: viewMode === "compact" ? "rgba(94,234,212,0.10)" : "transparent",
                color: viewMode === "compact" ? sv.cyanHi : sv.inkFaint,
                border: "none",
                cursor: "pointer",
                display: "flex",
              }}
            >
              <List size={16} />
            </button>
          </div>

          {completedCount > 0 && (
            <button
              onClick={clearCompleted}
              data-testid="sv-clear-btn"
              title="Clear Completed"
              style={{
                ...svButtonBase,
                padding: "6px 12px",
                fontSize: 10,
                fontWeight: 600,
                color: sv.red,
                background: "transparent",
                border: `1px solid ${sv.red}55`,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Trash2 size={12} />
              <span>CLEAR</span>
            </button>
          )}
        </div>
      </div>

      {/* Auto-update banner */}
      {!updateDismissed && (
        <UpdateBanner
          updateStatus={updateStatus}
          onShowNotes={() => setShowUpdateModal(true)}
          onDismiss={() => setUpdateDismissed(true)}
        />
      )}

      {/* Platform guidance banner for Linux/macOS users */}
      <AnimatePresence>
        {platform && platform !== "win32" && jobs.length === 0 && !bannerDismissed && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="max-w-[1600px] mx-auto px-4 sm:px-6 mt-4"
          >
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "12px 16px",
                background: `${sv.cyan}10`,
                border: `1px solid ${sv.cyan}55`,
                boxShadow: `0 0 12px ${sv.cyan}22`,
              }}
            >
              <Info size={18} color={sv.cyan} style={{ flexShrink: 0, marginTop: 1 }} />
              <div
                style={{
                  flex: 1,
                  fontFamily: sv.mono,
                  fontSize: 12,
                  letterSpacing: "0.06em",
                  color: sv.cyanHi,
                  lineHeight: 1.45,
                }}
              >
                <span>No optical drives detected. Drop MKV folders into your staging directory or </span>
                <button
                  onClick={() => setShowSettings(true)}
                  style={{
                    fontFamily: "inherit",
                    fontSize: "inherit",
                    color: sv.cyan,
                    textDecoration: "underline",
                    textUnderlineOffset: 2,
                    background: "none",
                    border: 0,
                    padding: 0,
                    cursor: "pointer",
                  }}
                >
                  configure staging import
                </button>
                <span>.</span>
              </div>
              <button
                onClick={() => setBannerDismissed(true)}
                title="Dismiss"
                aria-label="Dismiss banner"
                style={{
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: "transparent",
                  border: 0,
                  color: `${sv.cyan}99`,
                  cursor: "pointer",
                  transition: "color 120ms",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = sv.cyanHi; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = `${sv.cyan}99`; }}
              >
                <X size={14} />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* TMDB health banner */}
      <AnimatePresence>
        {!tmdbConfigured && !tmdbBannerDismissed && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="max-w-[1600px] mx-auto px-4 sm:px-6 mt-4"
          >
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "12px 16px",
                background: `${sv.amber}10`,
                border: `1px solid ${sv.amber}55`,
                boxShadow: `0 0 12px ${sv.amber}22`,
              }}
            >
              <AlertTriangle size={18} color={sv.amber} style={{ flexShrink: 0, marginTop: 1 }} />
              <div
                style={{
                  flex: 1,
                  fontFamily: sv.mono,
                  fontSize: 12,
                  letterSpacing: "0.06em",
                  color: sv.amber,
                  lineHeight: 1.45,
                }}
              >
                <span>TMDB not configured â€” classification is running in heuristic-only mode. </span>
                <button
                  onClick={() => setShowSettings(true)}
                  style={{
                    fontFamily: "inherit",
                    fontSize: "inherit",
                    color: sv.amber,
                    textDecoration: "underline",
                    textUnderlineOffset: 2,
                    background: "none",
                    border: 0,
                    padding: 0,
                    cursor: "pointer",
                  }}
                >
                  Configure token
                </button>
              </div>
              <button
                onClick={() => setTmdbBannerDismissed(true)}
                title="Dismiss"
                aria-label="Dismiss TMDB warning"
                style={{
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: "transparent",
                  border: 0,
                  color: `${sv.amber}99`,
                  cursor: "pointer",
                  transition: "color 120ms",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = sv.amber; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = `${sv.amber}99`; }}
              >
                <X size={14} />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Content */}
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-24 sm:pb-28 relative z-0">
        <div
          data-testid="sv-dashboard-grid"
          style={{
            display: "grid",
            gridTemplateColumns:
              filteredDiscs.length > 0 && viewMode === "expanded"
                ? "minmax(0, 1.4fr) 320px"
                : "1fr",
            gap: 14,
            // `stretch` lets the right rail's grid cell match the disc-card
            // column's height. The Activity log panel already has `flex: 1`,
            // so it consumes the slack and bottom-aligns with the card.
            alignItems: "stretch",
          }}
        >
        <div style={{ minWidth: 0 }}>
        {filteredDiscs.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "80px 0",
              textAlign: "center",
            }}
            data-testid="sv-empty-state"
          >
            <motion.div
              animate={{
                filter: [
                  `drop-shadow(0 0 12px ${sv.cyan}4d)`,
                  `drop-shadow(0 0 24px ${sv.cyan}80)`,
                  `drop-shadow(0 0 12px ${sv.cyan}4d)`,
                ],
              }}
              transition={{ duration: 3, repeat: Infinity }}
              style={{ marginBottom: 24 }}
            >
              {/* Synapse beacon â€” concentric rings + rotating sweep + chapter ticks. Same
                  visual language as SvDiscInsert but simplified for "no signal yet" semantics. */}
              <svg
                width={140}
                height={140}
                viewBox="0 0 200 200"
                aria-label="Engram beacon â€” awaiting input"
              >
                <defs>
                  <radialGradient id="sv-empty-bg" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor={sv.cyan} stopOpacity="0.18" />
                    <stop offset="60%" stopColor={sv.cyan} stopOpacity="0.04" />
                    <stop offset="100%" stopColor={sv.cyan} stopOpacity="0" />
                  </radialGradient>
                  <linearGradient id="sv-empty-sweep" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={sv.cyan} stopOpacity="0" />
                    <stop offset="100%" stopColor={sv.cyan} stopOpacity="0.55" />
                  </linearGradient>
                </defs>
                <circle cx="100" cy="100" r="92" fill="url(#sv-empty-bg)" />
                {[88, 72, 56, 40, 22].map((r, i) => (
                  <circle
                    key={r}
                    cx="100"
                    cy="100"
                    r={r}
                    fill="none"
                    stroke={sv.cyan}
                    strokeWidth="0.6"
                    opacity={0.18 + i * 0.06}
                  />
                ))}
                <line x1="100" y1="6" x2="100" y2="194" stroke={sv.cyan} strokeWidth="0.4" opacity="0.22" />
                <line x1="6" y1="100" x2="194" y2="100" stroke={sv.cyan} strokeWidth="0.4" opacity="0.22" />
                <g style={{ transformOrigin: "100px 100px", animation: "svSpin 4s linear infinite" }}>
                  <path
                    d="M 100 100 L 188 100 A 88 88 0 0 0 100 12 Z"
                    fill="url(#sv-empty-sweep)"
                    opacity="0.55"
                  />
                </g>
                {Array.from({ length: 24 }, (_, i) => {
                  const ang = (i / 24) * Math.PI * 2;
                  return (
                    <line
                      key={i}
                      x1={100 + Math.cos(ang) * 92}
                      y1={100 + Math.sin(ang) * 92}
                      x2={100 + Math.cos(ang) * 84}
                      y2={100 + Math.sin(ang) * 84}
                      stroke={sv.inkGhost}
                      strokeWidth="1"
                    />
                  );
                })}
                <circle cx="100" cy="100" r="4" fill={sv.cyan} />
                <circle cx="100" cy="100" r="1.5" fill={sv.bg0} />
              </svg>
            </motion.div>
            <h2
              data-testid="sv-empty-heading"
              style={{
                fontFamily: sv.display,
                fontWeight: 700,
                fontSize: 22,
                letterSpacing: "0.2em",
                textTransform: "uppercase",
                color: sv.cyanHi,
                textShadow: `0 0 12px ${sv.cyan}99`,
                marginBottom: 10,
              }}
            >
              {emptyHeading[filter]}
            </h2>
            <p
              style={{
                fontFamily: sv.mono,
                fontSize: 11,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: sv.inkFaint,
                maxWidth: 480,
                lineHeight: 1.6,
              }}
            >
              {emptyBody[filter]}
            </p>
          </motion.div>
        ) : viewMode === "compact" ? (
          /* Compact view â€” sv-token row layout */
          <CompactList
            discs={filteredDiscs}
            onReview={(id) => navigate(reviewPath(id))}
            onCancel={(id) => cancelJob(id)}
            onReIdentify={(id) => {
              const job = jobs.find((j) => String(j.id) === id);
              if (job) setReIdentifyTarget(job);
            }}
          />
        ) : (
          /* Expanded view */
          <div className="space-y-6">
            <AnimatePresence mode="popLayout">
              {filteredDiscs.map((disc: DiscData) => (
                <DiscCard
                  key={disc.id}
                  disc={disc}
                  onCancel={disc.state !== 'completed' && disc.state !== 'error' ? () => cancelJob(disc.id) : undefined}
                  onAdvance={disc.state !== 'completed' && disc.state !== 'error' ? () => advanceJob(disc.id) : undefined}
                  onReview={disc.needsReview && !disc.identityReview && (disc.tracks?.length ?? 0) > 0 ? () => navigate(reviewPath(disc.id)) : undefined}
                  onReIdentify={disc.needsReview && disc.title ? () => {
                    const job = jobs.find(j => String(j.id) === disc.id);
                    if (job) setReIdentifyTarget(job);
                  } : undefined}
                  onReportBug={() => setBugReportJobId(Number(disc.id))}
                  onOpenSettings={() => setShowSettings(true)}
                />
              ))}
            </AnimatePresence>
          </div>
        )}
        </div>
        {filteredDiscs.length > 0 && viewMode === "expanded" && (
          <DashboardSideRail jobs={jobs} titlesMap={titlesMap} />
        )}
        </div>
      </div>

      {/* Name Prompt Modal â€” appears when disc label is unreadable */}
      <AnimatePresence>
        {namePromptJob && (
          <NamePromptModal
            job={namePromptJob}
            initialTitle={namePromptJob.detected_title ?? ''}
            onSubmit={(name, contentType, season) => {
              setJobName(namePromptJob.id, name, contentType, season);
              setNamePromptJob(null);
            }}
            onDismiss={() => {
              dismissedPromptIdsRef.current.add(namePromptJob.id);
              setNamePromptJob(null);
            }}
            onCancelJob={() => {
              cancelJob(String(namePromptJob.id));
              setNamePromptJob(null);
            }}
          />
        )}
      </AnimatePresence>

      {/* Season Prompt Modal â€” show identified but the disc label has no season (#370) */}
      <AnimatePresence>
        {seasonPromptJob && !namePromptJob && (
          <SeasonPromptModal
            job={seasonPromptJob}
            onSubmit={(season) => {
              setJobName(
                seasonPromptJob.id,
                seasonPromptJob.detected_title ?? seasonPromptJob.volume_label,
                'tv',
                season,
              );
              setSeasonPromptJob(null);
            }}
            onDismiss={() => {
              dismissedPromptIdsRef.current.add(seasonPromptJob.id);
              setSeasonPromptJob(null);
            }}
            onCancelJob={() => {
              cancelJob(String(seasonPromptJob.id));
              setSeasonPromptJob(null);
            }}
          />
        )}
      </AnimatePresence>

      {/* Re-Identify Modal â€” appears when user clicks "Wrong title?" */}
      <AnimatePresence>
        {reIdentifyTarget && (
          <ReIdentifyModal
            job={reIdentifyTarget}
            onSubmit={(title, contentType, season, tmdbId) => {
              reIdentifyJob(reIdentifyTarget.id, title, contentType, season, tmdbId);
              setReIdentifyTarget(null);
            }}
            onCancel={() => setReIdentifyTarget(null)}
          />
        )}
      </AnimatePresence>

      {/* Fingerprint Disclosure Modal â€” JIT consent before any contribution upload */}
      <AnimatePresence>
        {disclosure && (
          <FingerprintDisclosureModal
            pendingCount={disclosure.pending_count}
            pseudonym={disclosure.pseudonym}
            serverUrl={disclosure.server_url}
            onAccept={async () => {
              // Only dismiss once the choice is actually persisted â€” fetch does
              // not throw on non-2xx, so a swallowed failure here would silently
              // start (or fail to authorize) uploads.
              const resp = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fingerprint_disclosure_accepted: true }),
              });
              if (!resp.ok) {
                toast.error('Could not save your choice â€” please try again.');
                return;
              }
              clearDisclosure();
            }}
            onDecline={async () => {
              const resp = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enable_fingerprint_contributions: false }),
              });
              if (!resp.ok) {
                toast.error('Could not save your choice â€” please try again.');
                return;
              }
              clearDisclosure();
            }}
          />
        )}
      </AnimatePresence>

      {/* Bug Report Modal â€” appears when user reports a bug for an active job */}
      <BugReportModal
        open={bugReportJobId != null}
        jobId={bugReportJobId ?? undefined}
        onClose={() => setBugReportJobId(null)}
      />

      {/* Update Modal â€” release notes opened from UpdateBanner */}
      <UpdateModal
        open={showUpdateModal}
        updateStatus={updateStatus}
        onClose={() => setShowUpdateModal(false)}
        onDismiss={() => {
          setUpdateDismissed(true);
          setShowUpdateModal(false);
        }}
      />

      {/* Onboarding Wizard (first run) */}
      {showOnboarding && (
        <ModalScrim>
          <ConfigWizard
            onClose={() => setShowOnboarding(false)}
            onComplete={() => { setShowOnboarding(false); checkSetup(); }}
            isOnboarding={true}
          />
        </ModalScrim>
      )}

      {/* Config Wizard Modal (settings) */}
      {showSettings && !showOnboarding && (
        <ModalScrim>
          <ConfigWizard
            onClose={() => setShowSettings(false)}
            onComplete={() => {
              setShowSettings(false);
              checkSetup();
            }}
            isOnboarding={false}
          />
        </ModalScrim>
      )}

      <SvStatusBar
        activeCount={activeCount}
        completedCount={completedCount}
        isConnected={isConnected}
        version={__APP_VERSION__}
        driveLabel={platform === "win32" ? "DRIVE READY" : "STAGING IMPORT"}
      />
    </SvAtmosphere>
  );
}

/** Modal backdrop with sv-token blur + sv.bg0 alpha overlay. */
function ModalScrim({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        background: "rgba(5, 7, 12, 0.78)",
        backdropFilter: "blur(8px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div style={{ width: "100%", maxWidth: 960, maxHeight: "90vh", overflow: "auto" }}>
        {children}
      </div>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path={ROUTES.HOME} element={<MainDashboard />} />
      <Route path={ROUTES.HISTORY} element={<HistoryPage />} />
      <Route path={ROUTES.HISTORY_DETAIL} element={<HistoryPage />} />
      {FEATURES.DISCDB && <Route path={ROUTES.CONTRIBUTE} element={<ContributePage />} />}
      <Route path="/library" element={<Navigate to={ROUTES.HISTORY} replace />} />
      <Route path={ROUTES.REVIEW} element={<Navigate to={ROUTES.HOME} replace />} />
      <Route path={ROUTES.REVIEW_DETAIL} element={<ReviewQueue />} />
    </Routes>
  );
}

export default App;

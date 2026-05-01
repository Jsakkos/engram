import { useState, useEffect } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { Trash2, LayoutGrid, List, Info, X } from "lucide-react";
import { DiscCard, type DiscData } from "./components/DiscCard";
import { useJobManagement } from "./hooks/useJobManagement";
import { useDiscFilters } from "./hooks/useDiscFilters";
import { useNotifications } from "./hooks/useNotifications";
import ReviewQueue from "../components/ReviewQueue";
import ConfigWizard from "../components/ConfigWizard";
import NamePromptModal from "../components/NamePromptModal";
import ReIdentifyModal from "../components/ReIdentifyModal";
import HistoryPage from "../components/HistoryPage";
import ContributePage from "../components/ContributePage";
import LibraryPage from "../components/LibraryPage";
import { FEATURES } from "../config/constants";
import type { Job } from "../types";
import {
  SvAtmosphere,
  SvTopBar,
  SvStatusBar,
  sv,
} from "./components/synapse";
import { DashboardSideRail } from "./components/DashboardSideRail";

type ViewMode = "expanded" | "compact";

function MainDashboard() {
  const navigate = useNavigate();
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [namePromptJob, setNamePromptJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("expanded");
  const [platform, setPlatform] = useState<string | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [contributionPending, setContributionPending] = useState(0);

  // Check for development mock mode
  const DEV_MODE = window.location.search.includes('mock=true');

  // Check if first-run setup is needed + fetch contribution badge count
  useEffect(() => {
    const checkSetup = async () => {
      try {
        const response = await fetch('/api/config');
        if (!response.ok) return;
        const data = await response.json();
        if (!data.setup_complete) {
          setShowOnboarding(true);
        }
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
        // Backend not reachable — don't block the UI
      }
    };
    checkSetup();
  }, []);

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
        // Backend not reachable — don't show banner
      }
    };
    detectPlatform();
  }, []);

  // Job management with WebSocket
  const { jobs, titlesMap, isConnected, cancelJob, clearCompleted, setJobName, reIdentifyJob } = useJobManagement(DEV_MODE);
  const [reIdentifyTarget, setReIdentifyTarget] = useState<Job | null>(null);

  // Disc filtering and transformation
  const { filter, setFilter, discsData, filteredDiscs, activeCount, completedCount } = useDiscFilters(jobs, titlesMap, DEV_MODE);

  // Browser notifications for job state changes
  useNotifications(jobs);

  // Show name prompt modal for jobs that need a name (generic/unreadable volume label)
  useEffect(() => {
    const needsName = jobs.find(
      (j) =>
        j.state === 'review_needed' &&
        j.review_reason?.includes('label unreadable') &&
        !j.detected_title,
    );
    setNamePromptJob(needsName ?? null);
  }, [jobs]);

  const reviewCount = jobs.filter((j) => j.state === 'review_needed').length;

  const navItems = [
    { label: "DASHBOARD", to: "/" },
    { label: "REVIEW", to: "/review", badge: reviewCount },
    { label: "LIBRARY", to: "/library" },
    { label: "HISTORY", to: "/history" },
    { label: "CONTRIBUTE", to: "/contribute", badge: contributionPending, show: FEATURES.DISCDB },
  ];

  return (
    <SvAtmosphere>
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
          {(["all", "active", "completed"] as const).map((f) => {
            const counts = { all: discsData.length, active: activeCount, completed: completedCount };
            const labels = { all: "ALL", active: "ACTIVE", completed: "DONE" };
            const active = filter === f;
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                data-testid={`sv-filter-${f}`}
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
                  transition: "all 0.18s",
                }}
              >
                {labels[f]} [{counts[f]}]
              </button>
            );
          })}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
                padding: "6px 12px",
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.20em",
                textTransform: "uppercase",
                color: sv.red,
                background: "transparent",
                border: `1px solid ${sv.red}55`,
                cursor: "pointer",
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

      {/* Main Content */}
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-20 sm:pb-24 relative z-0">
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
              {/* Synapse beacon — concentric rings + rotating sweep + chapter ticks. Same
                  visual language as SvDiscInsert but simplified for "no signal yet" semantics. */}
              <svg
                width={140}
                height={140}
                viewBox="0 0 200 200"
                aria-label="Engram beacon — awaiting input"
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
              {filter === "active" && "› No active operations"}
              {filter === "completed" && "› No completed archives"}
              {filter === "all" && "› No discs detected"}
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
              {filter === "active" && "All operations complete. Insert a disc to start a new job."}
              {filter === "completed" && "No archived media yet. Completed jobs will appear here."}
              {filter === "all" && "Insert a disc into your optical drive to begin archiving."}
            </p>
          </motion.div>
        ) : viewMode === "compact" ? (
          /* Compact view — sv-token row layout */
          <CompactList
            discs={filteredDiscs}
            onReview={(id) => navigate(`/review/${id}`)}
            onCancel={(id) => cancelJob(id)}
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
                  onReview={disc.needsReview ? () => navigate(`/review/${disc.id}`) : undefined}
                  onReIdentify={disc.needsReview && disc.title ? () => {
                    const job = jobs.find(j => String(j.id) === disc.id);
                    if (job) setReIdentifyTarget(job);
                  } : undefined}
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

      {/* Name Prompt Modal — appears when disc label is unreadable */}
      <AnimatePresence>
        {namePromptJob && (
          <NamePromptModal
            job={namePromptJob}
            onSubmit={(name, contentType, season) => {
              setJobName(namePromptJob.id, name, contentType, season);
              setNamePromptJob(null);
            }}
            onCancel={() => {
              cancelJob(String(namePromptJob.id));
              setNamePromptJob(null);
            }}
          />
        )}
      </AnimatePresence>

      {/* Re-Identify Modal — appears when user clicks "Wrong title?" */}
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

      {/* Onboarding Wizard (first run) */}
      {showOnboarding && (
        <ModalScrim>
          <ConfigWizard
            onClose={() => setShowOnboarding(false)}
            onComplete={() => setShowOnboarding(false)}
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

/**
 * Compact list view for the dashboard — sv-token row layout that mirrors the
 * SvPanel vocabulary used elsewhere (1px tinted border, sharp corners, mono
 * uppercase headers).
 */
function CompactList({
  discs,
  onReview,
  onCancel,
}: {
  discs: DiscData[];
  onReview: (id: string) => void;
  onCancel: (id: string) => void;
}) {
  const colTemplate = "auto auto 1fr 140px 60px auto";
  const stateColor: Partial<Record<DiscData["state"], string>> = {
    completed: sv.green,
    error: sv.red,
    ripping: sv.magenta,
    scanning: sv.cyan,
    review_needed: sv.yellow,
    matching: sv.amber,
    organizing: sv.purple,
  };
  const typeColor: Record<DiscData["mediaType"], string> = {
    movie: sv.magenta,
    tv: sv.cyan,
    unknown: sv.inkFaint,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {/* Column header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: colTemplate,
          columnGap: 16,
          padding: "8px 12px",
          fontFamily: sv.mono,
          fontSize: 9,
          fontWeight: 700,
          letterSpacing: "0.22em",
          textTransform: "uppercase",
          color: sv.inkFaint,
          borderBottom: `1px solid ${sv.line}`,
        }}
      >
        <span>State</span>
        <span>Type</span>
        <span>Title</span>
        <span>Progress</span>
        <span style={{ textAlign: "right" }}>ETA</span>
        <span>Actions</span>
      </div>
      <AnimatePresence mode="popLayout">
        {discs.map((disc) => {
          const stateC = stateColor[disc.state] ?? sv.inkDim;
          const typeC = typeColor[disc.mediaType];
          const showProgress = disc.progress > 0 && disc.state !== "completed";
          return (
            <motion.div
              key={disc.id}
              layout
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              style={{
                display: "grid",
                gridTemplateColumns: colTemplate,
                columnGap: 16,
                alignItems: "center",
                padding: "10px 12px",
                background: sv.bg1,
                border: `1px solid ${sv.line}`,
                fontFamily: sv.mono,
                fontSize: 12,
                transition: "background 120ms, border-color 120ms",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = sv.bg2;
                e.currentTarget.style.borderColor = sv.lineMid;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = sv.bg1;
                e.currentTarget.style.borderColor = sv.line;
              }}
            >
              {/* State */}
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    background: stateC,
                    boxShadow: `0 0 6px ${stateC}aa`,
                    flexShrink: 0,
                  }}
                />
                <span
                  style={{
                    color: sv.inkDim,
                    textTransform: "uppercase",
                    letterSpacing: "0.16em",
                    fontSize: 10,
                    width: 76,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {disc.state}
                </span>
              </div>
              {/* Type */}
              <span
                style={{
                  color: typeC,
                  fontWeight: 700,
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: "0.18em",
                }}
              >
                {disc.mediaType === "unknown" ? "…" : disc.mediaType}
              </span>
              {/* Title */}
              <span
                style={{
                  color: sv.ink,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {disc.title}
              </span>
              {/* Progress */}
              <div>
                {showProgress ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div
                      style={{
                        flex: 1,
                        height: 3,
                        background: sv.bg3,
                        position: "relative",
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          inset: "0 auto 0 0",
                          width: `${disc.progress}%`,
                          background: `linear-gradient(90deg, ${sv.cyan}, ${sv.cyanHi})`,
                          boxShadow: `0 0 6px ${sv.cyan}88`,
                          transition: "width 0.3s ease",
                        }}
                      />
                    </div>
                    <span
                      className="sv-tnum"
                      style={{ color: sv.cyanHi, fontSize: 10, fontWeight: 700 }}
                    >
                      {disc.progress.toFixed(0)}%
                    </span>
                  </div>
                ) : disc.state === "completed" ? (
                  <span style={{ color: sv.green, fontSize: 10, letterSpacing: "0.20em" }}>DONE</span>
                ) : (
                  <span style={{ color: sv.inkFaint }}>—</span>
                )}
              </div>
              {/* ETA */}
              <span
                className="sv-tnum"
                style={{
                  color: sv.inkDim,
                  fontSize: 11,
                  textAlign: "right",
                }}
              >
                {disc.etaSeconds
                  ? disc.etaSeconds < 60
                    ? "< 1m"
                    : `${Math.ceil(disc.etaSeconds / 60)}m`
                  : "—"}
              </span>
              {/* Actions */}
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {disc.needsReview && (
                  <CompactRowButton color={sv.yellow} onClick={() => onReview(disc.id)}>
                    Review
                  </CompactRowButton>
                )}
                {disc.state !== "completed" && disc.state !== "error" && (
                  <CompactRowButton color={sv.red} onClick={() => onCancel(disc.id)}>
                    Cancel
                  </CompactRowButton>
                )}
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
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

function CompactRowButton({
  color,
  onClick,
  children,
}: {
  color: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        height: 22,
        padding: "0 8px",
        background: sv.bg0,
        border: `1px solid ${color}55`,
        color,
        fontFamily: sv.mono,
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: "0.20em",
        textTransform: "uppercase",
        cursor: "pointer",
        transition: "border-color 120ms, box-shadow 120ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = color;
        e.currentTarget.style.boxShadow = `0 0 8px ${color}55`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = `${color}55`;
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {children}
    </button>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<MainDashboard />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/history/:jobId" element={<HistoryPage />} />
      <Route path="/library" element={<LibraryPage />} />
      {FEATURES.DISCDB && <Route path="/contribute" element={<ContributePage />} />}
      <Route path="/review/:jobId" element={<ReviewQueue />} />
    </Routes>
  );
}

export default App;

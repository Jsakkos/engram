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
            className="max-w-7xl mx-auto px-4 sm:px-6 mt-4"
          >
            <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-cyan-500/10 border border-cyan-500/30">
              <Info className="w-5 h-5 text-cyan-400 flex-shrink-0 mt-0.5" />
              <div className="flex-1 text-cyan-300 font-mono text-sm">
                <span>No optical drives detected. Drop MKV folders into your staging directory or </span>
                <button
                  onClick={() => setShowSettings(true)}
                  className="underline underline-offset-2 text-cyan-400 hover:text-cyan-300 transition-colors"
                >
                  configure staging import
                </button>
                <span>.</span>
              </div>
              <button
                onClick={() => setBannerDismissed(true)}
                className="text-cyan-500/60 hover:text-cyan-400 transition-colors flex-shrink-0"
                title="Dismiss"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-20 sm:pb-24 relative z-0">
        <div
          data-testid="sv-dashboard-grid"
          style={{
            display: "grid",
            gridTemplateColumns:
              filteredDiscs.length > 0 && viewMode === "expanded"
                ? "minmax(0, 1.4fr) 320px"
                : "1fr",
            gap: 14,
            alignItems: "start",
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
          /* Compact view */
          <div className="space-y-1">
            <div className="grid grid-cols-[auto_auto_1fr_auto_auto_auto] gap-x-4 px-3 py-2 text-xs font-mono font-bold text-slate-600 uppercase tracking-wider border-b border-navy-600">
              <span>State</span>
              <span>Type</span>
              <span>Title</span>
              <span>Progress</span>
              <span>ETA</span>
              <span>Actions</span>
            </div>
            <AnimatePresence mode="popLayout">
              {filteredDiscs.map((disc: DiscData) => (
                <motion.div
                  key={disc.id}
                  layout
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 10 }}
                  className="grid grid-cols-[auto_auto_1fr_auto_auto_auto] gap-x-4 items-center px-3 py-2.5 rounded-md border border-navy-600/50 bg-navy-800/40 hover:bg-navy-700/40 transition-colors font-mono text-sm"
                >
                  {/* State dot */}
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${
                      disc.state === "completed" ? "bg-green-400" :
                      disc.state === "error" ? "bg-red-400" :
                      disc.state === "ripping" ? "bg-magenta-400" :
                      disc.state === "scanning" ? "bg-cyan-400" :
                      disc.state === "review_needed" ? "bg-yellow-400" :
                      disc.state === "matching" ? "bg-amber-400" :
                      "bg-slate-500"
                    }`} />
                    <span className="text-xs text-slate-500 uppercase w-16 truncate">{disc.state}</span>
                  </div>
                  {/* Type badge */}
                  <span className={`text-xs font-bold uppercase ${
                    disc.mediaType === "movie" ? "text-magenta-400" :
                    disc.mediaType === "tv" ? "text-cyan-400" :
                    "text-slate-500"
                  }`}>
                    {disc.mediaType === "unknown" ? "..." : disc.mediaType}
                  </span>
                  {/* Title */}
                  <span className="text-slate-300 truncate">{disc.title}</span>
                  {/* Progress */}
                  <div className="w-24">
                    {disc.progress > 0 && disc.state !== "completed" ? (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-navy-700 rounded-full overflow-hidden">
                          <div className="h-full bg-cyan-500 rounded-full transition-all" style={{ width: `${disc.progress}%` }} />
                        </div>
                        <span className="text-xs text-cyan-400">{disc.progress.toFixed(0)}%</span>
                      </div>
                    ) : disc.state === "completed" ? (
                      <span className="text-xs text-green-400">DONE</span>
                    ) : (
                      <span className="text-xs text-slate-600">—</span>
                    )}
                  </div>
                  {/* ETA */}
                  <span className="text-xs text-slate-500 w-16 text-right">
                    {disc.etaSeconds ? (disc.etaSeconds < 60 ? "< 1m" : `${Math.ceil(disc.etaSeconds / 60)}m`) : "—"}
                  </span>
                  {/* Actions */}
                  <div className="flex items-center gap-1">
                    {disc.needsReview && (
                      <button
                        onClick={() => navigate(`/review/${disc.id}`)}
                        className="text-xs text-yellow-400 border border-yellow-500/30 px-2 py-0.5 rounded hover:bg-yellow-500/10"
                      >
                        REVIEW
                      </button>
                    )}
                    {disc.state !== "completed" && disc.state !== "error" && (
                      <button
                        onClick={() => cancelJob(disc.id)}
                        className="text-xs text-red-400 border border-red-500/30 px-2 py-0.5 rounded hover:bg-red-500/10"
                      >
                        CANCEL
                      </button>
                    )}
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
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
        <div className="fixed inset-0 z-50 bg-navy-900/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-4xl max-h-[90vh] overflow-auto">
            <ConfigWizard
              onClose={() => setShowOnboarding(false)}
              onComplete={() => setShowOnboarding(false)}
              isOnboarding={true}
            />
          </div>
        </div>
      )}

      {/* Config Wizard Modal (settings) */}
      {showSettings && !showOnboarding && (
        <div className="fixed inset-0 z-50 bg-navy-900/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-4xl max-h-[90vh] overflow-auto">
            <ConfigWizard
              onClose={() => setShowSettings(false)}
              onComplete={() => {
                setShowSettings(false);
              }}
              isOnboarding={false}
            />
          </div>
        </div>
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

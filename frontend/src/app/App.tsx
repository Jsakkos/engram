import { useState, useEffect } from "react";
import { Routes, Route, useNavigate, useLocation, Link } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { Zap, ZapOff, Settings, Trash2, LayoutGrid, List } from "lucide-react";
import { DiscCard, type DiscData } from "./components/DiscCard";
import { useJobManagement } from "./hooks/useJobManagement";
import { useDiscFilters } from "./hooks/useDiscFilters";
import { useNotifications } from "./hooks/useNotifications";
import ReviewQueue from "../components/ReviewQueue";
import ConfigWizard from "../components/ConfigWizard";
import NamePromptModal from "../components/NamePromptModal";
import type { Job } from "../types";

type ViewMode = "expanded" | "compact";

function MainDashboard() {
  const navigate = useNavigate();
  const location = useLocation();
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [namePromptJob, setNamePromptJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("expanded");

  // Check for development mock mode
  const DEV_MODE = window.location.search.includes('mock=true');

  // Check if first-run setup is needed
  useEffect(() => {
    const checkSetup = async () => {
      try {
        const response = await fetch('/api/config');
        if (!response.ok) return;
        const data = await response.json();
        if (!data.setup_complete) {
          setShowOnboarding(true);
        }
      } catch {
        // Backend not reachable — don't block the UI
      }
    };
    checkSetup();
  }, []);

  // Job management with WebSocket
  const { jobs, titlesMap, isConnected, cancelJob, clearCompleted, setJobName } = useJobManagement(DEV_MODE);

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

  const isDashboard = location.pathname === "/";

  return (
    <div className="min-h-screen bg-navy-900 circuit-bg relative overflow-hidden">
      {/* Animated gradient overlay */}
      <motion.div
        className="fixed inset-0 opacity-20 pointer-events-none"
        animate={{
          background: [
            "radial-gradient(circle at 0% 0%, rgba(6, 182, 212, 0.1) 0%, transparent 50%)",
            "radial-gradient(circle at 100% 50%, rgba(245, 158, 11, 0.08) 0%, transparent 50%)",
            "radial-gradient(circle at 100% 100%, rgba(236, 72, 153, 0.1) 0%, transparent 50%)",
            "radial-gradient(circle at 0% 0%, rgba(6, 182, 212, 0.1) 0%, transparent 50%)",
          ],
        }}
        transition={{ duration: 15, repeat: Infinity, ease: "linear" }}
      />

      {/* Header */}
      <div className="border-b-2 border-cyan-500/30 backdrop-blur-xl bg-navy-900/80 sticky top-0 z-10" style={{ boxShadow: "0 0 20px rgba(6, 182, 212, 0.2), 0 0 40px rgba(236, 72, 153, 0.1)" }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 sm:py-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div className="flex items-center gap-4">
              {/* Logo */}
              <motion.div
                animate={{ rotate: [0, 360] }}
                transition={{ duration: 30, repeat: Infinity, ease: "linear" }}
                className="relative w-10 h-10 sm:w-12 sm:h-12 flex-shrink-0"
              >
                <img src="/engram.svg" alt="Engram" className="w-full h-full" style={{ filter: "drop-shadow(0 0 8px rgba(6, 182, 212, 0.6))" }} />
              </motion.div>
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-cyan-400 tracking-[0.2em] font-mono uppercase neon-title">
                  Engram
                </h1>
                <p className="text-xs sm:text-sm text-slate-500 font-mono tracking-wider">&gt; MEDIA ARCHIVAL PLATFORM v{__APP_VERSION__}</p>
              </div>

              {/* Navigation tabs */}
              <nav className="hidden sm:flex items-center gap-1 ml-6">
                <Link
                  to="/"
                  className={`px-3 py-1.5 font-mono font-bold text-xs uppercase tracking-wider transition-all border-b-2 ${
                    isDashboard
                      ? "text-cyan-400 border-cyan-400"
                      : "text-slate-500 border-transparent hover:text-slate-300"
                  }`}
                >
                  Dashboard
                </Link>
                <Link
                  to="/history"
                  className={`px-3 py-1.5 font-mono font-bold text-xs uppercase tracking-wider transition-all border-b-2 ${
                    location.pathname === "/history"
                      ? "text-cyan-400 border-cyan-400"
                      : "text-slate-500 border-transparent hover:text-slate-300"
                  }`}
                >
                  History
                </Link>
              </nav>
            </div>

            {/* Right side: connection status + settings */}
            <div className="flex items-center gap-2 sm:gap-3">
              {/* Connection status pill */}
              <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-bold uppercase tracking-wider ${
                isConnected
                  ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-400"
                  : "bg-slate-500/10 border border-slate-500/30 text-slate-500"
              }`}>
                {isConnected ? <Zap className="w-3 h-3" /> : <ZapOff className="w-3 h-3" />}
                <span className="hidden sm:inline">{isConnected ? "LIVE" : "OFFLINE"}</span>
              </div>
              {DEV_MODE && <span className="text-xs font-mono text-yellow-500 font-bold">[MOCK]</span>}

              {/* Settings Button */}
              <button
                onClick={() => setShowSettings(true)}
                className="p-2 font-mono transition-all rounded-lg border border-transparent hover:border-cyan-500/30 hover:text-cyan-400 text-slate-500"
                title="Settings"
              >
                <Settings className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>

        {/* Secondary toolbar — filter + view controls */}
        <div className="border-t border-cyan-500/10 bg-navy-800/50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 py-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              {(["all", "active", "completed"] as const).map((f) => {
                const counts = { all: discsData.length, active: activeCount, completed: completedCount };
                const labels = { all: "ALL", active: "ACTIVE", completed: "DONE" };
                return (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`px-3 py-1.5 font-mono font-bold text-xs uppercase tracking-wider transition-all rounded-md ${
                      filter === f
                        ? "bg-cyan-500/15 text-cyan-400 border border-cyan-500/40"
                        : "text-slate-500 border border-transparent hover:text-slate-300 hover:border-slate-700"
                    }`}
                  >
                    {labels[f]} [{counts[f]}]
                  </button>
                );
              })}
            </div>

            <div className="flex items-center gap-2">
              {/* View mode toggle */}
              <div className="flex items-center border border-navy-600 rounded-md overflow-hidden">
                <button
                  onClick={() => setViewMode("expanded")}
                  className={`p-1.5 transition-all ${viewMode === "expanded" ? "bg-cyan-500/15 text-cyan-400" : "text-slate-600 hover:text-slate-400"}`}
                  title="Expanded view"
                >
                  <LayoutGrid className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setViewMode("compact")}
                  className={`p-1.5 transition-all ${viewMode === "compact" ? "bg-cyan-500/15 text-cyan-400" : "text-slate-600 hover:text-slate-400"}`}
                  title="Compact view"
                >
                  <List className="w-4 h-4" />
                </button>
              </div>

              {/* Clear Completed */}
              {completedCount > 0 && (
                <button
                  onClick={clearCompleted}
                  className="px-3 py-1.5 font-mono font-bold text-xs uppercase tracking-wider transition-all rounded-md text-red-400 border border-red-500/30 hover:border-red-500 hover:bg-red-500/10 flex items-center gap-1.5"
                  title="Clear Completed"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">CLEAR</span>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-20 sm:pb-24 relative z-0">
        {filteredDiscs.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center py-16 sm:py-20 text-center"
          >
            <motion.div
              className="w-24 h-24 sm:w-32 sm:h-32 mb-6 relative"
              animate={{
                filter: [
                  "drop-shadow(0 0 12px rgba(6, 182, 212, 0.3))",
                  "drop-shadow(0 0 20px rgba(6, 182, 212, 0.5))",
                  "drop-shadow(0 0 12px rgba(6, 182, 212, 0.3))",
                ],
              }}
              transition={{ duration: 3, repeat: Infinity }}
            >
              <motion.img
                src="/engram.svg"
                alt="Engram"
                className="w-full h-full opacity-40"
                animate={{ rotate: [0, 360] }}
                transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
              />
            </motion.div>
            <h2 className="text-lg sm:text-xl font-bold text-cyan-400 mb-2 font-mono uppercase tracking-wider neon-title">
              {filter === "active" && "NO ACTIVE OPERATIONS"}
              {filter === "completed" && "NO COMPLETED ARCHIVES"}
              {filter === "all" && "NO DISCS DETECTED"}
            </h2>
            <p className="text-xs sm:text-sm text-slate-500 font-mono max-w-md">
              {filter === "active" && "> All operations complete. Insert a disc to start a new job."}
              {filter === "completed" && "> No archived media yet. Completed jobs will appear here."}
              {filter === "all" && "> Insert a disc into your optical drive to begin archiving."}
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
                />
              ))}
            </AnimatePresence>
          </div>
        )}
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

      {/* Stats Footer — slimmer */}
      <div className="fixed bottom-0 left-0 right-0 border-t border-cyan-500/20 backdrop-blur-xl bg-navy-900/90">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-2">
          <div className="flex items-center justify-between text-xs font-mono gap-4">
            <div className="flex items-center gap-4 sm:gap-6">
              <div className="flex items-center gap-1.5">
                <motion.div
                  className="w-1.5 h-1.5 rounded-full bg-cyan-400"
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  style={{ boxShadow: "0 0 6px rgba(6, 182, 212, 0.8)" }}
                />
                <span className="text-cyan-400 uppercase tracking-wider font-bold">
                  {activeCount} Active
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-green-400" style={{ boxShadow: "0 0 6px rgba(16, 185, 129, 0.8)" }} />
                <span className="text-green-400 uppercase tracking-wider font-bold">
                  {completedCount} Archived
                </span>
              </div>
            </div>
            <span className="text-slate-600 font-bold tracking-wider">v{__APP_VERSION__}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<MainDashboard />} />
      <Route path="/review/:jobId" element={<ReviewQueue />} />
    </Routes>
  );
}

export default App;

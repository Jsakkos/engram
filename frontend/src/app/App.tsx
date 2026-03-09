import { useState, useEffect } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { Disc3, Zap, ZapOff, Settings, Trash2 } from "lucide-react";
import { DiscCard, type DiscData } from "./components/DiscCard";
import { useJobManagement } from "./hooks/useJobManagement";
import { useDiscFilters } from "./hooks/useDiscFilters";
import { useNotifications } from "./hooks/useNotifications";
import ReviewQueue from "../components/ReviewQueue";
import ConfigWizard from "../components/ConfigWizard";
import NamePromptModal from "../components/NamePromptModal";
import type { Job } from "../types";

function MainDashboard() {
  const navigate = useNavigate();
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [namePromptJob, setNamePromptJob] = useState<Job | null>(null);

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

  return (
    <div className="min-h-screen bg-black relative overflow-hidden">
      {/* Cyberpunk grid background */}
      <div className="fixed inset-0 opacity-10 pointer-events-none">
        <div
          className="h-full w-full"
          style={{
            backgroundImage: `
              linear-gradient(rgba(6, 182, 212, 0.3) 1px, transparent 1px),
              linear-gradient(90deg, rgba(6, 182, 212, 0.3) 1px, transparent 1px)
            `,
            backgroundSize: "50px 50px",
          }}
        />
      </div>

      {/* Animated gradient overlay */}
      <motion.div
        className="fixed inset-0 opacity-20 pointer-events-none"
        animate={{
          background: [
            "radial-gradient(circle at 0% 0%, rgba(6, 182, 212, 0.1) 0%, transparent 50%)",
            "radial-gradient(circle at 100% 100%, rgba(236, 72, 153, 0.1) 0%, transparent 50%)",
            "radial-gradient(circle at 0% 0%, rgba(6, 182, 212, 0.1) 0%, transparent 50%)",
          ],
        }}
        transition={{ duration: 10, repeat: Infinity, ease: "linear" }}
      />

      {/* Header */}
      <div className="border-b-2 border-cyan-500/30 backdrop-blur-xl bg-black/80 sticky top-0 z-10" style={{ boxShadow: "0 0 20px rgba(6, 182, 212, 0.2), 0 0 40px rgba(236, 72, 153, 0.1)" }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 sm:py-6">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <motion.div
                animate={{ rotate: [0, 360] }}
                transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
                className="relative"
              >
                <Disc3 className="w-8 h-8 sm:w-10 sm:h-10 text-cyan-400" style={{ filter: "drop-shadow(0 0 10px rgba(6, 182, 212, 0.8))" }} />
                <motion.div
                  className="absolute inset-0"
                  animate={{ scale: [1, 1.5], opacity: [0.5, 0] }}
                  transition={{ duration: 2, repeat: Infinity }}
                >
                  <Disc3 className="w-8 h-8 sm:w-10 sm:h-10 text-cyan-400" />
                </motion.div>
              </motion.div>
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-cyan-400 tracking-wider font-mono uppercase" style={{ textShadow: "0 0 10px rgba(6, 182, 212, 1), 0 0 30px rgba(6, 182, 212, 0.6), 0 0 60px rgba(6, 182, 212, 0.3), 0 0 80px rgba(236, 72, 153, 0.2)" }}>
                  Engram
                </h1>
                <p className="text-xs sm:text-sm text-slate-400 font-mono tracking-wider">&gt; MEDIA ARCHIVAL PLATFORM v{__APP_VERSION__}</p>
              </div>
            </div>

            {/* Filter Controls and Actions */}
            <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
              {/* Settings Button */}
              <button
                onClick={() => setShowSettings(true)}
                className="px-3 py-2 font-mono font-bold text-sm uppercase tracking-wider transition-all border-2 bg-black text-slate-400 border-slate-700 hover:border-cyan-500/50 hover:text-cyan-400"
                title="Settings"
              >
                <Settings className="w-5 h-5" />
              </button>

              {/* Clear Completed Button */}
              {completedCount > 0 && (
                <button
                  onClick={clearCompleted}
                  className="px-3 py-2 font-mono font-bold text-sm uppercase tracking-wider transition-all border-2 bg-black text-red-400 border-red-700 hover:border-red-500 hover:text-red-300 flex items-center gap-2"
                  title="Clear Completed"
                >
                  <Trash2 className="w-4 h-4" />
                  <span>CLEAR</span>
                </button>
              )}

              <button
                onClick={() => setFilter("all")}
                className="px-4 py-2 font-mono font-bold text-sm uppercase tracking-wider transition-all border-2 bg-black text-slate-400 border-slate-700 hover:text-slate-200"
                style={filter === "all" ? { boxShadow: "0 0 15px rgba(6, 182, 212, 0.5), 0 0 30px rgba(236, 72, 153, 0.2)", borderColor: "rgba(6, 182, 212, 0.7)" } : {}}
              >
                ALL [{discsData.length}]
              </button>
              <button
                onClick={() => setFilter("active")}
                className="px-4 py-2 font-mono font-bold text-sm uppercase tracking-wider transition-all border-2 bg-black text-slate-400 border-slate-700 hover:text-slate-200"
                style={filter === "active" ? { boxShadow: "0 0 15px rgba(6, 182, 212, 0.5), 0 0 30px rgba(236, 72, 153, 0.2)", borderColor: "rgba(6, 182, 212, 0.7)" } : {}}
              >
                ACTIVE [{activeCount}]
              </button>
              <button
                onClick={() => setFilter("completed")}
                className="px-4 py-2 font-mono font-bold text-sm uppercase tracking-wider transition-all border-2 bg-black text-slate-400 border-slate-700 hover:text-slate-200"
                style={filter === "completed" ? { boxShadow: "0 0 15px rgba(6, 182, 212, 0.5), 0 0 30px rgba(236, 72, 153, 0.2)", borderColor: "rgba(6, 182, 212, 0.7)" } : {}}
              >
                DONE [{completedCount}]
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-24 sm:pb-28 relative z-0">
        {filteredDiscs.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center py-16 sm:py-20 text-center"
          >
            <motion.div
              className="w-20 h-20 sm:w-24 sm:h-24 border-2 border-cyan-500/50 flex items-center justify-center mb-6"
              style={{ clipPath: "polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)" }}
              animate={{ borderColor: ["rgba(6, 182, 212, 0.3)", "rgba(6, 182, 212, 0.6)", "rgba(6, 182, 212, 0.3)"] }}
              transition={{ duration: 3, repeat: Infinity }}
            >
              <motion.div
                animate={{ rotate: [0, 360] }}
                transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
              >
                <Disc3 className="w-10 h-10 sm:w-12 sm:h-12 text-cyan-500/40" />
              </motion.div>
            </motion.div>
            <h2 className="text-lg sm:text-xl font-bold text-cyan-400 mb-2 font-mono uppercase tracking-wider" style={{ textShadow: "0 0 10px rgba(6, 182, 212, 0.5)" }}>
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
        ) : (
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
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
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
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-4xl max-h-[90vh] overflow-auto">
            <ConfigWizard
              onClose={() => setShowSettings(false)}
              onComplete={() => {
                setShowSettings(false);
                // Jobs will refresh automatically via WebSocket
              }}
              isOnboarding={false}
            />
          </div>
        </div>
      )}

      {/* Stats Footer */}
      <div className="fixed bottom-0 left-0 right-0 border-t-2 border-cyan-500/30 backdrop-blur-xl bg-black/90" style={{ boxShadow: "0 0 20px rgba(6, 182, 212, 0.2), 0 0 40px rgba(236, 72, 153, 0.1)" }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 sm:py-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between text-xs sm:text-sm font-mono gap-2 sm:gap-0">
            <div className="flex items-center gap-4 sm:gap-8">
              <div className="flex items-center gap-2">
                <motion.div
                  className="w-2 h-2 bg-cyan-400"
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  style={{ boxShadow: "0 0 10px rgba(6, 182, 212, 0.8)" }}
                />
                <span className="text-cyan-400 uppercase tracking-wider">
                  {activeCount} ACTIVE
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-green-400" style={{ boxShadow: "0 0 10px rgba(16, 185, 129, 0.8)" }} />
                <span className="text-green-400 uppercase tracking-wider">
                  {completedCount} ARCHIVED
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2 text-yellow-400">
              {isConnected ? (
                <>
                  <Zap className="w-4 h-4" />
                  <span className="uppercase tracking-wider hidden sm:inline">WEBSOCKET CONNECTED</span>
                  <span className="uppercase tracking-wider sm:hidden">CONNECTED</span>
                </>
              ) : (
                <>
                  <ZapOff className="w-4 h-4 text-slate-500" />
                  <span className="uppercase tracking-wider text-slate-500">DISCONNECTED</span>
                </>
              )}
              {DEV_MODE && <span className="ml-4 text-yellow-500">[MOCK]</span>}
            </div>
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

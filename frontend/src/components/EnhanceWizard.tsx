import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  ChevronLeft,
  ChevronRight,
  Search,
  Loader2,
  Check,
  Image,
  Barcode,
  Tag,
  Film,
  Save,
} from "lucide-react";

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

export interface TitleInfo {
  id: number;
  title_index: number;
  duration_seconds: number;
  matched_episode: string | null;
  match_source: string | null;
  match_confidence: number;
  is_extra: boolean;
  extra_description: string | null;
}

interface EnhanceWizardProps {
  job: ContributionJob;
  titles: TitleInfo[];
  onSave: () => void;
  onCancel: () => void;
}

interface LookupResult {
  product_title: string | null;
  brand: string | null;
  match_confidence: "high" | "low" | "none";
  asins: string[];
  images: string[];
  release_date: string | null;
}

type StepId = "upc" | "asin" | "cover" | "extras" | "save";

interface StepDef {
  id: StepId;
  label: string;
  icon: React.ReactNode;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function EnhanceWizard({ job, titles, onSave, onCancel }: EnhanceWizardProps) {
  const [upc, setUpc] = useState(job.upc_code || "");
  const [lookupResult, setLookupResult] = useState<LookupResult | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);

  const [selectedAsin, setSelectedAsin] = useState(job.asin || "");
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [coverSaved, setCoverSaved] = useState(false);
  const [coverSaving, setCoverSaving] = useState(false);

  const [extraDescriptions, setExtraDescriptions] = useState<Record<number, string>>(() => {
    const init: Record<number, string> = {};
    for (const t of titles) {
      if (t.is_extra) init[t.id] = t.extra_description || "";
    }
    return init;
  });

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const extras = useMemo(() => titles.filter((t) => t.is_extra), [titles]);

  const allSteps: StepDef[] = useMemo(() => {
    const steps: StepDef[] = [
      { id: "upc", label: "UPC", icon: <Barcode className="w-3.5 h-3.5" /> },
    ];
    if (lookupResult && lookupResult.asins.length > 0) {
      steps.push({ id: "asin", label: "ASIN", icon: <Tag className="w-3.5 h-3.5" /> });
    }
    if (lookupResult && lookupResult.images.length > 0) {
      steps.push({ id: "cover", label: "Cover", icon: <Image className="w-3.5 h-3.5" /> });
    }
    if (extras.length > 0) {
      steps.push({ id: "extras", label: "Extras", icon: <Film className="w-3.5 h-3.5" /> });
    }
    steps.push({ id: "save", label: "Save", icon: <Save className="w-3.5 h-3.5" /> });
    return steps;
  }, [lookupResult, extras.length]);

  const [currentStepId, setCurrentStepId] = useState<StepId>("upc");

  const currentIdx = allSteps.findIndex((s) => s.id === currentStepId);

  useEffect(() => {
    if (currentIdx === -1 && allSteps.length > 0) {
      setCurrentStepId(allSteps[0].id);
    }
  }, [allSteps, currentIdx]);

  const goNext = () => {
    if (currentIdx < allSteps.length - 1) {
      setCurrentStepId(allSteps[currentIdx + 1].id);
    }
  };

  const goBack = () => {
    if (currentIdx > 0) {
      setCurrentStepId(allSteps[currentIdx - 1].id);
    }
  };

  const handleLookup = async () => {
    if (!upc.trim()) return;
    setLookupLoading(true);
    setLookupError(null);
    try {
      const res = await fetch(`/api/contributions/${job.id}/upc-lookup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upc_code: upc.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Lookup failed" }));
        throw new Error(err.detail || "Lookup failed");
      }
      const data: LookupResult = await res.json();
      setLookupResult(data);
      if (data.asins.length === 1) setSelectedAsin(data.asins[0]);
    } catch (e) {
      setLookupError(e instanceof Error ? e.message : "Lookup failed");
    } finally {
      setLookupLoading(false);
    }
  };

  const handleFetchCover = async () => {
    if (!selectedImage) return;
    setCoverSaving(true);
    try {
      const res = await fetch(`/api/contributions/${job.id}/fetch-cover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_url: selectedImage }),
      });
      if (res.ok) setCoverSaved(true);
    } finally {
      setCoverSaving(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const body: Record<string, unknown> = {
        upc_code: upc.trim() || null,
        asin: selectedAsin || null,
        release_date: lookupResult?.release_date || job.release_date || null,
        extra_descriptions: Object.fromEntries(
          Object.entries(extraDescriptions)
            .filter(([, v]) => v.trim())
            .map(([k, v]) => [k, v.trim()])
        ),
      };
      const res = await fetch(`/api/contributions/${job.id}/enhance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Save failed" }));
        throw new Error(err.detail || "Save failed");
      }
      onSave();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const confidenceColor = (c: string) => {
    if (c === "high") return "text-green-400";
    if (c === "low") return "text-amber-400";
    return "text-red-400";
  };

  const confidenceLabel = (c: string) => {
    if (c === "high") return "High Match";
    if (c === "low") return "Low Match";
    return "No Match";
  };

  return (
    <div className="bg-navy-900/50 border border-navy-600/50 rounded-lg p-5 font-mono">
      {/* Step indicator */}
      <div className="flex items-center gap-1 mb-6 overflow-x-auto">
        {allSteps.map((step, i) => {
          const isCurrent = step.id === currentStepId;
          const isCompleted = i < currentIdx;
          return (
            <div key={step.id} className="flex items-center gap-1">
              {i > 0 && (
                <div
                  className={`w-6 h-px ${isCompleted ? "bg-cyan-500/50" : "bg-navy-600"}`}
                />
              )}
              <div
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
                  isCurrent
                    ? "text-cyan-400 bg-cyan-500/10 border border-cyan-500/30"
                    : isCompleted
                      ? "text-cyan-400/60 border border-cyan-500/15"
                      : "text-slate-600 border border-navy-600"
                }`}
              >
                {isCompleted ? <Check className="w-3 h-3" /> : step.icon}
                {step.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* Step content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={currentStepId}
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -20 }}
          transition={{ duration: 0.15 }}
          className="min-h-[180px]"
        >
          {/* Step 1: UPC */}
          {currentStepId === "upc" && (
            <div>
              <h3 className="text-sm font-bold text-cyan-400 mb-3">UPC CODE LOOKUP</h3>
              <div className="flex items-end gap-3 mb-4">
                <div className="flex-1 max-w-sm">
                  <label className="text-xs text-slate-500 block mb-1">UPC / Barcode</label>
                  <input
                    type="text"
                    value={upc}
                    onChange={(e) => setUpc(e.target.value)}
                    placeholder="e.g., 883929123456"
                    className="w-full px-3 py-1.5 bg-navy-800 border border-navy-600 rounded text-sm text-slate-300 font-mono placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none"
                    onKeyDown={(e) => e.key === "Enter" && handleLookup()}
                  />
                </div>
                <button
                  onClick={handleLookup}
                  disabled={lookupLoading || !upc.trim()}
                  className="px-4 py-1.5 text-xs font-bold uppercase text-cyan-400 border border-cyan-500/30 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-1.5"
                >
                  {lookupLoading ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Search className="w-3.5 h-3.5" />
                  )}
                  Lookup
                </button>
              </div>

              {lookupError && (
                <p className="text-xs text-red-400 mb-3">{lookupError}</p>
              )}

              {lookupResult && (
                <div className="border border-navy-600/50 rounded p-3 bg-navy-800/30 space-y-1.5">
                  {lookupResult.product_title && (
                    <p className="text-sm text-slate-300">{lookupResult.product_title}</p>
                  )}
                  {lookupResult.brand && (
                    <p className="text-xs text-slate-500">Brand: {lookupResult.brand}</p>
                  )}
                  <p
                    className={`text-xs font-bold ${confidenceColor(lookupResult.match_confidence)}`}
                  >
                    {confidenceLabel(lookupResult.match_confidence)}
                  </p>
                  {lookupResult.release_date && (
                    <p className="text-xs text-slate-500">
                      Release: {lookupResult.release_date}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 2: ASIN */}
          {currentStepId === "asin" && lookupResult && (
            <div>
              <h3 className="text-sm font-bold text-cyan-400 mb-3">SELECT ASIN</h3>
              <div className="space-y-2">
                {lookupResult.asins.map((asin) => (
                  <label
                    key={asin}
                    className={`flex items-center gap-3 px-3 py-2 rounded border cursor-pointer transition-colors ${
                      selectedAsin === asin
                        ? "border-cyan-500/30 bg-cyan-500/5 text-slate-300"
                        : "border-navy-600 bg-navy-800/30 text-slate-400 hover:border-navy-500"
                    }`}
                  >
                    <input
                      type="radio"
                      name="asin"
                      value={asin}
                      checked={selectedAsin === asin}
                      onChange={() => setSelectedAsin(asin)}
                      className="accent-cyan-400"
                    />
                    <span className="text-sm">{asin}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Step 3: Cover Art */}
          {currentStepId === "cover" && lookupResult && (
            <div>
              <h3 className="text-sm font-bold text-cyan-400 mb-3">COVER ART</h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                {lookupResult.images.slice(0, 4).map((url) => (
                  <button
                    key={url}
                    onClick={() => {
                      setSelectedImage(url);
                      setCoverSaved(false);
                    }}
                    className={`aspect-[2/3] rounded border-2 overflow-hidden transition-colors ${
                      selectedImage === url
                        ? "border-cyan-400"
                        : "border-navy-600 hover:border-navy-500"
                    }`}
                  >
                    <img
                      src={url}
                      alt="Cover"
                      className="w-full h-full object-cover"
                    />
                  </button>
                ))}
              </div>
              {selectedImage && (
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleFetchCover}
                    disabled={coverSaving || coverSaved}
                    className="px-4 py-1.5 text-xs font-bold uppercase text-cyan-400 border border-cyan-500/30 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {coverSaving ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : coverSaved ? (
                      <Check className="w-3.5 h-3.5" />
                    ) : (
                      <Image className="w-3.5 h-3.5" />
                    )}
                    {coverSaved ? "Saved!" : "Fetch Cover"}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Step 4: Extras */}
          {currentStepId === "extras" && (
            <div>
              <h3 className="text-sm font-bold text-cyan-400 mb-3">ANNOTATE EXTRAS</h3>
              <div className="space-y-3">
                {extras.map((t) => (
                  <div
                    key={t.id}
                    className="flex items-center gap-3 border border-navy-600/50 rounded p-3 bg-navy-800/30"
                  >
                    <div className="flex-shrink-0">
                      <span className="text-xs text-slate-500">
                        #{t.title_index}
                      </span>
                      <span className="text-xs text-slate-600 ml-2">
                        {formatDuration(t.duration_seconds)}
                      </span>
                    </div>
                    <input
                      type="text"
                      value={extraDescriptions[t.id] || ""}
                      onChange={(e) =>
                        setExtraDescriptions((prev) => ({ ...prev, [t.id]: e.target.value }))
                      }
                      placeholder="Describe this extra..."
                      className="flex-1 px-3 py-1.5 bg-navy-800 border border-navy-600 rounded text-sm text-slate-300 font-mono placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Final: Save */}
          {currentStepId === "save" && (
            <div>
              <h3 className="text-sm font-bold text-cyan-400 mb-3">SAVE ENHANCEMENT</h3>
              <div className="border border-navy-600/50 rounded p-3 bg-navy-800/30 space-y-2 mb-4">
                <p className="text-xs text-slate-500">Summary</p>
                <div className="space-y-1 text-sm text-slate-300">
                  {upc.trim() && <p>UPC: {upc.trim()}</p>}
                  {selectedAsin && <p>ASIN: {selectedAsin}</p>}
                  {coverSaved && <p>Cover art saved</p>}
                  {Object.values(extraDescriptions).filter((v) => v.trim()).length > 0 && (
                    <p>
                      {Object.values(extraDescriptions).filter((v) => v.trim()).length} extra(s)
                      annotated
                    </p>
                  )}
                  {!upc.trim() &&
                    !selectedAsin &&
                    !coverSaved &&
                    Object.values(extraDescriptions).filter((v) => v.trim()).length === 0 && (
                      <p className="text-slate-500">No enhancements to save</p>
                    )}
                </div>
              </div>

              {saveError && <p className="text-xs text-red-400 mb-3">{saveError}</p>}

              <button
                onClick={handleSave}
                disabled={saving}
                className="px-5 py-2 text-xs font-bold uppercase text-cyan-400 border border-cyan-500/30 rounded hover:bg-cyan-500/10 disabled:opacity-50 flex items-center gap-2"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                Save Enhancement
              </button>
            </div>
          )}
        </motion.div>
      </AnimatePresence>

      {/* Navigation */}
      <div className="flex items-center justify-between mt-6 pt-4 border-t border-navy-600/50">
        <div>
          {currentIdx > 0 ? (
            <button
              onClick={goBack}
              className="px-3 py-1.5 text-xs font-bold uppercase text-slate-400 border border-navy-600 rounded hover:text-slate-300 hover:border-navy-500 flex items-center gap-1"
            >
              <ChevronLeft className="w-3.5 h-3.5" /> Back
            </button>
          ) : (
            <button
              onClick={onCancel}
              className="px-3 py-1.5 text-xs font-bold uppercase text-slate-500 border border-navy-600 rounded hover:text-slate-400 hover:border-navy-500"
            >
              Cancel
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {currentStepId === "upc" && (
            <button
              onClick={goNext}
              className="px-3 py-1.5 text-xs font-bold uppercase text-slate-400 border border-navy-600 rounded hover:text-slate-300 hover:border-navy-500"
            >
              Skip Lookup
            </button>
          )}
          {currentStepId !== "save" && (
            <button
              onClick={goNext}
              className="px-3 py-1.5 text-xs font-bold uppercase text-magenta-400 border border-magenta-500/30 rounded hover:bg-magenta-500/10 flex items-center gap-1"
            >
              Next <ChevronRight className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

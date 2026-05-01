import { useState, useEffect, useMemo, type CSSProperties } from "react";
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
import { SvActionButton, SvLabel, SvNotice, SvPanel, sv } from "../app/components/synapse";

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

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  background: sv.bg0,
  border: `1px solid ${sv.lineMid}`,
  color: sv.ink,
  fontFamily: sv.mono,
  fontSize: 12,
  letterSpacing: "0.04em",
  outline: "none",
  transition: "border-color 120ms, box-shadow 120ms",
};

function focusInput(e: React.FocusEvent<HTMLInputElement>) {
  e.currentTarget.style.borderColor = sv.cyan;
  e.currentTarget.style.boxShadow = `0 0 8px ${sv.cyan}33`;
}

function blurInput(e: React.FocusEvent<HTMLInputElement>) {
  e.currentTarget.style.borderColor = sv.lineMid;
  e.currentTarget.style.boxShadow = "none";
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
      { id: "upc", label: "UPC", icon: <Barcode size={14} /> },
    ];
    if (lookupResult && lookupResult.asins.length > 0) {
      steps.push({ id: "asin", label: "ASIN", icon: <Tag size={14} /> });
    }
    if (lookupResult && lookupResult.images.length > 0) {
      steps.push({ id: "cover", label: "Cover", icon: <Image size={14} /> });
    }
    if (extras.length > 0) {
      steps.push({ id: "extras", label: "Extras", icon: <Film size={14} /> });
    }
    steps.push({ id: "save", label: "Save", icon: <Save size={14} /> });
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
    if (currentIdx < allSteps.length - 1) setCurrentStepId(allSteps[currentIdx + 1].id);
  };
  const goBack = () => {
    if (currentIdx > 0) setCurrentStepId(allSteps[currentIdx - 1].id);
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
            .map(([k, v]) => [k, v.trim()]),
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
    if (c === "high") return sv.green;
    if (c === "low") return sv.amber;
    return sv.red;
  };

  const confidenceLabel = (c: string) => {
    if (c === "high") return "High match";
    if (c === "low") return "Low match";
    return "No match";
  };

  return (
    <SvPanel pad={20}>
      {/* Step indicator */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 24, overflowX: "auto" }}>
        {allSteps.map((step, i) => {
          const isCurrent = step.id === currentStepId;
          const isCompleted = i < currentIdx;
          const fg = isCurrent ? sv.cyan : isCompleted ? `${sv.cyan}99` : sv.inkFaint;
          const border = isCurrent ? `${sv.cyan}66` : isCompleted ? `${sv.cyan}33` : sv.line;
          const bg = isCurrent ? `${sv.cyan}10` : "transparent";
          return (
            <div key={step.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {i > 0 && (
                <div
                  style={{
                    width: 24,
                    height: 1,
                    background: isCompleted ? `${sv.cyan}66` : sv.line,
                  }}
                />
              )}
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  background: bg,
                  border: `1px solid ${border}`,
                  color: fg,
                  fontFamily: sv.mono,
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                }}
              >
                {isCompleted ? <Check size={12} /> : step.icon}
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
          style={{ minHeight: 180 }}
        >
          {currentStepId === "upc" && (
            <div>
              <SvLabel>UPC code lookup</SvLabel>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 12, marginTop: 12, marginBottom: 12 }}>
                <div style={{ flex: 1, maxWidth: 360 }}>
                  <label
                    style={{
                      display: "block",
                      marginBottom: 6,
                      fontFamily: sv.mono,
                      fontSize: 10,
                      letterSpacing: "0.20em",
                      textTransform: "uppercase",
                      color: sv.inkFaint,
                    }}
                  >
                    UPC / Barcode
                  </label>
                  <input
                    type="text"
                    value={upc}
                    onChange={(e) => setUpc(e.target.value)}
                    placeholder="e.g., 883929123456"
                    style={inputStyle}
                    onFocus={focusInput}
                    onBlur={blurInput}
                    onKeyDown={(e) => e.key === "Enter" && handleLookup()}
                  />
                </div>
                <SvActionButton
                  tone="cyan"
                  onClick={handleLookup}
                  disabled={lookupLoading || !upc.trim()}
                >
                  {lookupLoading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                  Lookup
                </SvActionButton>
              </div>

              {lookupError && (
                <div style={{ marginTop: 12 }}>
                  <SvNotice tone="error">{lookupError}</SvNotice>
                </div>
              )}

              {lookupResult && (
                <SvPanel pad={12} style={{ marginTop: 12 }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {lookupResult.product_title && (
                      <p style={{ margin: 0, fontFamily: sv.mono, fontSize: 13, color: sv.ink }}>
                        {lookupResult.product_title}
                      </p>
                    )}
                    {lookupResult.brand && (
                      <p style={{ margin: 0, fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>
                        Brand: {lookupResult.brand}
                      </p>
                    )}
                    <p
                      style={{
                        margin: 0,
                        fontFamily: sv.mono,
                        fontSize: 11,
                        fontWeight: 700,
                        letterSpacing: "0.16em",
                        textTransform: "uppercase",
                        color: confidenceColor(lookupResult.match_confidence),
                      }}
                    >
                      {confidenceLabel(lookupResult.match_confidence)}
                    </p>
                    {lookupResult.release_date && (
                      <p style={{ margin: 0, fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>
                        Release: {lookupResult.release_date}
                      </p>
                    )}
                  </div>
                </SvPanel>
              )}
            </div>
          )}

          {currentStepId === "asin" && lookupResult && (
            <div>
              <SvLabel>Select ASIN</SvLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
                {lookupResult.asins.map((asin) => {
                  const checked = selectedAsin === asin;
                  return (
                    <label
                      key={asin}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        padding: "10px 12px",
                        background: checked ? `${sv.cyan}10` : sv.bg1,
                        border: `1px solid ${checked ? sv.cyan : sv.line}`,
                        color: checked ? sv.ink : sv.inkDim,
                        cursor: "pointer",
                        fontFamily: sv.mono,
                        fontSize: 12,
                        transition: "background 120ms, border-color 120ms",
                      }}
                    >
                      <input
                        type="radio"
                        name="asin"
                        value={asin}
                        checked={checked}
                        onChange={() => setSelectedAsin(asin)}
                        style={{ accentColor: sv.cyan }}
                      />
                      <span>{asin}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {currentStepId === "cover" && lookupResult && (
            <div>
              <SvLabel>Cover art</SvLabel>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 12,
                  marginTop: 12,
                  marginBottom: 12,
                }}
              >
                {lookupResult.images.slice(0, 4).map((url) => {
                  const selected = selectedImage === url;
                  return (
                    <button
                      key={url}
                      type="button"
                      onClick={() => {
                        setSelectedImage(url);
                        setCoverSaved(false);
                      }}
                      style={{
                        aspectRatio: "2 / 3",
                        background: sv.bg0,
                        border: `2px solid ${selected ? sv.cyan : sv.line}`,
                        boxShadow: selected ? `0 0 12px ${sv.cyan}55` : "none",
                        padding: 0,
                        overflow: "hidden",
                        cursor: "pointer",
                        transition: "border-color 120ms, box-shadow 120ms",
                      }}
                    >
                      <img src={url} alt="Cover" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    </button>
                  );
                })}
              </div>
              {selectedImage && (
                <SvActionButton tone="cyan" onClick={handleFetchCover} disabled={coverSaving || coverSaved}>
                  {coverSaving ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : coverSaved ? (
                    <Check size={12} />
                  ) : (
                    <Image size={12} />
                  )}
                  {coverSaved ? "Saved" : "Fetch cover"}
                </SvActionButton>
              )}
            </div>
          )}

          {currentStepId === "extras" && (
            <div>
              <SvLabel>Annotate extras</SvLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
                {extras.map((t) => (
                  <div
                    key={t.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      padding: "10px 12px",
                      background: sv.bg1,
                      border: `1px solid ${sv.line}`,
                    }}
                  >
                    <div style={{ flexShrink: 0 }}>
                      <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint }}>
                        #{t.title_index}
                      </span>
                      <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint, marginLeft: 8 }}>
                        {formatDuration(t.duration_seconds)}
                      </span>
                    </div>
                    <input
                      type="text"
                      value={extraDescriptions[t.id] || ""}
                      onChange={(e) =>
                        setExtraDescriptions((prev) => ({ ...prev, [t.id]: e.target.value }))
                      }
                      placeholder="Describe this extra…"
                      style={{ ...inputStyle, flex: 1 }}
                      onFocus={focusInput}
                      onBlur={blurInput}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {currentStepId === "save" && (
            <div>
              <SvLabel>Save enhancement</SvLabel>
              <SvPanel pad={12} style={{ marginTop: 12, marginBottom: 12 }}>
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
                  Summary
                </p>
                <div
                  style={{
                    marginTop: 8,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    fontFamily: sv.mono,
                    fontSize: 12,
                    color: sv.ink,
                  }}
                >
                  {upc.trim() && <p style={{ margin: 0 }}>UPC: {upc.trim()}</p>}
                  {selectedAsin && <p style={{ margin: 0 }}>ASIN: {selectedAsin}</p>}
                  {coverSaved && <p style={{ margin: 0 }}>Cover art saved</p>}
                  {Object.values(extraDescriptions).filter((v) => v.trim()).length > 0 && (
                    <p style={{ margin: 0 }}>
                      {Object.values(extraDescriptions).filter((v) => v.trim()).length} extra(s) annotated
                    </p>
                  )}
                  {!upc.trim() &&
                    !selectedAsin &&
                    !coverSaved &&
                    Object.values(extraDescriptions).filter((v) => v.trim()).length === 0 && (
                      <p style={{ margin: 0, color: sv.inkFaint }}>No enhancements to save</p>
                    )}
                </div>
              </SvPanel>

              {saveError && (
                <div style={{ marginBottom: 12 }}>
                  <SvNotice tone="error">{saveError}</SvNotice>
                </div>
              )}

              <SvActionButton tone="cyan" size="lg" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                Save enhancement
              </SvActionButton>
            </div>
          )}
        </motion.div>
      </AnimatePresence>

      {/* Navigation */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 24,
          paddingTop: 16,
          borderTop: `1px solid ${sv.line}`,
        }}
      >
        <div>
          {currentIdx > 0 ? (
            <SvActionButton tone="neutral" onClick={goBack}>
              <ChevronLeft size={12} /> Back
            </SvActionButton>
          ) : (
            <SvActionButton tone="neutral" onClick={onCancel}>Cancel</SvActionButton>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {currentStepId === "upc" && (
            <SvActionButton tone="neutral" onClick={goNext}>Skip lookup</SvActionButton>
          )}
          {currentStepId !== "save" && (
            <SvActionButton tone="magenta" onClick={goNext}>
              Next <ChevronRight size={12} />
            </SvActionButton>
          )}
        </div>
      </div>
    </SvPanel>
  );
}

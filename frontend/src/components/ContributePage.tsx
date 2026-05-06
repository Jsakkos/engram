import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { Package, ExternalLink } from "lucide-react";
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SvAtmosphere,
  SvLabel,
  SvNotice,
  SvPageHeader,
  SvPanel,
  sv,
} from "../app/components/synapse";
import { DeckCard, type DeckDragData, type DeckDropData } from "./contribute/DeckCard";
import { FannedDeck, type FannedDiscDragData } from "./contribute/FannedDeck";
import { EnhancePanel } from "./contribute/EnhancePanel";
import type { ContribConfig, Deck } from "./contribute/types";

interface ContributionStats {
  pending: number;
  exported: number;
  skipped: number;
  submitted: number;
}

export default function ContributePage() {
  const navigate = useNavigate();
  const [decks, setDecks] = useState<Deck[]>([]);
  const [stats, setStats] = useState<ContributionStats>({
    pending: 0,
    exported: 0,
    skipped: 0,
    submitted: 0,
  });
  const [config, setConfig] = useState<ContribConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submittingGroup, setSubmittingGroup] = useState<string | null>(null);
  const [groupResult, setGroupResult] = useState<{
    submitted: number;
    failed: number;
    contribute_url?: string | null;
    error?: string;
  } | null>(null);
  const [fannedDeckId, setFannedDeckId] = useState<string | null>(null);
  const [enhanceDeckId, setEnhanceDeckId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [decksRes, statsRes, configRes] = await Promise.all([
        fetch("/api/contributions/decks"),
        fetch("/api/contributions/stats"),
        fetch("/api/config"),
      ]);
      if (decksRes.ok) setDecks(await decksRes.json());
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

  // For solo decks (single disc, synthetic id "solo-<job_id>"), Submit hits
  // the per-job endpoint; for real groups, it hits the group endpoint.
  const handleSubmit = async (deck: Deck) => {
    setSubmittingGroup(deck.release_group_id);
    setGroupResult(null);
    try {
      // Pending discs in the group need to be exported first.
      const pending = deck.discs.filter((d) => d.export_status === "pending");
      for (const p of pending) {
        await fetch(`/api/contributions/${p.job_id}/export`, { method: "POST" });
      }

      if (deck.is_solo) {
        const job_id = deck.discs[0]?.job_id;
        const res = await fetch(`/api/contributions/${job_id}/submit`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          showError(data.detail || data.error || "Submission failed");
        } else {
          setGroupResult({
            submitted: 1,
            failed: 0,
            contribute_url: data.contribute_url ?? null,
          });
        }
      } else {
        const res = await fetch(
          `/api/contributions/release-group/${deck.release_group_id}/submit`,
          { method: "POST" },
        );
        const data = await res.json();
        if (!res.ok) {
          setGroupResult({
            submitted: 0,
            failed: 0,
            error: data.detail || "Submission failed",
          });
        } else {
          setGroupResult(data);
        }
      }
      await fetchData();
    } catch (e) {
      showError(e instanceof Error ? e.message : "Network error during submission");
    } finally {
      setSubmittingGroup(null);
    }
  };

  const handleUngroup = async (jobId: number) => {
    try {
      const res = await fetch(`/api/contributions/${jobId}/release-group`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ release_group_id: null }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || "Failed to ungroup");
      }
      await fetchData();
    } catch {
      showError("Network error");
    }
  };

  /**
   * @dnd-kit drag-end handler.
   *
   * Three legal gestures:
   *   1. Solo deck → multi deck: assign solo's job to that release_group_id.
   *   2. Solo deck → other solo deck: mint a shared release_group_id via the
   *      existing /contributions/release-group endpoint (POST {job_ids}).
   *   3. Fanned-out disc → outside any deck-target droppable: ungroup that disc.
   *
   * Anything else is a no-op.
   */
  const handleDragEnd = async (event: DragEndEvent) => {
    const active = event.active.data.current as DeckDragData | FannedDiscDragData | undefined;
    const over = event.over?.data.current as DeckDropData | undefined;

    if (!active) return;

    // Fanned disc dropped on nothing (or on its own deck) → ungroup
    if (active.kind === "fanned-disc") {
      if (!over) {
        await handleUngroup(active.jobId);
        return;
      }
      // Dropped on a different deck → reassign to that deck's release group
      if (over.releaseGroupId !== active.sourceReleaseGroupId && !over.isSolo) {
        await assignJobToGroup(active.jobId, over.releaseGroupId);
      }
      return;
    }

    // Solo deck dragged
    if (active.kind === "solo-deck") {
      if (!over) return;
      if (over.releaseGroupId === active.releaseGroupId) return;
      if (over.isSolo) {
        // Solo + solo → new release group
        await createReleaseGroup([active.jobId, ...over.jobIds]);
      } else {
        // Solo → existing multi deck → assign to that group
        await assignJobToGroup(active.jobId, over.releaseGroupId);
      }
    }
  };

  const assignJobToGroup = async (jobId: number, releaseGroupId: string) => {
    try {
      const res = await fetch(`/api/contributions/${jobId}/release-group`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ release_group_id: releaseGroupId }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || "Failed to assign to deck");
      }
      await fetchData();
    } catch {
      showError("Network error during deck assignment");
    }
  };

  const createReleaseGroup = async (jobIds: number[]) => {
    try {
      const res = await fetch(`/api/contributions/release-group`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: jobIds }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        showError(data.detail || "Failed to create deck");
      }
      await fetchData();
    } catch {
      showError("Network error creating deck");
    }
  };

  const toggleFanOut = (releaseGroupId: string) => {
    setEnhanceDeckId(null);
    setFannedDeckId((curr) => (curr === releaseGroupId ? null : releaseGroupId));
  };

  const toggleEnhance = (releaseGroupId: string) => {
    setFannedDeckId(null);
    setEnhanceDeckId((curr) => (curr === releaseGroupId ? null : releaseGroupId));
  };

  // 5px activation distance prevents click-throughs from registering as drags.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  if (loading) {
    return (
      <SvAtmosphere>
        <SvPageHeader title="Contribute to TheDiscDB" onBack={() => navigate("/")} />
        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "64px 24px", textAlign: "center" }}>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 11,
              letterSpacing: "0.18em",
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

  if (config && !config.discdb_contributions_enabled) {
    return (
      <SvAtmosphere>
        <SvPageHeader title="Contribute to TheDiscDB" onBack={() => navigate("/")} />
        <div style={{ maxWidth: 720, margin: "0 auto", padding: "64px 24px" }}>
          <SvPanel pad={32}>
            <div
              style={{
                textAlign: "center",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 16,
              }}
            >
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

  return (
    <SvAtmosphere>
      <SvPageHeader
        title="Contribute to TheDiscDB"
        subtitle="› Auto-grouped releases. Submit a deck, optionally enhance with UPC + cover."
        onBack={() => navigate("/")}
      />

      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "24px 24px 80px" }}>
        {/* Stats row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 12,
            marginBottom: 24,
          }}
        >
          <StatCard label="Pending" value={stats.pending} accent={sv.amber} />
          <StatCard label="Exported" value={stats.exported} accent={sv.green} />
          <StatCard label="Submitted" value={stats.submitted} accent={sv.cyan} />
          <StatCard label="Skipped" value={stats.skipped} accent={sv.inkDim} />
        </div>

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
                  ? `Submit failed: ${groupResult.error}`
                  : `Submit complete: ${groupResult.submitted} submitted, ${groupResult.failed} failed.`}
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
              No TheDiscDB API key configured. You can export locally, but submission requires an
              API key. Click the gear icon in the header, then go to TheDiscDB Contributions.
            </SvNotice>
          </div>
        )}

        {decks.length === 0 ? (
          <SvPanel pad={48}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
              }}
            >
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
          <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 24,
              }}
            >
              <AnimatePresence mode="popLayout">
                {decks.map((deck) => (
                <motion.div
                  key={deck.release_group_id}
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                >
                  <DeckCard
                    deck={deck}
                    apiKeySet={config?.discdb_api_key_set ?? false}
                    onSubmit={() => handleSubmit(deck)}
                    onEnhance={toggleEnhance}
                    onFanOut={toggleFanOut}
                    submitting={submittingGroup === deck.release_group_id}
                    enhanceOpen={enhanceDeckId === deck.release_group_id}
                  />
                  <AnimatePresence>
                    {fannedDeckId === deck.release_group_id && (
                      <FannedDeck
                        deck={deck}
                        onClose={() => setFannedDeckId(null)}
                        onUngroup={handleUngroup}
                      />
                    )}
                    {enhanceDeckId === deck.release_group_id && (
                      <EnhancePanel
                        deck={deck}
                        onClose={() => setEnhanceDeckId(null)}
                        onSaved={() => {
                          setEnhanceDeckId(null);
                          fetchData();
                        }}
                      />
                    )}
                  </AnimatePresence>
                </motion.div>
              ))}
              </AnimatePresence>
            </div>
          </DndContext>
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

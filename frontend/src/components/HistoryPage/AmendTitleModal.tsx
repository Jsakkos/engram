import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import { SvPanel, SvLabel, sv } from "../../app/components/synapse";
import type { AmendKind } from "../../api/client";

export interface AmendTitleModalProps {
  title: { id: number; matchedEpisode: string | null; titleIndex: number };
  /** The disc/job's season — drives the episode codes, NOT the (possibly wrong) matched episode. */
  season: number;
  seasonEpisodes: number[];
  hasUploadedFingerprint?: boolean;
  onSubmit: (target: { kind: AmendKind; episode_code?: string }) => Promise<void>;
  onClose: () => void;
}

export function AmendTitleModal({
  title,
  season,
  seasonEpisodes,
  hasUploadedFingerprint,
  onSubmit,
  onClose,
}: AmendTitleModalProps) {
  // The modal mounts fresh each time it's opened (the parent renders it only when
  // a track is selected), so state starts at these defaults — no reset effect needed.
  const [kind, setKind] = useState<AmendKind>("episode");
  const [episode, setEpisode] = useState<number | null>(seasonEpisodes[0] ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClose = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);

  // Escape key handler (active for the modal's whole mounted lifetime)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [handleClose]);

  const seasonCode = String(season).padStart(2, "0");

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      if (kind === "episode") {
        if (episode == null) throw new Error("Pick an episode");
        await onSubmit({
          kind: "episode",
          episode_code: `S${seasonCode}E${String(episode).padStart(2, "0")}`,
        });
      } else {
        await onSubmit({ kind });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Amend failed");
    } finally {
      setBusy(false);
    }
  }

  const kindButtonStyle = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "8px 12px",
    fontFamily: sv.mono,
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.14em",
    textTransform: "uppercase",
    color: active ? sv.cyanHi : sv.inkDim,
    border: `1px solid ${active ? sv.cyan : sv.lineMid}`,
    background: active ? `${sv.cyan}14` : "transparent",
    boxShadow: active ? `0 0 10px ${sv.cyan}33, inset 0 0 6px ${sv.cyan}0d` : "none",
    cursor: "pointer",
    transition: "all 0.15s",
  });

  const trackLabel = `t${String(title.titleIndex).padStart(2, "0")}`;

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          role="dialog"
          aria-modal="true"
          aria-label="Reassign track"
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0"
            style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            onClick={handleClose}
          />

          {/* Card */}
          <motion.div
            className="relative w-full max-w-md"
            initial={{ opacity: 0, scale: 0.94, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.94, y: 16 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
          >
            <SvPanel
              glow
              pad={0}
              style={{
                background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                boxShadow: `0 0 40px ${sv.cyan}22, 0 0 80px ${sv.cyan}0d, inset 0 0 30px ${sv.cyan}08`,
              }}
            >
              <div
                style={{ padding: 24, display: "flex", flexDirection: "column", gap: 20 }}
              >
                {/* Header */}
                <div>
                  <h3
                    style={{
                      fontFamily: sv.display,
                      fontWeight: 700,
                      fontSize: 16,
                      letterSpacing: "0.18em",
                      textTransform: "uppercase",
                      color: sv.cyanHi,
                      textShadow: `0 0 8px ${sv.cyan}88`,
                      margin: 0,
                    }}
                  >
                    Reassign track {trackLabel}
                  </h3>
                  <motion.div
                    style={{
                      height: 1,
                      marginTop: 6,
                      background: `linear-gradient(90deg, ${sv.cyan}99, transparent)`,
                    }}
                    initial={{ scaleX: 0, originX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ delay: 0.1, duration: 0.3 }}
                  />
                  {title.matchedEpisode && (
                    <p
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        color: sv.inkDim,
                        letterSpacing: "0.1em",
                        margin: "8px 0 0",
                      }}
                    >
                      Currently assigned:{" "}
                      <span style={{ color: sv.cyan }}>{title.matchedEpisode}</span>
                    </p>
                  )}
                </div>

                {/* Kind selector */}
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <SvLabel size={10}>New assignment</SvLabel>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button
                      type="button"
                      aria-pressed={kind === "episode"}
                      onClick={() => setKind("episode")}
                      style={kindButtonStyle(kind === "episode")}
                    >
                      Episode
                    </button>
                    <button
                      type="button"
                      aria-pressed={kind === "extra"}
                      onClick={() => setKind("extra")}
                      style={kindButtonStyle(kind === "extra")}
                    >
                      Mark as Extra
                    </button>
                    <button
                      type="button"
                      aria-pressed={kind === "discard"}
                      onClick={() => setKind("discard")}
                      style={kindButtonStyle(kind === "discard")}
                    >
                      Discard
                    </button>
                  </div>
                </div>

                {/* Episode picker — only shown when kind === "episode" */}
                <AnimatePresence>
                  {kind === "episode" && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ type: "spring", stiffness: 400, damping: 35 }}
                      style={{ overflow: "hidden" }}
                    >
                      <div
                        style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 2 }}
                      >
                        <SvLabel size={10}>Episode</SvLabel>
                        <select
                          aria-label="Episode"
                          value={episode ?? ""}
                          onChange={(e) => setEpisode(Number(e.target.value))}
                          style={{
                            background: sv.bg1,
                            border: `1px solid ${sv.lineMid}`,
                            color: sv.cyanHi,
                            fontFamily: sv.mono,
                            fontSize: 12,
                            padding: "8px 10px",
                            outline: "none",
                            cursor: "pointer",
                            width: "100%",
                          }}
                        >
                          {seasonEpisodes.map((ep) => (
                            <option key={ep} value={ep}>
                              S{seasonCode}E{String(ep).padStart(2, "0")} — Episode {ep}
                            </option>
                          ))}
                        </select>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Fingerprint notice */}
                {hasUploadedFingerprint && (
                  <div
                    style={{
                      padding: "10px 12px",
                      border: `1px solid ${sv.amber}4d`,
                      background: `${sv.amber}0d`,
                      fontFamily: sv.mono,
                      fontSize: 11,
                      lineHeight: 1.5,
                      color: `${sv.amber}cc`,
                    }}
                  >
                    This will retract the previous fingerprint from the shared network and
                    submit your correction.
                  </div>
                )}

                {/* Error */}
                {error && (
                  <div
                    role="alert"
                    style={{
                      padding: "8px 12px",
                      border: `1px solid ${sv.red}4d`,
                      background: `${sv.red}0d`,
                      fontFamily: sv.mono,
                      fontSize: 11,
                      color: sv.red,
                    }}
                  >
                    {error}
                  </div>
                )}

                {/* Action buttons */}
                <div
                  style={{ display: "flex", gap: 8, borderTop: `1px solid ${sv.line}`, paddingTop: 16 }}
                >
                  <button
                    type="button"
                    onClick={handleClose}
                    disabled={busy}
                    style={{
                      flex: 1,
                      padding: "10px 16px",
                      fontFamily: sv.mono,
                      fontSize: 11,
                      fontWeight: 700,
                      letterSpacing: "0.16em",
                      textTransform: "uppercase",
                      color: sv.inkDim,
                      border: `1px solid ${sv.lineMid}`,
                      background: "transparent",
                      cursor: busy ? "not-allowed" : "pointer",
                      opacity: busy ? 0.5 : 1,
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={apply}
                    disabled={busy}
                    style={{
                      flex: 1,
                      padding: "10px 16px",
                      fontFamily: sv.mono,
                      fontSize: 11,
                      fontWeight: 700,
                      letterSpacing: "0.16em",
                      textTransform: "uppercase",
                      color: busy ? `${sv.cyan}4d` : sv.cyan,
                      border: `1px solid ${busy ? `${sv.cyan}33` : sv.cyan}`,
                      background: busy ? "transparent" : `${sv.cyan}1f`,
                      boxShadow: busy ? "none" : `0 0 14px ${sv.cyan}33, inset 0 0 8px ${sv.cyan}0d`,
                      cursor: busy ? "not-allowed" : "pointer",
                      opacity: busy ? 0.6 : 1,
                    }}
                  >
                    {busy ? "Applying…" : "Apply"}
                  </button>
                </div>
              </div>
            </SvPanel>
          </motion.div>
        </motion.div>
  );
}

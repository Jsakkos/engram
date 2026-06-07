/**
 * UpdateModal — release notes modal opened from UpdateBanner's "What's new" button.
 *
 * Follows the BugReportModal overlay pattern.
 */

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ArrowUp, ExternalLink, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { toast } from "sonner";
import { SvPanel, sv } from "../app/components/synapse";
import { apiFetchVoid, ApiError } from "../api/client";
import type { UpdateStatus } from "../types";

interface UpdateModalProps {
    open: boolean;
    updateStatus: UpdateStatus | null;
    onClose: () => void;
    onDismiss: () => void;
}

export default function UpdateModal({
    open,
    updateStatus,
    onClose,
    onDismiss,
}: UpdateModalProps) {
    const [restarting, setRestarting] = useState(false);

    useEffect(() => {
        if (!open) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [open, onClose]);

    const handleRestart = useCallback(async () => {
        setRestarting(true);
        try {
            await apiFetchVoid("/api/updates/restart", { method: "POST" });
            onClose();
            toast.info("Restarting to apply update…");
        } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
                toast.error("A disc operation is in progress. Please wait before restarting.");
            } else if (err instanceof ApiError && err.status === 400) {
                toast.error("Updates cannot be applied in dev mode.");
            } else {
                toast.error(
                    `Restart failed. Download manually from GitHub: ${updateStatus?.release_url ?? ""}`,
                );
            }
            setRestarting(false);
        }
    }, [onClose, updateStatus]);

    const handleSkip = useCallback(async () => {
        if (!updateStatus?.latest_version) return;
        try {
            await apiFetchVoid("/api/updates/skip", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: updateStatus.latest_version }),
            });
            onClose();
            onDismiss();
        } catch {
            toast.error("Failed to save skip preference.");
        }
    }, [updateStatus, onClose, onDismiss]);

    const isFrozen = updateStatus?.is_frozen ?? false;

    const buttonBase: CSSProperties = {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "10px 16px",
        fontFamily: sv.mono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.18em",
        textTransform: "uppercase",
        cursor: "pointer",
        transition: "all 0.18s",
        border: "none",
    };

    return (
        <AnimatePresence>
            {open && (
                <motion.div
                    className="fixed inset-0 z-50 flex items-center justify-center p-4"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="update-modal-title"
                >
                    <motion.div
                        className="absolute inset-0"
                        style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        onClick={onClose}
                    />

                    <motion.div
                        className="relative w-full max-w-2xl"
                        initial={{ opacity: 0, scale: 0.94, y: 20 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.94, y: 20 }}
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                    >
                        <SvPanel
                            glow
                            pad={0}
                            style={{
                                background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                                boxShadow: `0 0 40px ${sv.cyan}26, inset 0 0 30px ${sv.cyan}0a`,
                                maxHeight: "85vh",
                                display: "flex",
                                flexDirection: "column",
                            }}
                            data-testid="update-modal"
                        >
                            {/* Header */}
                            <div
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 12,
                                    padding: "20px 24px",
                                    borderBottom: `1px solid ${sv.line}`,
                                }}
                            >
                                <ArrowUp
                                    size={20}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}99)` }}
                                />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <h2
                                        id="update-modal-title"
                                        style={{
                                            fontFamily: sv.display,
                                            fontWeight: 700,
                                            fontSize: 16,
                                            letterSpacing: "0.2em",
                                            textTransform: "uppercase",
                                            color: sv.cyanHi,
                                            margin: 0,
                                        }}
                                    >
                                        What's new in {updateStatus?.latest_version ?? "…"}
                                    </h2>
                                </div>
                                <button
                                    type="button"
                                    onClick={onClose}
                                    aria-label="Close"
                                    style={{
                                        color: sv.inkFaint,
                                        background: "transparent",
                                        border: "none",
                                        cursor: "pointer",
                                        padding: 4,
                                        display: "flex",
                                    }}
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            {/* Release notes body */}
                            <div
                                style={{
                                    flex: 1,
                                    overflowY: "auto",
                                    padding: "20px 24px",
                                }}
                            >
                                {updateStatus?.release_notes ? (
                                    <div
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 13,
                                            color: sv.ink,
                                            lineHeight: 1.65,
                                        }}
                                    >
                                        <ReactMarkdown>{updateStatus.release_notes}</ReactMarkdown>
                                    </div>
                                ) : (
                                    <p
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 12,
                                            color: sv.inkDim,
                                        }}
                                    >
                                        No release notes available.
                                    </p>
                                )}
                                {updateStatus?.release_url && (
                                    <a
                                        href={updateStatus.release_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        style={{
                                            display: "inline-flex",
                                            alignItems: "center",
                                            gap: 6,
                                            marginTop: 16,
                                            fontFamily: sv.mono,
                                            fontSize: 11,
                                            letterSpacing: "0.1em",
                                            color: sv.cyanHi,
                                            textDecoration: "none",
                                            textTransform: "uppercase",
                                        }}
                                    >
                                        <ExternalLink size={12} />
                                        View on GitHub
                                    </a>
                                )}
                            </div>

                            {/* Footer */}
                            <div
                                style={{
                                    display: "flex",
                                    justifyContent: "space-between",
                                    alignItems: "center",
                                    padding: "16px 24px",
                                    borderTop: `1px solid ${sv.line}`,
                                    gap: 12,
                                }}
                            >
                                <button
                                    type="button"
                                    onClick={handleSkip}
                                    style={{
                                        ...buttonBase,
                                        color: sv.inkDim,
                                        background: "transparent",
                                        border: `1px solid ${sv.line}`,
                                    }}
                                >
                                    Skip this version
                                </button>

                                {isFrozen ? (
                                    <button
                                        type="button"
                                        onClick={handleRestart}
                                        disabled={restarting}
                                        style={{
                                            ...buttonBase,
                                            color: sv.bg0,
                                            background: restarting ? `${sv.cyan}99` : sv.cyan,
                                            opacity: restarting ? 0.8 : 1,
                                        }}
                                    >
                                        {restarting ? "Restarting…" : "Restart to update →"}
                                    </button>
                                ) : (
                                    <a
                                        href={updateStatus?.release_url ?? "#"}
                                        target="_blank"
                                        rel="noreferrer"
                                        style={{
                                            ...buttonBase,
                                            color: sv.bg0,
                                            background: sv.cyan,
                                            textDecoration: "none",
                                        }}
                                    >
                                        Download from GitHub →
                                    </a>
                                )}
                            </div>
                        </SvPanel>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}

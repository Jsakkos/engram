/**
 * UpdateBanner — slim top-of-page notification shown when a new version is staged.
 *
 * Visible only when updateStatus.state === 'ready'.
 * In dev mode (is_frozen = false) the "Restart now" button is hidden.
 */

import { useState } from "react";
import { ArrowUp, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { sv } from "./synapse";
import { apiFetchVoid, ApiError } from "../../api/client";
import type { UpdateStatus } from "../../types";

interface UpdateBannerProps {
    updateStatus: UpdateStatus | null;
    onShowNotes: () => void;
    onDismiss: () => void;
    onRestart?: () => void;
}

export function UpdateBanner({ updateStatus, onShowNotes, onDismiss, onRestart }: UpdateBannerProps) {
    const [restarting, setRestarting] = useState(false);
    const [failureDismissed, setFailureDismissed] = useState(false);

    const showReady = !!updateStatus && updateStatus.state === "ready";
    const showFailure = !!updateStatus?.last_update_error && !failureDismissed;
    if (!showReady && !showFailure) return null;

    const isFrozen = updateStatus.is_frozen;

    const handleRestart = async () => {
        onRestart?.();
        setRestarting(true);
        try {
            await apiFetchVoid("/api/updates/restart", { method: "POST" });
            toast.info("Restarting to apply update…");
        } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
                toast.error("A disc operation is in progress. Please wait before restarting.");
            } else if (err instanceof ApiError && err.status === 400) {
                toast.error("Updates cannot be applied in dev mode.");
            } else {
                toast.error(
                    `Restart failed. Download manually from GitHub: ${updateStatus.release_url ?? ""}`,
                );
            }
            setRestarting(false);
        }
    };

    const handleSkip = async () => {
        if (!updateStatus.latest_version) return;
        try {
            await apiFetchVoid("/api/updates/skip", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: updateStatus.latest_version }),
            });
            onDismiss();
        } catch {
            toast.error("Failed to save skip preference.");
        }
    };

    if (showFailure && !showReady) {
        return (
            <div
                data-testid="update-failure-banner"
                style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 28px",
                    background: `${sv.magenta}10`, borderBottom: `1px solid ${sv.magenta}55`,
                    fontFamily: sv.mono, fontSize: 12, letterSpacing: "0.06em", color: sv.magenta,
                }}
            >
                <X size={14} color={sv.magenta} style={{ flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{updateStatus!.last_update_error}</span>
                {updateStatus!.release_url && (
                    <a
                        href={updateStatus!.release_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: sv.magenta, textTransform: "uppercase", fontSize: 10, letterSpacing: "0.14em" }}
                    >
                        Download manually
                    </a>
                )}
                <button
                    type="button"
                    onClick={() => setFailureDismissed(true)}
                    style={{ color: sv.inkDim, background: "transparent", border: "none", cursor: "pointer" }}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    return (
        <div
            data-testid="update-banner"
            style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 28px",
                background: `${sv.cyan}10`,
                borderBottom: `1px solid ${sv.cyan}55`,
                boxShadow: `0 0 12px ${sv.cyan}22`,
                fontFamily: sv.mono,
                fontSize: 12,
                letterSpacing: "0.06em",
                color: sv.cyanHi,
            }}
        >
            <ArrowUp size={14} color={sv.cyan} style={{ flexShrink: 0 }} />
            <span style={{ flex: 1 }}>
                engram {updateStatus.latest_version} is ready to install
                {!isFrozen && (
                    <span style={{ color: sv.inkDim }}> — dev mode, manual download required</span>
                )}
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                    type="button"
                    onClick={onShowNotes}
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        letterSpacing: "0.14em",
                        textTransform: "uppercase",
                        color: sv.cyanHi,
                        background: "transparent",
                        border: `1px solid ${sv.cyan}55`,
                        padding: "4px 10px",
                        cursor: "pointer",
                    }}
                >
                    What's new
                </button>

                {isFrozen && (
                    <button
                        type="button"
                        onClick={handleRestart}
                        disabled={restarting}
                        style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            letterSpacing: "0.14em",
                            textTransform: "uppercase",
                            color: sv.bg0,
                            background: restarting ? `${sv.cyan}99` : sv.cyan,
                            border: "none",
                            padding: "4px 10px",
                            cursor: restarting ? "wait" : "pointer",
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 6,
                        }}
                    >
                        {restarting && <RefreshCw size={10} />}
                        {restarting ? "Restarting…" : "Restart now"}
                    </button>
                )}

                <button
                    type="button"
                    onClick={handleSkip}
                    title="Skip this version"
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        color: sv.inkDim,
                        background: "transparent",
                        border: "none",
                        padding: "4px 6px",
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                    }}
                >
                    <X size={11} />
                    Skip
                </button>
            </div>
        </div>
    );
}

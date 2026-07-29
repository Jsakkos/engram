/**
 * UpdateModal — release notes modal opened from UpdateBanner's "What's new" button.
 *
 * Follows the BugReportModal overlay pattern.
 */

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ArrowUp, ExternalLink, Sparkles, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { toast } from "sonner";
import { SvPanel, sv } from "../app/components/synapse";
import { apiFetchVoid, ApiError } from "../api/client";
import type { UpdateStatus } from "../types";

// Tailwind's preflight zeroes margins on p/ul/li and strips heading sizes, list
// markers and paragraph rhythm, so raw markdown release notes render as one
// undifferentiated wall of text. A scoped stylesheet is used instead of a
// per-element component map because the rules that do the real work here are
// structural selectors (:first-of-type for the highlights lede, li > strong for
// the entry headline, ::before for markers) that inline styles cannot express.
//
// Changelog convention (see CLAUDE.md) is "**headline sentence.** explanation",
// so `li > strong:first-child` is promoted to its own line: every bullet becomes
// a title/body pair rather than a paragraph of uniformly-bold mono.
const PROSE_CLASS = "um-prose";

const proseCss = `
.${PROSE_CLASS} {
    font-family: ${sv.sans};
    font-size: 13.5px;
    line-height: 1.7;
    /* Body prose is the primary content here, so inkDim (the app's secondary
       ink) reads as disabled at this size. Sit between ink and inkDim: bright
       enough to read at length, dim enough that <strong> at full ink still
       steps forward. */
    color: rgba(230, 236, 245, 0.78);
    /* Cap the measure. The panel is wider than comfortable reading length,
       and long unbroken lines are half of why this read as a wall. */
    max-width: 68ch;
}
.${PROSE_CLASS} > *:first-child { margin-top: 0; }
.${PROSE_CLASS} > *:last-child { margin-bottom: 0; }

/* Release bodies open with an italic "_Highlights: …_" line. Treat it as a
   lede: a bracketed summary panel rather than a stray italic paragraph. */
.${PROSE_CLASS} > p:first-of-type {
    font-style: normal;
    color: ${sv.ink};
    background: ${sv.cyan}0d;
    border-left: 2px solid ${sv.cyan};
    padding: 12px 14px;
    margin: 0 0 22px;
}
.${PROSE_CLASS} > p:first-of-type em { font-style: normal; }

.${PROSE_CLASS} p { margin: 0 0 14px; }

/* Section rules ("Added" / "Fixed"): mono + letterspacing is the Synapse
   label voice, and the hairline extends them across the column so they read
   as dividers when scanning. */
.${PROSE_CLASS} h1,
.${PROSE_CLASS} h2,
.${PROSE_CLASS} h3,
.${PROSE_CLASS} h4 {
    font-family: ${sv.mono};
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: ${sv.cyan};
    font-size: 11px;
    line-height: 1;
    margin: 30px 0 14px;
    padding-bottom: 10px;
    border-bottom: 1px solid ${sv.line};
}
.${PROSE_CLASS} h1 { font-size: 12px; color: ${sv.cyanHi}; }

.${PROSE_CLASS} ul,
.${PROSE_CLASS} ol { margin: 0 0 14px; padding: 0; list-style: none; }

/* 18px between entries is the "not smashed together" fix; each bullet is a
   multi-line block, so it needs more separation than its own line-height. */
.${PROSE_CLASS} li {
    position: relative;
    padding-left: 20px;
    margin-bottom: 18px;
}
.${PROSE_CLASS} li:last-child { margin-bottom: 0; }
.${PROSE_CLASS} li::before {
    content: "";
    position: absolute;
    left: 2px;
    top: 0.62em;
    width: 6px;
    height: 6px;
    background: ${sv.cyan};
    box-shadow: 0 0 6px ${sv.cyan}99;
}

/* The emphasis fix: bold at full ink on a dim body is a real figure/ground
   step, where bolder-mono-on-ink was not. */
.${PROSE_CLASS} strong {
    font-weight: 600;
    color: ${sv.ink};
}
.${PROSE_CLASS} li > strong:first-child {
    display: block;
    color: ${sv.cyanHi};
    margin-bottom: 3px;
}
.${PROSE_CLASS} em { color: ${sv.ink}; }

.${PROSE_CLASS} code {
    font-family: ${sv.mono};
    font-size: 0.86em;
    color: ${sv.cyanHi};
    background: ${sv.cyan}14;
    padding: 1px 5px;
}
.${PROSE_CLASS} a {
    color: ${sv.cyanHi};
    text-decoration: underline;
    text-underline-offset: 2px;
}
.${PROSE_CLASS} hr {
    border: 0;
    border-top: 1px solid ${sv.line};
    margin: 22px 0;
}
`;

interface UpdateModalProps {
    open: boolean;
    updateStatus: UpdateStatus | null;
    onClose: () => void;
    onDismiss?: () => void;
    /**
     * "update" (default): notes for an available update, with skip and restart actions.
     * "whatsNew": notes for the version already running, read-only.
     */
    variant?: "update" | "whatsNew";
}

export default function UpdateModal({
    open,
    updateStatus,
    onClose,
    onDismiss,
    variant = "update",
}: UpdateModalProps) {
    const [restarting, setRestarting] = useState(false);

    const isWhatsNew = variant === "whatsNew";
    const version = isWhatsNew ? updateStatus?.current_version : updateStatus?.latest_version;
    const notes = isWhatsNew ? updateStatus?.current_release_notes : updateStatus?.release_notes;
    const releaseUrl = isWhatsNew ? updateStatus?.current_release_url : updateStatus?.release_url;
    // Both variants can be mounted (and open) at once, so the dialog's labelling id
    // and test id must differ per variant or aria-labelledby resolves to whichever
    // heading comes first in DOM order and getByTestId matches two elements.
    const titleId = isWhatsNew ? "whats-new-modal-title" : "update-modal-title";
    const testId = isWhatsNew ? "whats-new-modal" : "update-modal";
    const HeaderIcon = isWhatsNew ? Sparkles : ArrowUp;

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
            onDismiss?.();
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
                    aria-labelledby={titleId}
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
                            testid={testId}
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
                                {/* The whatsNew variant describes the version already
                                    running, so an "update available" arrow would be wrong. */}
                                <HeaderIcon
                                    size={20}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}99)` }}
                                />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <h2
                                        id={titleId}
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
                                        What's new in {version ?? "…"}
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
                                {notes ? (
                                    <>
                                        {/* Outside the prose wrapper so it does not
                                            occupy the :first-child slot. */}
                                        <style>{proseCss}</style>
                                        <div className={PROSE_CLASS}>
                                            <ReactMarkdown>{notes}</ReactMarkdown>
                                        </div>
                                    </>
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
                                {releaseUrl && (
                                    <a
                                        href={releaseUrl}
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
                                {isWhatsNew ? (
                                    <button
                                        type="button"
                                        onClick={onClose}
                                        data-testid="whats-new-close"
                                        style={{
                                            ...buttonBase,
                                            marginLeft: "auto",
                                            color: sv.bg0,
                                            background: sv.cyan,
                                        }}
                                    >
                                        Got it
                                    </button>
                                ) : (
                                    <>
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
                                                href={releaseUrl ?? "#"}
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
                                    </>
                                )}
                            </div>
                        </SvPanel>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}

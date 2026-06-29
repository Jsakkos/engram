/**
 * Action buttons for the disc card (Cancel, Re-Identify, Review Needed).
 *
 * Synapse v2 vocabulary: monospace + uppercase labels, 1px tinted borders,
 * sharp 90° corners, sv-token glow on hover. All three buttons share a
 * common 30px height so they baseline-align with the StateIndicator pill
 * to the left.
 *
 * Visual hierarchy (intentional, not arbitrary):
 *   Cancel       — 30×30 square, icon-only, red    → quiet/destructive
 *   Re-Identify  — 30 tall, icon + 10px label, cyan → tertiary action
 *   Review       — 30 tall, icon + 11px label, yellow → primary callout
 *
 * Exception: when a disc needs review but hasn't ripped yet, the Review button
 * is suppressed (the review queue is empty), so Re-Identify ("Wrong title?")
 * becomes the only useful action — `emphasizeReIdentify` renders it filled to
 * promote it to the primary callout in that state.
 */

import { useState, useEffect, type CSSProperties, type ReactNode, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import { IcoCancel, IcoError, IcoRetry, IcoPlay } from "../icons";
import type { DiscState } from "../DiscCard";
import { sv, SvPanel } from "../synapse";

interface ConfirmModalProps {
    titleId: string;
    title: string;
    body: string;
    confirmLabel: string;
    dismissLabel: string;
    confirmTone?: "red" | "cyan";
    onConfirm: () => void;
    onDismiss: () => void;
}

function ConfirmModal({ titleId, title, body, confirmLabel, dismissLabel, confirmTone = "red", onConfirm, onDismiss }: ConfirmModalProps) {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onDismiss(); };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [onDismiss]);

    const confirmColor = confirmTone === "red" ? sv.red : sv.cyan;
    const confirmBg = confirmTone === "red" ? "rgba(255,85,85,0.12)" : "rgba(94,234,212,0.12)";

    return createPortal(
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
        >
            <div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
                onClick={onDismiss}
            />
            <motion.div
                className="relative w-full max-w-xs"
                initial={{ opacity: 0, scale: 0.92, y: 16 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 16 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
            >
                <SvPanel glow pad={24} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                    <div id={titleId} style={{ fontFamily: sv.mono, fontSize: 13, fontWeight: 700, letterSpacing: "0.2em", color: confirmColor, textTransform: "uppercase" }}>
                        {title}
                    </div>
                    <div style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim, lineHeight: 1.6 }}>
                        {body}
                    </div>
                    <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                        <button
                            onClick={onDismiss}
                            style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: "0.2em", textTransform: "uppercase", padding: "8px 14px", background: sv.bg0, border: `1px solid ${sv.lineMid}`, color: sv.inkDim, cursor: "pointer" }}
                        >
                            {dismissLabel}
                        </button>
                        <button
                            onClick={onConfirm}
                            style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: "0.2em", textTransform: "uppercase", padding: "8px 14px", background: confirmBg, border: `1px solid ${confirmColor}`, color: confirmColor, cursor: "pointer", boxShadow: `0 0 10px ${confirmColor}44` }}
                        >
                            {confirmLabel}
                        </button>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>,
        document.body
    );
}

interface ActionButtonsProps {
    state: DiscState;
    isHovered: boolean;
    onCancel?: () => void;
    onReview?: () => void;
    onReIdentify?: () => void;
    onAdvance?: () => void;
    /** Render the "Wrong title?" button filled — it's the primary action when
     *  the review queue is suppressed pre-rip. */
    emphasizeReIdentify?: boolean;
}

// States where Force-advance makes sense (job is actively processing).
const ACTIVE_STATES = ["scanning", "ripping", "matching", "organizing", "processing"];
// Cancel was historically shown only during rip-phase states; keep that scope so it
// doesn't surface during organizing, where cancelling could leave files partially moved.
const CANCELABLE_STATES = ["scanning", "ripping", "processing"];

interface Tone {
    fg: string;        // foreground / icon / text
    fgHi: string;      // hover-state foreground
    border: string;    // resting border (rgba)
    borderHi: string;  // hover border (rgba)
    glow: string;      // resting box-shadow color
    glowHi: string;    // hover box-shadow color
    bgHi: string;      // hover background tint
}

const RED: Tone = {
    fg: sv.red,
    fgHi: "#ff8a8a",
    border: `${sv.red}55`,
    borderHi: sv.red,
    glow: `${sv.red}33`,
    glowHi: `${sv.red}66`,
    bgHi: "rgba(255, 85, 85, 0.10)",
};

const CYAN: Tone = {
    fg: sv.cyan,
    fgHi: sv.cyanHi,
    border: `${sv.cyan}55`,
    borderHi: sv.cyan,
    glow: `${sv.cyan}33`,
    glowHi: `${sv.cyan}66`,
    bgHi: "rgba(94, 234, 212, 0.10)",
};

const YELLOW: Tone = {
    fg: sv.yellow,
    fgHi: sv.yellow,
    border: `${sv.yellow}99`,
    borderHi: sv.yellow,
    glow: `${sv.yellow}55`,
    glowHi: `${sv.yellow}99`,
    bgHi: "rgba(253, 224, 71, 0.12)",
};

const BUTTON_HEIGHT = 30;

function baseStyle(tone: Tone, hovered: boolean): CSSProperties {
    return {
        height: BUTTON_HEIGHT,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: hovered ? tone.bgHi : sv.bg0,
        border: `1px solid ${hovered ? tone.borderHi : tone.border}`,
        color: hovered ? tone.fgHi : tone.fg,
        boxShadow: `0 0 ${hovered ? 14 : 8}px ${hovered ? tone.glowHi : tone.glow}`,
        fontFamily: sv.mono,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.20em",
        cursor: "pointer",
        transition: "background 120ms ease-out, border-color 120ms ease-out, color 120ms ease-out, box-shadow 120ms ease-out",
    };
}

interface ToneButtonProps {
    tone: Tone;
    onClick: (e: MouseEvent) => void;
    title?: string;
    ariaLabel: string;
    children: ReactNode;
    /** Horizontal padding in px. 0 means render as a square icon-only button. */
    paddingX?: number;
    testId?: string;
    /** Render the resting state as if hovered (filled bg, stronger border + glow)
     *  to promote this button to the primary action. */
    emphasis?: boolean;
}

function ToneButton({ tone, onClick, title, ariaLabel, children, paddingX = 0, testId, emphasis = false }: ToneButtonProps) {
    const [hovered, setHovered] = useState(false);
    const isSquare = paddingX === 0;
    return (
        <motion.button
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.85 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            onClick={onClick}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onFocus={() => setHovered(true)}
            onBlur={() => setHovered(false)}
            title={title}
            aria-label={ariaLabel}
            data-testid={testId}
            style={{
                ...baseStyle(tone, hovered || emphasis),
                paddingLeft: paddingX,
                paddingRight: paddingX,
                width: isSquare ? BUTTON_HEIGHT : undefined,
                justifyContent: isSquare ? "center" : "flex-start",
            }}
        >
            {children}
        </motion.button>
    );
}

export function ActionButtons({ state, isHovered, onCancel, onReview, onReIdentify, onAdvance, emphasizeReIdentify = false }: ActionButtonsProps) {
    const showCancel = !!onCancel && (isHovered || CANCELABLE_STATES.includes(state));
    const showReview = !!onReview && state === "review_needed";
    const showAdvance = !!onAdvance && ACTIVE_STATES.includes(state);
    const [showCancelModal, setShowCancelModal] = useState(false);
    const [showAdvanceModal, setShowAdvanceModal] = useState(false);

    const handleCancel = (e: MouseEvent) => {
        e.stopPropagation();
        setShowCancelModal(true);
    };

    const handleAdvance = (e: MouseEvent) => {
        e.stopPropagation();
        setShowAdvanceModal(true);
    };

    return (
        <>
        <AnimatePresence>
            {showCancelModal && (
                <ConfirmModal
                    titleId="cancel-modal-title"
                    title="Cancel rip?"
                    body="Any files already ripped will be deleted from staging."
                    confirmLabel="Cancel rip"
                    dismissLabel="Keep ripping"
                    confirmTone="red"
                    onConfirm={() => { setShowCancelModal(false); onCancel!(); }}
                    onDismiss={() => setShowCancelModal(false)}
                />
            )}
            {showAdvanceModal && (
                <ConfirmModal
                    titleId="advance-modal-title"
                    title="Force advance?"
                    body={"Tracks still ripping or matching will be sent to review (or failed if no file was produced), and anything already matched will be organized."}
                    confirmLabel="Force advance"
                    dismissLabel="Keep going"
                    confirmTone="cyan"
                    onConfirm={() => { setShowAdvanceModal(false); onAdvance!(); }}
                    onDismiss={() => setShowAdvanceModal(false)}
                />
            )}
        </AnimatePresence>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {showCancel && (
                <ToneButton
                    tone={RED}
                    onClick={handleCancel}
                    title="Cancel job"
                    ariaLabel="Cancel job"
                    paddingX={0}
                >
                    <IcoCancel size={14} />
                </ToneButton>
            )}

            {showAdvance && (
                <ToneButton
                    tone={CYAN}
                    onClick={handleAdvance}
                    title="Force this job to its next step"
                    ariaLabel="Force job to next step"
                    paddingX={10}
                >
                    <IcoPlay size={12} />
                    <span style={{ fontSize: 10 }}>Force</span>
                </ToneButton>
            )}

            {onReIdentify && (
                <ToneButton
                    tone={CYAN}
                    onClick={(e) => { e.stopPropagation(); onReIdentify(); }}
                    title="Wrong title — re-identify disc"
                    ariaLabel="Wrong title — re-identify disc"
                    paddingX={10}
                    emphasis={emphasizeReIdentify}
                >
                    <IcoRetry size={12} />
                    <span style={{ fontSize: 10 }}>Wrong title?</span>
                </ToneButton>
            )}

            {showReview && (
                <ToneButton
                    tone={YELLOW}
                    onClick={onReview!}
                    title="Review needed — open review queue"
                    ariaLabel="Review needed — open review queue"
                    paddingX={12}
                >
                    <IcoError size={14} />
                    <span style={{ fontSize: 11 }}>Review needed</span>
                </ToneButton>
            )}

        </div>
        </>
    );
}

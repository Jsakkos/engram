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

import { useState, type CSSProperties, type ReactNode, type MouseEvent } from "react";
import { motion } from "motion/react";
import { Bug } from "lucide-react";
import { IcoCancel, IcoError, IcoRetry, IcoPlay } from "../icons";
import type { DiscState } from "../DiscCard";
import { sv } from "../synapse";

interface ActionButtonsProps {
    state: DiscState;
    isHovered: boolean;
    onCancel?: () => void;
    onReview?: () => void;
    onReIdentify?: () => void;
    onAdvance?: () => void;
    onReportBug?: () => void;
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

export function ActionButtons({ state, isHovered, onCancel, onReview, onReIdentify, onAdvance, onReportBug, emphasizeReIdentify = false }: ActionButtonsProps) {
    const showCancel = !!onCancel && (isHovered || CANCELABLE_STATES.includes(state));
    const showReview = !!onReview && state === "review_needed";
    const showAdvance = !!onAdvance && ACTIVE_STATES.includes(state);
    const showReportBug = !!onReportBug && isHovered;

    const handleAdvance = (e: MouseEvent) => {
        e.stopPropagation();
        if (window.confirm(
            "Force this job to its next step?\n\n" +
            "Tracks still ripping or matching will be sent to review (or failed if no " +
            "file was produced), and anything already matched will be organized."
        )) {
            onAdvance!();
        }
    };

    return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {showCancel && (
                <ToneButton
                    tone={RED}
                    onClick={onCancel!}
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

            {showReportBug && (
                <ToneButton
                    tone={RED}
                    onClick={(e) => { e.stopPropagation(); onReportBug!(); }}
                    title="Report a bug for this job"
                    ariaLabel="Report a bug for this job"
                    paddingX={0}
                    testId="disc-card-report-bug"
                >
                    <Bug size={14} />
                </ToneButton>
            )}
        </div>
    );
}

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
 */

import { useState, type CSSProperties, type ReactNode, type MouseEvent } from "react";
import { motion } from "motion/react";
import { X, AlertTriangle, RefreshCw } from "lucide-react";
import type { DiscState } from "../DiscCard";
import { sv } from "../synapse";

interface ActionButtonsProps {
    state: DiscState;
    isHovered: boolean;
    onCancel?: () => void;
    onReview?: () => void;
    onReIdentify?: () => void;
}

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
}

function ToneButton({ tone, onClick, title, ariaLabel, children, paddingX = 0 }: ToneButtonProps) {
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
            style={{
                ...baseStyle(tone, hovered),
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

export function ActionButtons({ state, isHovered, onCancel, onReview, onReIdentify }: ActionButtonsProps) {
    const showCancel = !!onCancel && (isHovered || ["scanning", "ripping", "processing"].includes(state));
    const showReview = !!onReview && state === "review_needed";

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
                    <X size={14} />
                </ToneButton>
            )}

            {onReIdentify && (
                <ToneButton
                    tone={CYAN}
                    onClick={(e) => { e.stopPropagation(); onReIdentify(); }}
                    title="Wrong title — re-identify disc"
                    ariaLabel="Wrong title — re-identify disc"
                    paddingX={10}
                >
                    <RefreshCw size={12} />
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
                    <AlertTriangle size={14} />
                    <span style={{ fontSize: 11 }}>Review needed</span>
                </ToneButton>
            )}
        </div>
    );
}

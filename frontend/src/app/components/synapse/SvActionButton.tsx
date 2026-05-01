import type { CSSProperties, MouseEvent, ReactNode } from "react";
import { useState } from "react";
import { sv } from "./tokens";

export type SvActionButtonTone = "cyan" | "magenta" | "yellow" | "red" | "green" | "amber" | "neutral";
export type SvActionButtonSize = "sm" | "md" | "lg";

interface Props {
    /** Color family. `neutral` uses ink colors for back-style buttons. */
    tone: SvActionButtonTone;
    /** Visual scale. `sm`=22px (compact rows), `md`=30px (default actions), `lg`=36px (primary CTAs). */
    size?: SvActionButtonSize;
    onClick?: (e: MouseEvent) => void;
    disabled?: boolean;
    title?: string;
    ariaLabel?: string;
    /** Renders as `<a>` instead of `<button>` if href is provided. */
    href?: string;
    target?: string;
    rel?: string;
    /** Optional className for layout-only positioning (e.g. self-end). */
    className?: string;
    style?: CSSProperties;
    children: ReactNode;
}

interface ToneSpec {
    fg: string;
    fgHi: string;
    border: string;
    borderHi: string;
    bgHi: string;
    glow: string;
    glowHi: string;
}

const TONES: Record<SvActionButtonTone, ToneSpec> = {
    cyan:    { fg: sv.cyan,    fgHi: sv.cyanHi,    border: `${sv.cyan}55`,    borderHi: sv.cyan,    bgHi: "rgba(94, 234, 212, 0.10)", glow: `${sv.cyan}33`,    glowHi: `${sv.cyan}66`    },
    magenta: { fg: sv.magenta, fgHi: sv.magentaHi, border: `${sv.magenta}55`, borderHi: sv.magenta, bgHi: "rgba(255, 61, 127, 0.10)", glow: `${sv.magenta}33`, glowHi: `${sv.magenta}66` },
    yellow:  { fg: sv.yellow,  fgHi: sv.yellow,    border: `${sv.yellow}99`,  borderHi: sv.yellow,  bgHi: "rgba(253, 224, 71, 0.12)", glow: `${sv.yellow}55`,  glowHi: `${sv.yellow}99`  },
    red:     { fg: sv.red,     fgHi: "#ff8a8a",    border: `${sv.red}55`,     borderHi: sv.red,     bgHi: "rgba(255, 85, 85, 0.10)",  glow: `${sv.red}33`,     glowHi: `${sv.red}66`     },
    green:   { fg: sv.green,   fgHi: sv.green,     border: `${sv.green}55`,   borderHi: sv.green,   bgHi: "rgba(134, 239, 172, 0.10)",glow: `${sv.green}33`,   glowHi: `${sv.green}66`   },
    amber:   { fg: sv.amber,   fgHi: sv.amber,     border: `${sv.amber}55`,   borderHi: sv.amber,   bgHi: "rgba(252, 211, 77, 0.10)", glow: `${sv.amber}33`,   glowHi: `${sv.amber}66`   },
    neutral: { fg: sv.inkDim,  fgHi: sv.cyanHi,    border: sv.line,           borderHi: sv.lineHi,  bgHi: "rgba(94, 234, 212, 0.05)", glow: "transparent",     glowHi: `${sv.cyan}33`    },
};

const SIZES: Record<SvActionButtonSize, { height: number; padX: number; fontSize: number; gap: number; letterSpacing: string }> = {
    sm: { height: 22, padX: 8,  fontSize: 9,  gap: 4, letterSpacing: "0.20em" },
    md: { height: 30, padX: 12, fontSize: 11, gap: 6, letterSpacing: "0.20em" },
    lg: { height: 36, padX: 16, fontSize: 12, gap: 8, letterSpacing: "0.18em" },
};

/**
 * Unified action button — synapse v2 vocabulary across the entire app.
 * Replaces ~5 ad-hoc inline button styles. Provides:
 *   - Three sizes (sm/md/lg) and seven tones (the six accents + neutral)
 *   - Hover state via React state (real swap of border/bg/glow, not just CSS)
 *   - Renders as <a> when `href` is set (otherwise <button>)
 *   - Disabled state with reduced opacity and frozen tone
 *
 * Existing specialized button variants (ToneButton in DiscCard ActionButtons,
 * CompactRowButton in App.tsx, HeaderButton in ReviewQueue) remain since they
 * have niche behaviors. New call sites should use this primitive.
 */
export function SvActionButton({
    tone,
    size = "md",
    onClick,
    disabled,
    title,
    ariaLabel,
    href,
    target,
    rel,
    className,
    style,
    children,
}: Props) {
    const [hovered, setHovered] = useState(false);
    const t = TONES[tone];
    const s = SIZES[size];
    const live = hovered && !disabled;

    const composed: CSSProperties = {
        height: s.height,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: s.gap,
        padding: `0 ${s.padX}px`,
        background: live ? t.bgHi : sv.bg0,
        border: `1px solid ${live ? t.borderHi : t.border}`,
        color: live ? t.fgHi : t.fg,
        fontFamily: sv.mono,
        fontSize: s.fontSize,
        fontWeight: 700,
        letterSpacing: s.letterSpacing,
        textTransform: "uppercase",
        textDecoration: "none",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        boxShadow: t.glow === "transparent" && !live ? "none" : `0 0 ${live ? 14 : 8}px ${live ? t.glowHi : t.glow}`,
        transition: "background 120ms ease-out, border-color 120ms ease-out, color 120ms ease-out, box-shadow 120ms ease-out",
        ...style,
    };

    const enter = () => !disabled && setHovered(true);
    const leave = () => setHovered(false);

    if (href) {
        return (
            <a
                href={href}
                target={target}
                rel={rel}
                onClick={onClick}
                title={title}
                aria-label={ariaLabel}
                className={className}
                style={composed}
                onMouseEnter={enter}
                onMouseLeave={leave}
                onFocus={enter}
                onBlur={leave}
                data-testid="sv-action-button"
                data-tone={tone}
                data-size={size}
            >
                {children}
            </a>
        );
    }

    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            title={title}
            aria-label={ariaLabel}
            className={className}
            style={composed}
            onMouseEnter={enter}
            onMouseLeave={leave}
            onFocus={enter}
            onBlur={leave}
            data-testid="sv-action-button"
            data-tone={tone}
            data-size={size}
        >
            {children}
        </button>
    );
}

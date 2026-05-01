import type { CSSProperties, ReactNode } from "react";
import { motion } from "motion/react";
import { ArrowLeft } from "lucide-react";
import { sv } from "./tokens";

interface Props {
    /** The uppercase title — rendered in display font with cyan glow. */
    title: string;
    /** Caret-prefixed mono subtitle, e.g. "› For All Mankind". */
    subtitle?: string;
    /** Optional icon shown to the left of the title (e.g. BarChart3). */
    icon?: ReactNode;
    /** Back-navigation handler. When omitted, no back button renders. */
    onBack?: () => void;
    /** Right-side slot — typically actions like "Report Bug" buttons. */
    right?: ReactNode;
    /** Inner max-width in px. Defaults to `sv.layoutMaxWidth`. */
    maxWidth?: number;
    /** Override testid for E2E selectors. */
    testid?: string;
}

const HEADER_PADDING_Y = 18;

/**
 * Shared sticky page header for Dashboard / Review / History.
 *
 * Synapse v2 vocabulary: 1px cyan-tinted bottom border, blurred translucent
 * background, monospace + uppercase + cyan-glow title, left back-arrow + icon,
 * right slot for actions. Single source of truth for header chrome — replaces
 * the three slightly-different ad-hoc headers that existed before.
 */
export function SvPageHeader({
    title,
    subtitle,
    icon,
    onBack,
    right,
    maxWidth = sv.layoutMaxWidth,
    testid = "sv-page-header",
}: Props) {
    const root: CSSProperties = {
        position: "sticky",
        top: 0,
        zIndex: 10,
        borderBottom: `1px solid ${sv.lineMid}`,
        background: "rgba(10, 14, 24, 0.78)",
        backdropFilter: "blur(14px)",
        boxShadow: `0 0 24px ${sv.cyan}1a`,
    };

    const inner: CSSProperties = {
        maxWidth,
        margin: "0 auto",
        padding: `${HEADER_PADDING_Y}px ${sv.layoutPadX}px`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
    };

    return (
        <header style={root} data-testid={testid}>
            <div style={inner}>
                <div style={{ display: "flex", alignItems: "center", gap: 16, minWidth: 0 }}>
                    {onBack && <BackButton onClick={onBack} />}
                    {icon && (
                        <span
                            style={{
                                color: sv.cyan,
                                filter: `drop-shadow(0 0 8px ${sv.cyan}99)`,
                                display: "inline-flex",
                            }}
                        >
                            {icon}
                        </span>
                    )}
                    <div style={{ minWidth: 0 }}>
                        <h1
                            style={{
                                margin: 0,
                                fontFamily: sv.display,
                                fontSize: 22,
                                fontWeight: 700,
                                letterSpacing: "0.16em",
                                textTransform: "uppercase",
                                color: sv.cyanHi,
                                textShadow: `0 0 14px ${sv.cyan}88`,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                            }}
                            data-testid="sv-page-header-title"
                        >
                            {title}
                        </h1>
                        {subtitle && (
                            <p
                                style={{
                                    margin: "2px 0 0 0",
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    letterSpacing: "0.10em",
                                    color: sv.inkDim,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                }}
                            >
                                {subtitle}
                            </p>
                        )}
                    </div>
                </div>
                {right && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                        {right}
                    </div>
                )}
            </div>
        </header>
    );
}

function BackButton({ onClick }: { onClick: () => void }) {
    return (
        <motion.button
            whileHover={{ x: -2 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            onClick={onClick}
            aria-label="Back"
            style={{
                width: 32,
                height: 32,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
                color: sv.cyan,
                cursor: "pointer",
                transition: "border-color 120ms ease-out, color 120ms ease-out",
            }}
            onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = sv.cyan;
                e.currentTarget.style.color = sv.cyanHi;
            }}
            onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = sv.lineMid;
                e.currentTarget.style.color = sv.cyan;
            }}
            data-testid="sv-page-header-back"
        >
            <ArrowLeft size={16} />
        </motion.button>
    );
}

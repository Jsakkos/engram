import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";

export type SvNoticeTone = "error" | "warn" | "info" | "success";

interface Props {
    tone: SvNoticeTone;
    children: ReactNode;
    /** Optional leading icon, rendered in the tone color. */
    icon?: ReactNode;
    /** Optional dismiss button — renders an X on the right when provided. */
    onDismiss?: () => void;
    style?: CSSProperties;
    testid?: string;
}

const TONES: Record<SvNoticeTone, { color: string }> = {
    error:   { color: sv.red    },
    warn:    { color: sv.yellow },
    info:    { color: sv.cyan   },
    success: { color: sv.green  },
};

/**
 * Inline notification banner — error / warning / info / success.
 * Replaces the ad-hoc `border-{color}/30 bg-{color}/5 text-{color}-400` divs
 * that appeared in ReviewQueue, ContributePage, EnhanceWizard, and HistoryPage.
 *
 * Synapse v2 vocabulary: 1px tinted border, low-alpha background, soft glow,
 * mono body text.
 */
export function SvNotice({ tone, children, icon, onDismiss, style, testid = "sv-notice" }: Props) {
    const c = TONES[tone].color;
    const composed: CSSProperties = {
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "12px 16px",
        background: `${c}10`,
        border: `1px solid ${c}55`,
        boxShadow: `0 0 12px ${c}22`,
        color: c,
        fontFamily: sv.mono,
        fontSize: 12,
        letterSpacing: "0.06em",
        lineHeight: 1.45,
        ...style,
    };
    return (
        <div style={composed} data-testid={testid} data-tone={tone}>
            {icon && <span style={{ flexShrink: 0, color: c, display: "inline-flex" }}>{icon}</span>}
            <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
            {onDismiss && (
                <button
                    type="button"
                    onClick={onDismiss}
                    aria-label="Dismiss"
                    style={{
                        flexShrink: 0,
                        background: "transparent",
                        border: 0,
                        color: `${c}aa`,
                        cursor: "pointer",
                        fontFamily: sv.mono,
                        fontSize: 14,
                        padding: 0,
                        lineHeight: 1,
                        transition: "color 120ms",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = c; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = `${c}aa`; }}
                >
                    ×
                </button>
            )}
        </div>
    );
}

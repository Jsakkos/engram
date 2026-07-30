/**
 * Media type badge — color-coded per content type, Synapse v2 styling.
 * Sharp 90° corners, mono label, cyan-tinted border per content kind.
 */

import { IcoMovie, IcoTv, IcoDisc } from "../icons";
import type { DiscState, MediaType } from "../DiscCard";
import { sv } from "../synapse";

interface MediaTypeBadgeProps {
    mediaType: MediaType;
    /** Job state. An unknown content type only reads as "ANALYZING" while the
     *  pipeline is actually running — see UNSETTLED_STATES. */
    state?: DiscState;
}

interface Variant {
    Icon: React.ElementType;
    label: string;
    color: string;
    pulse?: boolean;
}

/**
 * States in which the content type is still being worked out. Anything else —
 * error, completed, review_needed, idle — has settled: nothing is analyzing,
 * so an unknown type is a *result*, not a work-in-progress (#552). Without
 * this gate a job that failed during identification pulsed "ANALYZING"
 * forever, right next to its own red ERROR badge.
 */
const UNSETTLED_STATES: ReadonlySet<DiscState> = new Set<DiscState>([
    "scanning",
    "archiving_iso",
    "ripping",
    "matching",
    "organizing",
    "processing",
]);

const VARIANTS: Record<MediaType, Variant> = {
    movie:   { Icon: IcoMovie, label: "MOVIE",     color: sv.magenta },
    tv:      { Icon: IcoTv,    label: "TV",        color: sv.cyan    },
    unknown: { Icon: IcoDisc,  label: "ANALYZING", color: sv.amber, pulse: true },
};

/** Settled-but-unknown: a plain, static label with no "still working" signal. */
const UNKNOWN_SETTLED: Variant = {
    Icon: IcoDisc,
    label: "UNKNOWN",
    color: sv.inkDim,
};

export function MediaTypeBadge({ mediaType, state }: MediaTypeBadgeProps) {
    const settled = state !== undefined && !UNSETTLED_STATES.has(state);
    const v = mediaType === "unknown" && settled ? UNKNOWN_SETTLED : VARIANTS[mediaType];
    const { Icon } = v;

    return (
        <div
            data-testid={`sv-mediatype-${mediaType}`}
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                background: "rgba(10,14,24,0.90)",
                border: `1px solid ${v.color}55`,
                boxShadow: `0 0 8px ${v.color}33`,
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.20em",
                color: v.color,
                animation: v.pulse ? "svPulse 1.2s ease-in-out infinite" : undefined,
            }}
        >
            <Icon size={14} color={v.color} />
            <span>{v.label}</span>
        </div>
    );
}

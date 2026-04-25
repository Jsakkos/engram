/**
 * Media type badge — color-coded per content type, Synapse v2 styling.
 * Sharp 90° corners, mono label, cyan-tinted border per content kind.
 */

import { Film, Tv, Disc } from "lucide-react";
import type { MediaType } from "../DiscCard";
import { sv } from "../synapse";

interface MediaTypeBadgeProps {
    mediaType: MediaType;
}

interface Variant {
    Icon: React.ElementType;
    label: string;
    color: string;
    pulse?: boolean;
}

const VARIANTS: Record<MediaType, Variant> = {
    movie:   { Icon: Film, label: "MOVIE",     color: sv.magenta },
    tv:      { Icon: Tv,   label: "TV",        color: sv.cyan    },
    unknown: { Icon: Disc, label: "ANALYZING", color: sv.amber, pulse: true },
};

export function MediaTypeBadge({ mediaType }: MediaTypeBadgeProps) {
    const v = VARIANTS[mediaType];
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
                animation: v.pulse ? "svPulse 1.6s ease-in-out infinite" : undefined,
            }}
        >
            <Icon size={14} color={v.color} />
            <span>{v.label}</span>
        </div>
    );
}

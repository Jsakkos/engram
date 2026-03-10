/**
 * Media type badge component for disc cards — color-coded per content type
 */

import { Film, Tv, Disc } from "lucide-react";
import type { MediaType } from "../DiscCard";

interface MediaTypeBadgeProps {
    mediaType: MediaType;
}

export function MediaTypeBadge({ mediaType }: MediaTypeBadgeProps) {
    if (mediaType === "movie") {
        return (
            <div
                className="px-3 py-1.5 bg-navy-900/90 border-2 border-magenta-500/40 flex items-center gap-1.5 rounded-sm"
                style={{ boxShadow: "0 0 8px rgba(236, 72, 153, 0.2)" }}
            >
                <Film className="w-4 h-4 text-magenta-400" />
                <span className="text-xs font-bold text-magenta-400 tracking-wider">
                    MOVIE
                </span>
            </div>
        );
    }

    if (mediaType === "tv") {
        return (
            <div
                className="px-3 py-1.5 bg-navy-900/90 border-2 border-cyan-500/40 flex items-center gap-1.5 rounded-sm"
                style={{ boxShadow: "0 0 8px rgba(6, 182, 212, 0.2)" }}
            >
                <Tv className="w-4 h-4 text-cyan-400" />
                <span className="text-xs font-bold text-cyan-400 tracking-wider">
                    TV
                </span>
            </div>
        );
    }

    return (
        <div
            className="px-3 py-1.5 bg-navy-900/90 border-2 border-amber-500/40 flex items-center gap-1.5 rounded-sm animate-pulse"
            style={{ boxShadow: "0 0 8px rgba(245, 158, 11, 0.2)" }}
        >
            <Disc className="w-4 h-4 text-amber-400" />
            <span className="text-xs font-bold text-amber-400 tracking-wider">
                ANALYZING
            </span>
        </div>
    );
}

/**
 * Media type badge component for disc cards
 */

import { Film, Tv, Disc } from "lucide-react";
import type { MediaType } from "../DiscCard";

interface MediaTypeBadgeProps {
    mediaType: MediaType;
}

export function MediaTypeBadge({ mediaType }: MediaTypeBadgeProps) {
    if (mediaType === "movie") {
        return (
            <div className="px-3 py-1.5 bg-black border-2 border-slate-700 flex items-center gap-1.5">
                <Film className="w-4 h-4 text-slate-400" />
                <span className="text-xs font-bold text-slate-400 tracking-wider">
                    MOVIE
                </span>
            </div>
        );
    }

    if (mediaType === "tv") {
        return (
            <div className="px-3 py-1.5 bg-black border-2 border-slate-700 flex items-center gap-1.5">
                <Tv className="w-4 h-4 text-slate-400" />
                <span className="text-xs font-bold text-slate-400 tracking-wider">
                    TV
                </span>
            </div>
        );
    }

    return (
        <div className="px-3 py-1.5 bg-black border-2 border-slate-700 flex items-center gap-1.5">
            <Disc className="w-4 h-4 text-slate-400 animate-pulse" />
            <span className="text-xs font-bold text-slate-400 tracking-wider">
                ANALYZING
            </span>
        </div>
    );
}

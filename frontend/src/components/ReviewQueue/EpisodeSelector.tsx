/**
 * Episode selection dropdown component with season selector
 */

import { useState } from 'react';
import { DiscTitle, Job } from '../../types';
import { MatchDetails } from './types';
import { generateEpisodeOptions, parseMatchDetails } from './utils';
import { EPISODE_CONFIG } from '../../config/constants';

interface EpisodeSelectorProps {
    title: DiscTitle;
    job: Job | null;
    selectedEpisode: string;
    onEpisodeChange: (titleId: number, episodeCode: string) => void;
}

export function EpisodeSelector({ title, job, selectedEpisode, onEpisodeChange }: EpisodeSelectorProps) {
    const [season, setSeason] = useState(job?.detected_season || 1);

    const getAlternativeMatches = (title: DiscTitle): Array<{ episode: string; confidence: number; vote_count?: number }> => {
        const details: MatchDetails = parseMatchDetails(title);
        return details.runner_ups || [];
    };

    return (
        <div className="flex items-center gap-1">
            <label className="text-xs text-slate-400 font-mono whitespace-nowrap" title="Season">S</label>
            <input
                type="number"
                min={1}
                max={20}
                value={season}
                onChange={(e) => setSeason(Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
                className="w-10 bg-slate-800 border border-slate-600 rounded px-1 py-0.5 text-xs text-center font-mono text-slate-200"
                title="Season number"
            />
            <select
                value={selectedEpisode}
                onChange={(e) => onEpisodeChange(title.id, e.target.value)}
            >
                <option value="">Select episode...</option>

                {/* Primary match - show confidence */}
                {title.matched_episode && (
                    <option value={title.matched_episode}>
                        {title.matched_episode} - Best Match ({Math.round(title.match_confidence * 100)}%)
                    </option>
                )}

                {/* Alternative matches from runner_ups */}
                {getAlternativeMatches(title).map((alt, idx) => (
                    <option key={`alt-${idx}`} value={alt.episode}>
                        {alt.episode} - Alternative ({Math.round(alt.confidence * 100)}%)
                    </option>
                ))}

                {(title.matched_episode || getAlternativeMatches(title).length > 0) && (
                    <option disabled>──────────</option>
                )}

                {/* All episodes (manual fallback) */}
                {generateEpisodeOptions(season, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON).map(ep => (
                    <option key={ep} value={ep}>{ep}</option>
                ))}

                <option value="skip">Skip this title</option>
            </select>
        </div>
    );
}

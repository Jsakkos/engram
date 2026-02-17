/**
 * Episode selection dropdown component
 */

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
    const getAlternativeMatches = (title: DiscTitle): Array<{ episode: string; confidence: number; vote_count?: number }> => {
        const details: MatchDetails = parseMatchDetails(title);
        return details.runner_ups || [];
    };

    return (
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
            {generateEpisodeOptions(job?.detected_season || 1, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON).map(ep => (
                <option key={ep} value={ep}>{ep}</option>
            ))}

            <option value="skip">Skip this title</option>
        </select>
    );
}

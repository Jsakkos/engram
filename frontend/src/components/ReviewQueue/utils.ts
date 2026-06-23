/**
 * Utility functions for ReviewQueue component
 */

import { DiscTitle } from '../../types';
import { MatchDetails } from './types';
import { normalizeEpisodeCode } from './coverage';
import { MATCHING_CONFIG } from '../../config/constants';
import { formatSizeGB, formatDurationLong } from '../../utils/formatting';
import { sv } from '../../app/components/synapse';

/**
 * Format file size in bytes to human-readable format.
 * Re-exported under the original name for existing call sites.
 */
export { formatSizeGB as formatSize };

/**
 * Format duration in seconds to HH:MM:SS or MM:SS format.
 * Re-exported under the original name for existing call sites.
 */
export { formatDurationLong as formatDuration };

/**
 * Parse match_details JSON from a title
 */
export function parseMatchDetails(title: DiscTitle): MatchDetails {
    if (!title.match_details) return {};
    try {
        return typeof title.match_details === 'string'
            ? JSON.parse(title.match_details)
            : title.match_details;
    } catch {
        return {};
    }
}

/**
 * Get review reasons for a title
 */
export function getReviewReasons(title: DiscTitle): string[] {
    const details = parseMatchDetails(title);
    const reasons: string[] = [];

    if (title.match_confidence < MATCHING_CONFIG.HIGH_CONFIDENCE) {
        reasons.push('Low confidence');
    }
    if (details.vote_count && details.vote_count < MATCHING_CONFIG.MIN_VOTES) {
        reasons.push('Few votes');
    }
    if (details.conflict_reason) {
        reasons.push(details.conflict_reason);
    }
    if (details.runner_ups && details.runner_ups.length > 0) {
        reasons.push(`${details.runner_ups.length} alternatives`);
    }

    return reasons;
}

/**
 * Display name for a title — filename basename, or a generic fallback.
 */
export function titleDisplayName(title: DiscTitle): string {
    return title.output_filename
        ? title.output_filename.split(/[/\\]/).pop() ?? `Title ${title.title_index}`
        : `Title ${title.title_index}`;
}

/**
 * Color a confidence score: green (auto-match), yellow (plausible), red (weak).
 */
export function confidenceColor(confidence: number): string {
    if (confidence >= MATCHING_CONFIG.AUTO_MATCH_THRESHOLD) return sv.green;
    if (confidence >= MATCHING_CONFIG.MIN_CONFIDENCE) return sv.yellow;
    return sv.red;
}

/**
 * Generate episode options for a given season
 */
export function generateEpisodeOptions(season: number, maxEpisodes: number): string[] {
    const options: string[] = [];
    for (let i = 1; i <= maxEpisodes; i++) {
        options.push(`S${season.toString().padStart(2, '0')}E${i.toString().padStart(2, '0')}`);
    }
    return options;
}

/** A staged review decision for a single title. */
export type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';

/**
 * Build the initial staged selections/actions from persisted match results.
 *
 * An auto-deferred extra (matched_episode === "extra") pre-fills as the "extra"
 * action so the review UI shows it selected as an extra and a no-op save re-files
 * it as one. Any other matched_episode pre-fills as a (canonicalized) episode pick.
 */
export function buildInitialSelections(titles: DiscTitle[]): {
    episodes: Record<number, string>;
    actions: Record<number, TitleAction>;
} {
    const episodes: Record<number, string> = {};
    const actions: Record<number, TitleAction> = {};
    for (const title of titles) {
        if (title.matched_episode === 'extra') {
            episodes[title.id] = 'extra';
            actions[title.id] = 'extra';
        } else if (title.matched_episode) {
            episodes[title.id] = normalizeEpisodeCode(title.matched_episode);
            actions[title.id] = 'episode';
        }
    }
    return { episodes, actions };
}

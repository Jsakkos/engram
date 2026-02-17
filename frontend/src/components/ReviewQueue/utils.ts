/**
 * Utility functions for ReviewQueue component
 */

import { DiscTitle } from '../../types';
import { MatchDetails } from './types';
import { MATCHING_CONFIG } from '../../config/constants';

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
 * Format file size in bytes to human-readable format
 */
export function formatSize(bytes: number): string {
    const gb = bytes / (1024 * 1024 * 1024);
    return `${gb.toFixed(2)} GB`;
}

/**
 * Format duration in seconds to HH:MM:SS or MM:SS format
 */
export function formatDuration(seconds: number): string {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) {
        return `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Get detailed confidence explanation tooltip
 */
export function getDetailedConfidenceTooltip(title: DiscTitle): string {
    const conf = Math.round(title.match_confidence * 100);
    const details = parseMatchDetails(title);
    const votes = details.vote_count || 0;
    const coverage = Math.round((details.file_cov || 0) * 100);

    let explanation = `Confidence: ${conf}%\n\n`;

    // Explain what confidence measures
    explanation += "This score represents how closely the audio fingerprint ";
    explanation += "of this video matches the reference episode audio.\n\n";

    // Explain why it needs review
    if (title.match_confidence < MATCHING_CONFIG.HIGH_CONFIDENCE) {
        explanation += `⚠️ Below auto-match threshold (${MATCHING_CONFIG.HIGH_CONFIDENCE * 100}%)\n`;
        explanation += "Needs manual review to confirm episode.\n\n";
    }

    // Provide context about reliability
    explanation += `Supporting Evidence:\n`;
    explanation += `• ${votes} audio chunks matched this episode\n`;
    explanation += `• ${coverage}% of video file was analyzed\n`;

    if (votes < 3) {
        explanation += `\n⚠️ Low vote count - match may be unreliable`;
    }
    if (coverage < 50) {
        explanation += `\n⚠️ Low coverage - limited data to compare`;
    }

    return explanation;
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
 * Generate episode options for a given season
 */
export function generateEpisodeOptions(season: number, maxEpisodes: number): string[] {
    const options: string[] = [];
    for (let i = 1; i <= maxEpisodes; i++) {
        options.push(`S${season.toString().padStart(2, '0')}E${i.toString().padStart(2, '0')}`);
    }
    return options;
}

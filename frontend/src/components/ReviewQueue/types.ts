/**
 * Type definitions for ReviewQueue component
 */

import { DiscTitle } from '../../types';

export interface MatchDetails {
    vote_count?: number;
    file_cov?: number;
    score?: number;
    score_gap?: number;
    runner_ups?: Array<{
        episode: string;
        confidence: number;
        vote_count?: number;
    }>;
    error?: string;
    matches_found?: number;
    conflict_reason?: string;
    auto_sorted?: string;
}

export interface ReviewState {
    selectedEpisodes: Record<number, string>;
    selectedEditions: Record<number, string>;
    expandedTitles: Set<number>;
}

export type { DiscTitle };

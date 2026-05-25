/**
 * Type definitions for ReviewQueue component
 */

import { DiscTitle } from '../../types';

export interface MatchDetails {
    vote_count?: number;
    target_votes?: number;
    file_cov?: number;
    score?: number;
    score_gap?: number;
    runner_ups?: Array<{
        episode: string;
        confidence: number;
        vote_count?: number;
        target_votes?: number;
    }>;
    error?: string;
    message?: string;
    matches_found?: number;
    conflict_reason?: string;
    auto_sorted?: string;
}

/** One episode slot from GET /api/jobs/{id}/season-roster. */
export interface RosterEpisode {
    episode_code: string;
    episode_number: number;
    name: string;
    /** Persisted coverage from the server; the UI recomputes live while editing. */
    status?: 'assigned' | 'duplicate' | 'missing' | 'off';
    assigned_title_ids?: number[];
}

/** Response shape of the season-roster endpoint. */
export interface SeasonRoster {
    available: boolean;
    season_number: number | null;
    show_id: number | null;
    episodes: RosterEpisode[];
    reason: string | null;
}

export type { DiscTitle };

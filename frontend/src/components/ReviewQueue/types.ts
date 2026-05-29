/**
 * Type definitions for ReviewQueue component
 */

import { DiscTitle } from '../../types';

export interface LLMSuggestion {
    episode: number;
    confidence: number;
    reasoning: string;
    runner_up: { episode: number; confidence: number } | null;
    model: string;
}

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
    /** LLM-based episode suggestion, if one has been run for this title. */
    llm_suggestion?: LLMSuggestion;
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

/** One selectable output ordering for a show (#200). */
export interface OrderingOption {
    ordering: string; // "aired" | "dvd" | "digital" | ...
    label: string; // human label (e.g. "DVD Order")
    tmdb_type: number;
    /** Whether this ordering renumbers any episode matched on this disc. */
    diverges: boolean;
    /** Canonical "SxxExx" -> projected "SxxExx" for the season's episodes. */
    projection: Record<string, string>;
}

/** Response shape of the season-roster endpoint. */
export interface SeasonRoster {
    available: boolean;
    season_number: number | null;
    show_id: number | null;
    episodes: RosterEpisode[];
    reason: string | null;
    /** Episode ordering (#200) — selector is only shown when ordering_diverges. */
    ordering_available?: boolean;
    ordering_diverges?: boolean;
    current_ordering?: string;
    ordering_options?: OrderingOption[];
}

export type { DiscTitle };

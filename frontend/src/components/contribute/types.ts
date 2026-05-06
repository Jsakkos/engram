/**
 * Shared types for the Contribute page deck UI.
 * Mirrors the backend response shape from GET /api/contributions/decks.
 */

export interface DeckDiscEntry {
  job_id: number;
  volume_label: string;
  content_hash: string | null;
  disc_number: number;
  title_count: number;
  matched_count: number;
  runtime_seconds: number;
  episode_range: string | null;
  has_extras: boolean;
  export_status: "pending" | "exported" | "skipped" | "submitted";
  submitted_at: string | null;
  contribute_url: string | null;
  completed_at: string | null;
}

export interface DeckSubmissionStatus {
  pending: number;
  exported: number;
  skipped: number;
  submitted: number;
}

export interface Deck {
  release_group_id: string;
  is_solo: boolean;
  title: string | null;
  season: number | null;
  year: number | null;
  tmdb_id: number | null;
  content_type: "tv" | "movie" | "unknown";
  upc_code: string | null;
  asin: string | null;
  release_date: string | null;
  total_runtime_seconds: number;
  matched_episodes: string;
  discs: DeckDiscEntry[];
  submission_status: DeckSubmissionStatus;
  most_recent_completed_at: string | null;
}

export interface ContribConfig {
  discdb_contributions_enabled: boolean;
  discdb_contribution_tier: number;
  discdb_export_path: string;
  discdb_api_key_set: boolean;
  discdb_api_url: string;
}

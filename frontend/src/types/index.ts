// Type definitions for Engram Frontend

export type JobState =
    | 'idle'
    | 'identifying'
    | 'review_needed'
    | 'ripping'
    | 'matching'
    | 'organizing'
    | 'completed'
    | 'failed';

export type ContentType = 'tv' | 'movie' | 'unknown';

export type TitleState = 'pending' | 'ripping' | 'matching' | 'matched' | 'review' | 'completed' | 'failed';

export type SubtitleStatus = 'downloading' | 'completed' | 'partial' | 'failed' | null;

export interface Job {
    id: number;
    drive_id: string;
    volume_label: string;
    content_type: ContentType;
    state: JobState;
    current_speed: string;
    eta_seconds: number;
    progress_percent: number;
    current_title: number;
    total_titles: number;
    error_message: string | null;
    detected_title?: string;
    detected_season?: number;
    subtitle_status?: SubtitleStatus;
    subtitle_error_message?: string | null;
    subtitles_downloaded?: number;
    subtitles_total?: number;
    subtitles_failed?: number;
    review_reason?: string | null;
    conflict_status?: string | null;
    destination_mode?: string;
    created_at?: string;
    /**
     * Raw JSON string (from the API) of same-name TMDB candidates recorded at
     * identify time when >=2 shows share a name, e.g. Frasier 1993 + 2023 revival.
     * Each entry: `{ tmdb_id, name, year, popularity }`. Drives the quick-pick in
     * ReIdentifyModal. Null/absent when there was no same-name collision.
     */
    candidates_json?: string | null;
}

export interface DiscTitle {
    id: number;
    job_id: number;
    title_index: number;
    duration_seconds: number;
    file_size_bytes: number;
    chapter_count: number;
    is_selected: boolean;
    output_filename: string | null;
    matched_episode: string | null;
    match_confidence: number;
    match_stage?: string;
    match_progress?: number;
    video_resolution?: string;
    edition?: string;
    match_details?: string | { runner_ups?: Array<{ episode: string; confidence: number }> } | null;
    state: TitleState;
    expected_size_bytes?: number;
    actual_size_bytes?: number;
    matches_found?: number;
    matches_rejected?: number;
    conflict_resolution?: string | null;
    existing_file_path?: string | null;
    organized_from?: string | null;
    organized_to?: string | null;
    is_extra?: boolean;
    error_message?: string | null;
    match_source?: string | null;
    discdb_match_details?: string | null;
    discdb_flagged?: boolean;
    discdb_flag_reason?: string | null;
}

export interface DriveEvent {
    type: 'drive_event';
    drive_id: string;
    event: 'inserted' | 'removed';
    volume_label: string;
}

export interface JobUpdate {
    type: 'job_update';
    job_id: number;
    state: JobState;
    progress_percent: number;
    current_speed: string;
    eta_seconds: number;
    current_title?: number;
    total_titles?: number;
    error_message: string | null;
    content_type?: ContentType;
    detected_title?: string;
    detected_season?: number;
    review_reason?: string | null;
    conflict_status?: string | null;
}

export interface TitleUpdate {
    type: 'title_update';
    job_id: number;
    title_id: number;
    state: TitleState;
    matched_episode?: string | null;
    match_confidence?: number;
    match_stage?: string;
    match_progress?: number;
    duration_seconds?: number;
    file_size_bytes?: number;
    video_resolution?: string;
    edition?: string;
    expected_size_bytes?: number;
    actual_size_bytes?: number;
    matches_found?: number;
    matches_rejected?: number;
    match_details?: string | null;
    organized_from?: string | null;
    organized_to?: string | null;
    output_filename?: string | null;
    is_extra?: boolean;
    error?: string | null;
}

export interface SubtitleEvent {
    type: 'subtitle_event';
    job_id: number;
    status: 'downloading' | 'completed' | 'partial' | 'failed';
    downloaded: number;
    total: number;
    failed_count: number;
}

export interface TitlesDiscovered {
    type: 'titles_discovered';
    job_id: number;
    titles: Array<{
        id: number;
        title_index: number;
        duration_seconds: number;
        file_size_bytes: number;
        chapter_count: number;
        video_resolution?: string;
    }>;
    content_type: ContentType;
    detected_title?: string;
    detected_season?: number;
}

export interface UpdateStatusMessage {
    type: 'update_status';
    state: 'idle' | 'checking' | 'up_to_date' | 'downloading' | 'ready' | 'skipped' | 'error';
    current_version: string;
    latest_version?: string | null;
    release_notes?: string | null;
    release_url?: string | null;
    download_progress?: number | null;
    error?: string | null;
    is_frozen?: boolean;
}

/** Snapshot of update state, stored in App.tsx state. */
export interface UpdateStatus {
    state: 'idle' | 'checking' | 'up_to_date' | 'downloading' | 'ready' | 'skipped' | 'error';
    current_version: string;
    latest_version: string | null;
    release_notes: string | null;
    release_url: string | null;
    download_progress: number | null;
    error: string | null;
    is_frozen: boolean;
}

export interface FingerprintDisclosureRequiredMessage {
    type: 'fingerprint_disclosure_required';
    pending_count: number;
    pseudonym: string;
    server_url: string;
}

export type WebSocketMessage =
    | DriveEvent
    | JobUpdate
    | TitleUpdate
    | SubtitleEvent
    | TitlesDiscovered
    | UpdateStatusMessage
    | FingerprintDisclosureRequiredMessage;

export interface Config {
    makemkv_path: string;
    staging_path: string;
    library_movies_path: string;
    library_tv_path: string;
    tmdb_api_key: string;
}

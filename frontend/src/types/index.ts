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
    subtitles_downloaded?: number;
    subtitles_total?: number;
    subtitles_failed?: number;
    review_reason?: string | null;
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

export type WebSocketMessage =
    | DriveEvent
    | JobUpdate
    | TitleUpdate
    | SubtitleEvent
    | TitlesDiscovered;

export interface Config {
    makemkv_path: string;
    staging_path: string;
    library_movies_path: string;
    library_tv_path: string;
    transcoding_enabled: boolean;
    tmdb_api_key: string;
}

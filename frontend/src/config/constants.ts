/**
 * Application constants and configuration values.
 * Centralized location for magic numbers and thresholds.
 */

/**
 * Episode matching confidence thresholds
 */
export const MATCHING_CONFIG = {
  /** Minimum confidence to consider a match valid */
  MIN_CONFIDENCE: 0.5,
  /** Threshold for high-confidence matches */
  HIGH_CONFIDENCE: 0.7,
  /** Auto-match threshold (no review needed) */
  AUTO_MATCH_THRESHOLD: 0.85,
  /** Minimum votes required for a confident match */
  MIN_VOTES: 3,
} as const;

/**
 * UI behavior and timing configuration
 */
export const UI_CONFIG = {
  /** WebSocket reconnect delay in milliseconds */
  WEBSOCKET_RECONNECT_DELAY_MS: 3000,
  /** Maximum number of WebSocket reconnection attempts */
  MAX_RECONNECT_ATTEMPTS: 5,
  /** Delay before retrying poster fetch in milliseconds */
  POSTER_FETCH_RETRY_DELAY_MS: 2000,
  /** Maximum number of poster fetch retries */
  POSTER_MAX_RETRIES: 3,
} as const;

/**
 * Episode and season configuration
 */
export const EPISODE_CONFIG = {
  /** Default number of episodes per season (for initial state) */
  DEFAULT_EPISODES_PER_SEASON: 24,
  /** Minimum episode count for validation */
  MIN_EPISODE_COUNT: 3,
  /** Maximum episode count for validation */
  MAX_EPISODE_COUNT: 30,
} as const;

/**
 * Job state display names for UI
 */
export const JOB_STATE_LABELS = {
  idle: 'Idle',
  identifying: 'Scanning',
  review_needed: 'Review',
  ripping: 'Ripping',
  matching: 'Processing',
  organizing: 'Organizing',
  completed: 'Done',
  failed: 'Error',
} as const;

/**
 * Content type display names
 */
export const CONTENT_TYPE_LABELS = {
  tv: 'TV Show',
  movie: 'Movie',
  unknown: 'Unknown',
} as const;

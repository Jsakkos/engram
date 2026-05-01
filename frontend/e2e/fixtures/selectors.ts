/**
 * Selector constants for E2E tests
 *
 * Updated for the Synapse v2 redesign — every load-bearing element is now
 * keyed off a `data-testid` attribute. Prefer data-testid > text > role
 * over CSS classes; classes are presentation details that change with
 * future restyles.
 *
 * Note: track-state and track-byte selectors deliberately use text regex
 * instead of testid because the *text content* is the user-visible signal
 * the tests care about (e.g. "RIPPING", "245 MB / 520 MB").
 */

export const SELECTORS = {
  // App-level (Synapse v2 shell)
  appContainer: '[data-testid="sv-atmosphere"]',
  header: '[data-testid="sv-topbar"]',
  footer: '[data-testid="sv-statusbar"]',

  // Connection status
  connectionStatus: {
    connected: 'text=/LIVE/i',
    disconnected: '[data-testid="sv-status-ws"]:has-text("OFFLINE")',
  },

  // Filter buttons
  filterAll: '[data-testid="sv-filter-all"]',
  filterActive: '[data-testid="sv-filter-active"]',
  filterDone: '[data-testid="sv-filter-completed"]',

  // Disc card (the SvJobCard outer)
  discCard: '[data-testid="sv-job-card"]',
  discTitle: '[data-testid="sv-job-title"]',
  discSubtitle: '[data-testid="sv-disc-metadata"] p',

  // Media type badge
  movieBadge: '[data-testid="sv-mediatype-movie"]',
  tvBadge: '[data-testid="sv-mediatype-tv"]',

  // State indicator
  stateIndicator: '[data-testid="sv-state-indicator"]',
  stateScanning: 'text=/SCANNING/i',
  stateRipping: 'text=/RIPPING/i',
  stateProcessing: 'text=/PROCESSING/i',
  stateCompleted: 'text=/COMPLETE/i',
  stateFailed: 'text=/ERROR/i',

  // Progress bar (the SvBar wrapper used by CyberpunkProgressBar)
  progressBar: '[data-testid="sv-bar-progress"]',
  progressPercentage: 'text=/\\d+%/',

  // Speed and ETA values (text-based — content is the signal)
  speed: 'text=/\\d+(\\.\\d+)?x/',
  eta: 'text=/\\d+\\s*h|\\d+\\s*min|< 1 min/',

  // Track grid
  trackGrid: '[data-testid="sv-track-grid"]',
  trackItem: '[data-testid="sv-track-card"]',
  trackTitle: '[data-testid="sv-track-card"]',

  // Per-track state indicators — scoped to the track card via data-state.
  // Page-wide text matchers would also pick up "RIPPING" etc. from the
  // job-level StateIndicator pill and the DashboardSideRail activity log.
  trackStateRipping: '[data-testid="sv-track-card"][data-state="ripping"]',
  trackStateMatching: '[data-testid="sv-track-card"][data-state="matching"]',
  trackStateMatched: '[data-testid="sv-track-card"][data-state="matched"]',
  trackStateQueued: '[data-testid="sv-track-card"][data-state="pending"]',

  // Track byte progress (e.g., "245.3 MB / 520.1 MB")
  trackByteProgress: 'text=/\\d+(\\.\\d+)?\\s*(MB|GB)\\s*\\/\\s*\\d+(\\.\\d+)?\\s*(MB|GB)/i',

  // Match source badges (preserved data-testid contract)
  matchSourceDiscdb: '[data-testid="source-badge-discdb"]',
  matchSourceEngram: '[data-testid="source-badge-engram"]',
  matchSourceManual: '[data-testid="source-badge-user"]',

  // Match candidates with confidence
  matchCandidate: 'text=/S\\d{2}E\\d{2}/i',
  matchConfidence: 'text=/\\d+(\\.\\d+)?%/i',

  // Dashboard side rail (visible when active jobs exist + expanded view)
  dashboardGrid: '[data-testid="sv-dashboard-grid"]',
  sideRail: '[data-testid="sv-side-rail"]',
  sideRailNumeric: '[data-testid="sv-side-rail-numeric"]',
  sideRailBytes: '[data-testid="sv-side-rail-bytes"]',
  sideRailSpeed: '[data-testid="sv-side-rail-speed"]',
  sideRailThroughput: '[data-testid="sv-side-rail-throughput"]',
  sideRailStats: '[data-testid="sv-side-rail-stats"]',
  sideRailLog: '[data-testid="sv-side-rail-log"]',
  sideRailLogEntry: '[data-testid="sv-side-rail-log-entry"]',

  // History page stats rail
  historyGrid: '[data-testid="sv-history-grid"]',
  historyStatsRail: '[data-testid="sv-history-stats-rail"]',
  historyStatsGrid: '[data-testid="sv-history-stats-grid"]',
  historyStatsThroughput: '[data-testid="sv-history-stats-throughput"]',
  historyStatsDistribution: '[data-testid="sv-history-stats-distribution"]',

  // Disc-insert / classify visualization
  discInsert: '[data-testid="sv-disc-insert"]',
  discInsertRadar: '[data-testid="sv-disc-insert-radar"]',
  discInsertBreadcrumb: '[data-testid="sv-disc-insert-breadcrumb"]',
  discInsertBestMatch: '[data-testid="sv-disc-insert-best-match"]',

  // Empty states
  emptyState: 'text=/NO DISCS DETECTED|NO ACTIVE OPERATIONS|NO COMPLETED ARCHIVES/i',

  // Clear/Cancel buttons
  clearButton: '[data-testid="sv-clear-btn"]',
  cancelButton: 'button[title="Cancel Job"]',
};

/**
 * Helper: locate a disc card whose title contains the given substring.
 */
export function getDiscCardByTitle(title: string) {
  return `[data-testid="sv-job-card"]:has([data-testid="sv-job-title"]:has-text("${title}"))`;
}

/**
 * Helper: locate the Nth disc card (0-indexed) by position.
 */
export function getDiscCardByIndex(index: number) {
  return `([data-testid="sv-job-card"]) >> nth=${index}`;
}

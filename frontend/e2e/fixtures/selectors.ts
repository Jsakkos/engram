/**
 * Selector constants for E2E tests
 *
 * Updated for the Synapse v2 redesign — shell is keyed off `data-testid`
 * attributes from src/app/components/synapse/. Disc-card / track / progress
 * selectors still target the legacy DiscCard markup until Phase 3.
 *
 * Prefer text/role/data-testid selectors over CSS classes for resilience.
 */

export const SELECTORS = {
  // App-level (Synapse v2 shell — Phase 2)
  appContainer: '[data-testid="sv-atmosphere"]',
  header: '[data-testid="sv-topbar"]',
  footer: '[data-testid="sv-statusbar"]',

  // Connection status — appears in both topbar pill and statusbar
  connectionStatus: {
    connected: 'text=/LIVE/i',
    disconnected: '[data-testid="sv-status-ws"]:has-text("OFFLINE")',
  },

  // Filter buttons (Synapse v2 strip)
  filterAll: '[data-testid="sv-filter-all"]',
  filterActive: '[data-testid="sv-filter-active"]',
  filterDone: '[data-testid="sv-filter-completed"]',

  // Disc card (expanded view — DiscCard component wrapper)
  discCard: 'div.relative.overflow-hidden.rounded-lg.shadow-2xl',
  discTitle: 'h3.text-xl.font-bold.text-cyan-400',
  discSubtitle: 'p.text-sm.text-slate-400',

  // Media type badge (inside disc card)
  movieBadge: 'text=/MOVIE/i',
  tvBadge: 'text=/TV/i',

  // State indicator (labels from StateIndicator.tsx)
  stateScanning: 'text=/SCANNING/i',
  stateRipping: 'text=/RIPPING/i',
  stateProcessing: 'text=/PROCESSING/i',
  stateCompleted: 'text=/COMPLETE/i',
  stateFailed: 'text=/ERROR/i',

  // Progress bar (CyberpunkProgressBar component — the outer container with progress text)
  progressBar: 'div:has(> div.h-3.bg-navy-800)',
  progressPercentage: 'text=/\\d+%/',

  // Speed and ETA
  speed: 'text=/\\d+(\\.\\d+)?x/',
  eta: 'text=/\\d+\\s*h|\\d+\\s*min|< 1 min/',

  // Track grid (for TV shows and multi-track movies)
  trackGrid: 'div.grid.grid-cols-2.gap-2',
  trackItem: 'div.border-2.cursor-pointer',
  trackTitle: 'div.font-bold.text-sm',

  // Per-track state indicators
  trackStateRipping: 'text=/RIPPING|EXTRACTING/i',
  trackStateMatching: 'text=/MATCHING/i',
  trackStateMatched: 'text=/MATCHED/i',
  trackStateQueued: 'text=/QUEUED/i',

  // Track byte progress (e.g., "245.3 MB / 520.1 MB")
  trackByteProgress: 'text=/\\d+(\\.\\d+)?\\s*(MB|GB)\\s*\\/\\s*\\d+(\\.\\d+)?\\s*(MB|GB)/i',

  // Match source badges
  matchSourceDiscdb: '[data-testid="source-badge-discdb"]',
  matchSourceEngram: '[data-testid="source-badge-engram"]',
  matchSourceManual: '[data-testid="source-badge-user"]',

  // Match candidates with confidence
  matchCandidate: 'text=/S\\d{2}E\\d{2}/i',
  matchConfidence: 'text=/\\d+(\\.\\d+)?%/i',

  // Cycle indicator dots (transcribing phase)
  cycleIndicator: 'div[class*="rounded-full"]',

  // Empty states
  emptyState: 'text=/NO DISCS DETECTED|NO ACTIVE OPERATIONS|NO COMPLETED ARCHIVES/i',

  // Clear/Cancel buttons
  clearButton: '[data-testid="sv-clear-btn"]',
  cancelButton: 'button[title="Cancel Job"]',
};

/**
 * Helper to get disc card by title
 */
export function getDiscCardByTitle(title: string) {
  return `div.relative.overflow-hidden.rounded-lg.shadow-2xl:has(h3:has-text("${title}"))`;
}

/**
 * Helper to get disc card by position
 */
export function getDiscCardByIndex(index: number) {
  return `(div.relative.overflow-hidden.rounded-lg.shadow-2xl)[${index + 1}]`;
}

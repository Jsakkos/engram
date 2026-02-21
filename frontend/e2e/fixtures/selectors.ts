/**
 * Selector constants for E2E tests
 *
 * The new cyberpunk UI uses Tailwind utility classes without specific semantic class names.
 * We use text content and structural selectors for reliability.
 */

export const SELECTORS = {
  // App-level
  appContainer: 'div.min-h-screen.bg-black',
  header: 'div.border-b-2.border-cyan-500',
  footer: 'div.fixed.bottom-0',

  // Connection status (in footer)
  connectionStatus: {
    connected: 'text=/WEBSOCKET CONNECTED/i',
    disconnected: 'text=/DISCONNECTED/i',
  },

  // Filter buttons
  filterAll: 'button:has-text("ALL")',
  filterActive: 'button:has-text("ACTIVE")',
  filterDone: 'button:has-text("DONE")',

  // Disc card
  discCard: 'div.relative.overflow-hidden.rounded-none.bg-black.border-2',
  discTitle: 'h3.text-xl.font-bold.text-cyan-400',
  discSubtitle: 'p.text-sm.text-slate-400',

  // Media type badge (inside disc card)
  movieBadge: 'text=/MOVIE/i',
  tvBadge: 'text=/TV/i',

  // State indicator (labels from StateIndicator.tsx)
  stateScanning: 'text=/SCANNING/i',
  stateRipping: 'text=/PROCESSING/i',
  stateCompleted: 'text=/COMPLETE/i',
  stateFailed: 'text=/ERROR/i',

  // Progress bar (CyberpunkProgressBar component — the outer container with progress text)
  progressBar: 'div:has(> div.h-3.bg-black)',
  progressPercentage: 'text=/\\d+%/',

  // Speed and ETA
  speed: 'text=/\\d+(\\.\\d+)?x/',
  eta: 'text=/\\d+\\s*h|\\d+\\s*min|< 1 min/',

  // Track grid (for TV shows)
  trackGrid: 'div.grid.grid-cols-2',
  trackItem: 'div.border-2',
  trackTitle: 'div.font-bold.text-sm',

  // Per-track state indicators
  trackStateRipping: 'text=/RIPPING|EXTRACTING/i',
  trackStateMatching: 'text=/MATCHING/i',
  trackStateMatched: 'text=/MATCHED/i',

  // Track byte progress (e.g., "245.3 MB / 520.1 MB")
  trackByteProgress: 'text=/\\d+(\\.\\d+)?\\s*(MB|GB)\\s*\\/\\s*\\d+(\\.\\d+)?\\s*(MB|GB)/i',

  // Match candidates with confidence
  matchCandidate: 'text=/S\\d{2}E\\d{2}/i',
  matchConfidence: 'text=/\\d+(\\.\\d+)?%/i',

  // Cycle indicator dots (transcribing phase)
  cycleIndicator: 'div[class*="rounded-full"]',

  // Empty states
  emptyState: 'text=/NO DISCS DETECTED|NO ACTIVE OPERATIONS|NO COMPLETED ARCHIVES/i',

  // Clear/Cancel buttons (need to find in new UI structure)
  clearButton: 'button:has-text("Clear")',
  cancelButton: 'button[title="Cancel Job"]',
};

/**
 * Helper to get disc card by title
 */
export function getDiscCardByTitle(title: string) {
  return `div.relative.overflow-hidden.rounded-none.bg-black.border-2:has(h3:has-text("${title}"))`;
}

/**
 * Helper to get disc card by position
 */
export function getDiscCardByIndex(index: number) {
  return `(div.relative.overflow-hidden.rounded-none.bg-black.border-2)[${index + 1}]`;
}

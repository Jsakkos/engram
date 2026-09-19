/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Icon system
   24×24 grid, 1.5px stroke, round caps. Cyan primary + magenta accent (used
   sparingly for "active" or "primary action"). The whole set is line-only —
   no fills except deliberate "lit" states. Built to feel like surveillance
   equipment glyphs, not material/feather icons.
   ═══════════════════════════════════════════════════════════════════════════ */

const I = brandTokens;

// Base wrapper — accepts size, color, accent
function Ico({ children, size = 24, color, accent, title }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}
      stroke={color || I.cyan} fill="none"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{display:'block', overflow:'visible'}}
      role="img" aria-label={title}>
      {children}
    </svg>
  );
}

// ── Status icons ────────────────────────────────────────────────────────────

const IcoIdle = (p) => (
  <Ico {...p} title="Idle">
    <circle cx="12" cy="12" r="9"/>
    <line x1="12" y1="12" x2="12" y2="6"/>
    <line x1="12" y1="12" x2="16" y2="14"/>
  </Ico>
);

const IcoScan = (p) => (
  <Ico {...p} title="Scanning">
    <circle cx="11" cy="11" r="6"/>
    <line x1="11" y1="5" x2="11" y2="17" opacity="0.5"/>
    <line x1="5" y1="11" x2="17" y2="11" opacity="0.5"/>
    <line x1="15.5" y1="15.5" x2="20" y2="20"/>
  </Ico>
);

const IcoRipping = (p) => (
  <Ico {...p} title="Ripping" accent={p?.accent || I.magenta}>
    <circle cx="12" cy="12" r="9"/>
    <circle cx="12" cy="12" r="5" opacity="0.5"/>
    <circle cx="12" cy="12" r="1.5" fill={p?.accent || I.magenta} stroke="none"/>
    <line x1="12" y1="12" x2="21" y2="12" stroke={p?.accent || I.magenta}/>
  </Ico>
);

const IcoMatching = (p) => (
  <Ico {...p} title="Matching" accent={p?.accent || I.amber}>
    <path d="M 4 8 L 9 8 L 11 11"/>
    <path d="M 20 8 L 15 8 L 13 11"/>
    <path d="M 4 16 L 9 16 L 11 13"/>
    <path d="M 20 16 L 15 16 L 13 13"/>
    <circle cx="12" cy="12" r="1.5" fill={p?.accent || I.amber} stroke="none"/>
  </Ico>
);

const IcoComplete = (p) => (
  <Ico {...p} title="Complete">
    <circle cx="12" cy="12" r="9"/>
    <path d="M 8 12 L 11 15 L 16 9"/>
  </Ico>
);

const IcoError = (p) => (
  <Ico {...p} title="Error" color={p?.color || I.red}>
    <path d="M 12 3 L 22 20 L 2 20 Z"/>
    <line x1="12" y1="10" x2="12" y2="14"/>
    <circle cx="12" cy="17" r="0.5" fill={p?.color || I.red} stroke="none"/>
  </Ico>
);

const IcoPaused = (p) => (
  <Ico {...p} title="Paused">
    <circle cx="12" cy="12" r="9"/>
    <line x1="10" y1="9" x2="10" y2="15"/>
    <line x1="14" y1="9" x2="14" y2="15"/>
  </Ico>
);

const IcoQueued = (p) => (
  <Ico {...p} title="Queued">
    <circle cx="12" cy="12" r="9" strokeDasharray="3 3"/>
    <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
  </Ico>
);

// ── Media-type icons ────────────────────────────────────────────────────────

const IcoDisc = (p) => (
  <Ico {...p} title="Disc">
    <circle cx="12" cy="12" r="9"/>
    <circle cx="12" cy="12" r="4"/>
    <circle cx="12" cy="12" r="1" fill="currentColor"/>
    <path d="M 12 3 A 9 9 0 0 1 21 12" strokeWidth="2.4" opacity="0.6"/>
  </Ico>
);

const IcoBluRay = (p) => (
  <Ico {...p} title="Blu-ray">
    <circle cx="12" cy="12" r="9"/>
    <circle cx="12" cy="12" r="4"/>
    <text x="12" y="20.5" fontFamily={I.mono} fontSize="3.4" fontWeight="700"
      textAnchor="middle" fill="currentColor" stroke="none" letterSpacing="0.05em">BD</text>
  </Ico>
);

const IcoDvd = (p) => (
  <Ico {...p} title="DVD">
    <circle cx="12" cy="12" r="9"/>
    <circle cx="12" cy="12" r="4"/>
    <text x="12" y="20.5" fontFamily={I.mono} fontSize="3" fontWeight="700"
      textAnchor="middle" fill="currentColor" stroke="none" letterSpacing="0.05em">DVD</text>
  </Ico>
);

const IcoTv = (p) => (
  <Ico {...p} title="TV series">
    <rect x="3" y="6" width="18" height="12" rx="1"/>
    <line x1="8" y1="20.5" x2="16" y2="20.5"/>
    <path d="M 8 10 L 11 12 L 8 14 Z" fill="currentColor"/>
  </Ico>
);

const IcoMovie = (p) => (
  <Ico {...p} title="Movie">
    <rect x="3" y="5" width="18" height="14" rx="1"/>
    <line x1="3" y1="9" x2="21" y2="9"/>
    <circle cx="6.5" cy="7" r="0.6" fill="currentColor"/>
    <circle cx="10" cy="7" r="0.6" fill="currentColor"/>
    <circle cx="13.5" cy="7" r="0.6" fill="currentColor"/>
    <circle cx="17" cy="7" r="0.6" fill="currentColor"/>
    <path d="M 10 12 L 15 14.5 L 10 17 Z" fill="currentColor"/>
  </Ico>
);

const IcoEpisode = (p) => (
  <Ico {...p} title="Episode">
    <rect x="3" y="5" width="18" height="14" rx="1"/>
    <line x1="3" y1="11" x2="21" y2="11" opacity="0.4"/>
    <line x1="3" y1="15" x2="21" y2="15" opacity="0.4"/>
    <line x1="9" y1="5" x2="9" y2="19" opacity="0.4"/>
    <line x1="15" y1="5" x2="15" y2="19" opacity="0.4"/>
    <rect x="9" y="11" width="6" height="4" fill="currentColor" stroke="none" opacity="0.9"/>
  </Ico>
);

const IcoLibrary = (p) => (
  <Ico {...p} title="Library">
    <rect x="4" y="4" width="3" height="16"/>
    <rect x="9" y="6" width="3" height="14"/>
    <rect x="14" y="3" width="3" height="17"/>
    <rect x="19" y="8" width="2" height="12"/>
  </Ico>
);

const IcoDrive = (p) => (
  <Ico {...p} title="Drive">
    <rect x="3" y="6" width="18" height="12" rx="1"/>
    <line x1="3" y1="14" x2="21" y2="14"/>
    <circle cx="7" cy="16.5" r="0.8" fill="currentColor"/>
    <line x1="11" y1="16.5" x2="18" y2="16.5" opacity="0.4"/>
    <line x1="6" y1="9.5" x2="14" y2="9.5" opacity="0.4"/>
  </Ico>
);

// ── Action / nav icons ───────────────────────────────────────────────────────

const IcoPlay = (p) => (
  <Ico {...p} title="Play">
    <path d="M 7 5 L 19 12 L 7 19 Z"/>
  </Ico>
);

const IcoPause = (p) => (
  <Ico {...p} title="Pause">
    <rect x="7" y="5" width="3" height="14"/>
    <rect x="14" y="5" width="3" height="14"/>
  </Ico>
);

const IcoCancel = (p) => (
  <Ico {...p} title="Cancel">
    <circle cx="12" cy="12" r="9"/>
    <line x1="8" y1="8" x2="16" y2="16"/>
    <line x1="16" y1="8" x2="8" y2="16"/>
  </Ico>
);

const IcoRetry = (p) => (
  <Ico {...p} title="Retry">
    <path d="M 4 12 A 8 8 0 1 1 6.5 17.7"/>
    <polyline points="3 17 6.5 17.7 7.2 14.2"/>
  </Ico>
);

const IcoEject = (p) => (
  <Ico {...p} title="Eject">
    <path d="M 6 11 L 12 4 L 18 11 Z"/>
    <line x1="6" y1="19" x2="18" y2="19"/>
  </Ico>
);

const IcoSettings = (p) => (
  <Ico {...p} title="Settings">
    <circle cx="12" cy="12" r="3"/>
    <path d="M 12 2 L 12 5 M 12 19 L 12 22 M 4.93 4.93 L 7.05 7.05 M 16.95 16.95 L 19.07 19.07
            M 2 12 L 5 12 M 19 12 L 22 12 M 4.93 19.07 L 7.05 16.95 M 16.95 7.05 L 19.07 4.93"/>
  </Ico>
);

const IcoHistory = (p) => (
  <Ico {...p} title="History">
    <path d="M 3 12 A 9 9 0 1 0 6 5.5"/>
    <polyline points="3 5 3 9 7 9"/>
    <polyline points="12 7 12 12 15.5 14"/>
  </Ico>
);

const IcoReview = (p) => (
  <Ico {...p} title="Review">
    <rect x="4" y="3" width="16" height="18" rx="1"/>
    <line x1="8" y1="8" x2="16" y2="8"/>
    <line x1="8" y1="12" x2="16" y2="12"/>
    <line x1="8" y1="16" x2="13" y2="16"/>
    <circle cx="17" cy="16" r="2" fill="currentColor" stroke="none" opacity="0.8"/>
  </Ico>
);

const IcoDashboard = (p) => (
  <Ico {...p} title="Dashboard">
    <rect x="3" y="3" width="8" height="10"/>
    <rect x="13" y="3" width="8" height="5"/>
    <rect x="13" y="10" width="8" height="11"/>
    <rect x="3" y="15" width="8" height="6"/>
  </Ico>
);

const IcoSearch = (p) => (
  <Ico {...p} title="Search">
    <circle cx="10" cy="10" r="6"/>
    <line x1="14.5" y1="14.5" x2="20" y2="20"/>
  </Ico>
);

const IcoFilter = (p) => (
  <Ico {...p} title="Filter">
    <path d="M 3 5 L 21 5 L 14 13 L 14 20 L 10 18 L 10 13 Z"/>
  </Ico>
);

const IcoMore = (p) => (
  <Ico {...p} title="More">
    <circle cx="5" cy="12" r="1.5" fill="currentColor"/>
    <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
    <circle cx="19" cy="12" r="1.5" fill="currentColor"/>
  </Ico>
);

const IcoConfidence = (p) => (
  <Ico {...p} title="Confidence">
    <path d="M 4 14 L 9 9 L 13 13 L 20 6"/>
    <polyline points="15 6 20 6 20 11"/>
  </Ico>
);

const IcoBytes = (p) => (
  <Ico {...p} title="Bytes">
    <rect x="3" y="9" width="6" height="6"/>
    <rect x="11" y="9" width="6" height="6"/>
    <rect x="19" y="9" width="2" height="6"/>
    <line x1="3" y1="6" x2="21" y2="6" opacity="0.5"/>
  </Ico>
);

// All icons with metadata for rendering the icon grid
const ICONS = {
  status: [
    ['idle',     IcoIdle],
    ['scan',     IcoScan],
    ['ripping',  IcoRipping],
    ['matching', IcoMatching],
    ['complete', IcoComplete],
    ['paused',   IcoPaused],
    ['queued',   IcoQueued],
    ['error',    IcoError],
  ],
  media: [
    ['disc',    IcoDisc],
    ['blu-ray', IcoBluRay],
    ['dvd',     IcoDvd],
    ['tv',      IcoTv],
    ['movie',   IcoMovie],
    ['episode', IcoEpisode],
    ['drive',   IcoDrive],
    ['library', IcoLibrary],
  ],
  action: [
    ['dashboard', IcoDashboard],
    ['history',   IcoHistory],
    ['review',    IcoReview],
    ['settings',  IcoSettings],
    ['search',    IcoSearch],
    ['filter',    IcoFilter],
    ['play',      IcoPlay],
    ['pause',     IcoPause],
    ['cancel',    IcoCancel],
    ['retry',     IcoRetry],
    ['eject',     IcoEject],
    ['more',      IcoMore],
    ['confidence',IcoConfidence],
    ['bytes',     IcoBytes],
  ],
};

Object.assign(window, {
  Ico, ICONS,
  IcoIdle, IcoScan, IcoRipping, IcoMatching, IcoComplete, IcoError, IcoPaused, IcoQueued,
  IcoDisc, IcoBluRay, IcoDvd, IcoTv, IcoMovie, IcoEpisode, IcoLibrary, IcoDrive,
  IcoPlay, IcoPause, IcoCancel, IcoRetry, IcoEject, IcoSettings, IcoHistory, IcoReview,
  IcoDashboard, IcoSearch, IcoFilter, IcoMore, IcoConfidence, IcoBytes,
});

/* Shared data, utilities, and sample state that all three Engram directions render from.
   Keep this DUMB — pure data + helpers. Visuals live in each direction file. */

const SAMPLE_JOB = {
  title: 'ARRESTED DEVELOPMENT',
  label: 'ARRESTED_DEVELOPMENT_S1D1',
  mediaType: 'TV',
  season: 1,
  disc: 1,
  speedX: 7.6,
  speedMBs: 34.4,
  etaMin: 4,
  overall: 0.057,
  tracksDone: 0,
  tracksTotal: 8,
};

const SAMPLE_TRACKS = [
  { id: 0, runtime: '22:00', state: 'ripping', progress: 0.40, size: 409.6, total: 1000, ep: null, confidence: null },
  { id: 1, runtime: '21:30', state: 'queued',  progress: 0,    size: 0,     total: 1000, ep: null, confidence: null },
  { id: 2, runtime: '22:30', state: 'queued',  progress: 0,    size: 0,     total: 1020, ep: null, confidence: null },
  { id: 3, runtime: '21:20', state: 'queued',  progress: 0,    size: 0,     total: 970,  ep: null, confidence: null },
  { id: 4, runtime: '21:50', state: 'queued',  progress: 0,    size: 0,     total: 990,  ep: null, confidence: null },
  { id: 5, runtime: '22:20', state: 'queued',  progress: 0,    size: 0,     total: 1020, ep: null, confidence: null },
  { id: 6, runtime: '21:40', state: 'queued',  progress: 0,    size: 0,     total: 980,  ep: null, confidence: null },
  { id: 7, runtime: '22:10', state: 'queued',  progress: 0,    size: 0,     total: 1010, ep: null, confidence: null },
];

const MATCHING_TRACKS = [
  { id: 0, runtime: '22:00', state: 'matched',  ep: 'S01E01', confidence: 0.97 },
  { id: 1, runtime: '21:30', state: 'matching', progress: 0.75, ep: null, candidates: [
      { ep: 'S01E02', score: 0.78 }, { ep: 'S01E03', score: 0.52 }, { ep: 'S01E04', score: 0.48 },
  ]},
  { id: 2, runtime: '22:30', state: 'pending', ep: null },
  { id: 3, runtime: '21:20', state: 'pending', ep: null },
  { id: 4, runtime: '21:50', state: 'pending', ep: null },
  { id: 5, runtime: '22:20', state: 'pending', ep: null },
  { id: 6, runtime: '21:40', state: 'pending', ep: null },
  { id: 7, runtime: '22:10', state: 'pending', ep: null },
];

const WIZARD_STEPS = [
  { id: 'paths',  label: 'PATHS',  desc: 'Library locations' },
  { id: 'tools',  label: 'TOOLS',  desc: 'MakeMKV + ffmpeg' },
  { id: 'tmdb',   label: 'TMDB',   desc: 'Metadata API key' },
  { id: 'prefs',  label: 'PREFS',  desc: 'Auto-rip & review' },
];

/* Nice little helpers */
function pct(n) { return (Math.round(n * 1000) / 10).toFixed(1) + '%'; }
function mbOf(n) { return n.toFixed(1) + ' MB'; }
function gbOf(n) { return (n / 1000).toFixed(1) + ' GB'; }

Object.assign(window, { SAMPLE_JOB, SAMPLE_TRACKS, MATCHING_TRACKS, WIZARD_STEPS, pct, mbOf, gbOf });

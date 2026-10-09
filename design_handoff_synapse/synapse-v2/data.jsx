/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — EXTENDED DATA
   Richer sample content for library browser, history, review queue.
   ═══════════════════════════════════════════════════════════════════════════ */

const SV_LIBRARY = [
  { id:'blade-runner-2049', type:'movie', title:'Blade Runner 2049', year:2017, runtime:'2h 43m', size:'8.2 GB', quality:'BD50 · 1080p', added:'2 days ago', color:'cyan', poster:'BR' },
  { id:'arrested-dev-s1',    type:'tv',    title:'Arrested Development', subtitle:'Season 01', year:2003, episodes:8, size:'7.8 GB', quality:'BD50 · 1080p', added:'just now',    color:'magenta', poster:'AD' },
  { id:'akira',              type:'movie', title:'Akira',              year:1988, runtime:'2h 04m', size:'6.4 GB', quality:'BD50 · 1080p', added:'1 week ago', color:'yellow',  poster:'AK' },
  { id:'ghost-shell',        type:'movie', title:'Ghost in the Shell', year:1995, runtime:'1h 23m', size:'4.2 GB', quality:'BD25 · 1080p', added:'3 weeks ago',color:'cyan',    poster:'GS' },
  { id:'dune-pt2',           type:'movie', title:'Dune: Part Two',     year:2024, runtime:'2h 46m', size:'12.1 GB',quality:'UHD · 2160p', added:'1 month ago',color:'magenta', poster:'D2' },
  { id:'severance-s1',       type:'tv',    title:'Severance', subtitle:'Season 01', year:2022, episodes:9, size:'14.2 GB',quality:'UHD · 2160p',added:'2 months ago',color:'cyan', poster:'SV' },
  { id:'tron-legacy',        type:'movie', title:'Tron: Legacy',       year:2010, runtime:'2h 05m', size:'8.9 GB', quality:'BD50 · 1080p', added:'2 months ago',color:'yellow', poster:'TR' },
  { id:'mr-robot-s1',        type:'tv',    title:'Mr. Robot', subtitle:'Season 01', year:2015, episodes:10, size:'18.6 GB',quality:'BD50 · 1080p',added:'3 months ago',color:'magenta',poster:'MR' },
];

const SV_HISTORY = [
  { id:1, at:'00:00:21', tag:'WORK', color:'cyan',    msg:'Spawned makemkv · title 0 · 1/8',          title:'Arrested Development' },
  { id:2, at:'00:00:20', tag:'INFO', color:'green',   msg:'TMDB match · Arrested Development (2003) · conf 0.94', title:'Arrested Development' },
  { id:3, at:'00:00:19', tag:'INFO', color:'green',   msg:'Classified · tv · 8 titles · ~22 min',      title:'Arrested Development' },
  { id:4, at:'00:00:18', tag:'INFO', color:'cyan',    msg:'Disc inserted · drive E:\\ · BD50 · 46.2 GB', title:'Arrested Development' },
  { id:5, at:'Jan 14',   tag:'DONE', color:'green',   msg:'Archived 1 motion picture · 8.2 GB · 12m04s', title:'Blade Runner 2049' },
  { id:6, at:'Jan 14',   tag:'INFO', color:'cyan',    msg:'Disc inserted · drive E:\\ · BD50',           title:'Blade Runner 2049' },
  { id:7, at:'Jan 09',   tag:'WARN', color:'yellow',  msg:'Ambiguous match · resolved to S01E03 by user', title:'Akira' },
  { id:8, at:'Jan 09',   tag:'DONE', color:'green',   msg:'Archived 1 motion picture · 6.4 GB',          title:'Akira' },
];

const SV_REVIEW_QUEUE = [
  { id:'q1', title:'Cowboy Bebop · Disc 2', type:'tv', need:'Ambiguous track matches',     tracks:7, flagged:3, state:'warn' },
  { id:'q2', title:'The Matrix',            type:'movie', need:'Multiple feature candidates', tracks:5, flagged:2, state:'warn' },
  { id:'q3', title:'Ghost in the Shell',    type:'movie', need:'No TMDB match',              tracks:3, flagged:3, state:'error' },
];

// candidate list for the track-review detail — one track, multiple episode guesses
const SV_CANDIDATES = [
  { ep:'S01E02 · Top Banana',         score: 0.78, sources:['Audio FP · 0.82', 'Runtime · 0.74', 'Chapter count · 0.78'] },
  { ep:'S01E03 · Bringing Up Buster', score: 0.52, sources:['Audio FP · 0.44', 'Runtime · 0.72', 'Chapter count · 0.40'] },
  { ep:'S01E04 · Key Decisions',      score: 0.48, sources:['Audio FP · 0.40', 'Runtime · 0.68', 'Chapter count · 0.36'] },
  { ep:'S01E05 · Visiting Ours',      score: 0.31, sources:['Audio FP · 0.25', 'Runtime · 0.52', 'Chapter count · 0.16'] },
];

const SV_TELEMETRY_STRINGS = [
  'UNIT 07', 'SESSION 01', 'WS·CONNECTED', 'v0.6.0', 'DRIVE E:', 'BUFFER 1.2/8.0 GB',
  'CPU 34%', 'GPU IDLE', 'THERMAL NOMINAL', 'TMDB · ONLINE', 'TRAKT · ONLINE',
  'AUDIO FP · READY', 'CHAPTER ANALYZER · READY', 'LIBRARY · 23 TITLES · 142 GB',
];

Object.assign(window, { SV_LIBRARY, SV_HISTORY, SV_REVIEW_QUEUE, SV_CANDIDATES, SV_TELEMETRY_STRINGS });

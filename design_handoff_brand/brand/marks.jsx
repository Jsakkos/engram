/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Marks
   The primary brand mark and its variants. Concept: an engram is a memory
   trace. We draw it as concentric rings (think hippocampal replay, or disc
   tracks) intersected by a single read-line + node — the "trace" being made.
   The mark is also an "E" hidden in plain sight: the broken rings form three
   open arcs facing right.
   ═══════════════════════════════════════════════════════════════════════════ */

const M = brandTokens;

// ── Primary mark ─────────────────────────────────────────────────────────────
// Three open arcs (facing right — the implicit "E") with a single dendrite
// extending from the inner core to an offset node. Scales cleanly to 16px.
function Mark({ size = 48, primary, secondary, glow = true, monochrome = false, paper = false }) {
  const id = React.useId().replace(/:/g,'-');
  const p = primary  || (paper ? M.inkOnPaper : M.cyan);
  const s = secondary || (paper ? M.inkOnPaper : M.magenta);
  const sw = monochrome ? p : s;
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} style={{display:'block', overflow:'visible'}}>
      <defs>
        <radialGradient id={`mk-g-${id}`} cx="0.5" cy="0.5" r="0.55">
          <stop offset="0%"   stopColor={p} stopOpacity={glow ? 0.22 : 0}/>
          <stop offset="70%"  stopColor={p} stopOpacity="0"/>
        </radialGradient>
      </defs>
      {glow && <circle cx="32" cy="32" r="30" fill={`url(#mk-g-${id})`}/>}

      {/* Three concentric open arcs — break facing right (270°→90° clockwise) */}
      {/* Outer arc */}
      <path d="M 32 8 A 24 24 0 1 0 32 56"
        fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round"/>
      {/* Mid arc */}
      <path d="M 32 16 A 16 16 0 1 0 32 48"
        fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round" opacity="0.78"/>
      {/* Inner arc */}
      <path d="M 32 24 A 8 8 0 1 0 32 40"
        fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round" opacity="0.55"/>

      {/* Read-line crossing the break, terminating in a node — the "trace" */}
      <line x1="32" y1="32" x2="56" y2="32"
        stroke={sw} strokeWidth="2.5" strokeLinecap="round"/>
      <circle cx="56" cy="32" r="3.5" fill={sw}/>
      {glow && <circle cx="56" cy="32" r="6.5" fill={sw} opacity="0.18"/>}
    </svg>
  );
}

// ── Wordmark ────────────────────────────────────────────────────────────────
function Wordmark({ size = 56, color, letterSpacing = '0.14em', tracking, paper = false }) {
  return (
    <span style={{
      fontFamily: M.display, fontWeight: 700, fontSize: size,
      letterSpacing: tracking || letterSpacing,
      color: color || (paper ? M.inkOnPaper : M.ink),
      lineHeight: 1, textTransform:'uppercase', whiteSpace:'nowrap',
    }}>ENGRAM</span>
  );
}

// ── Horizontal lockup ───────────────────────────────────────────────────────
function LockupH({ size = 56, color, paper = false, glow = true }) {
  return (
    <div style={{display:'inline-flex', alignItems:'center', gap: size * 0.34}}>
      <Mark size={size * 1.18} paper={paper} glow={glow}/>
      <Wordmark size={size} color={color} paper={paper}/>
    </div>
  );
}

// ── Stacked lockup ──────────────────────────────────────────────────────────
function LockupV({ size = 64, color, paper = false, glow = true }) {
  return (
    <div style={{display:'inline-flex', flexDirection:'column', alignItems:'center', gap: size * 0.30}}>
      <Mark size={size * 1.6} paper={paper} glow={glow}/>
      <Wordmark size={size} color={color} paper={paper} tracking={`${size * 0.0028}em`}/>
    </div>
  );
}

// ── Mark + descriptor (the formal lockup) ───────────────────────────────────
function LockupDescriptor({ size = 56, color, paper = false, descriptor = 'MEDIA ARCHIVE' }) {
  return (
    <div style={{display:'inline-flex', alignItems:'center', gap: size * 0.34}}>
      <Mark size={size * 1.18} paper={paper}/>
      <div style={{display:'flex', flexDirection:'column', gap: size * 0.10}}>
        <Wordmark size={size} color={color} paper={paper}/>
        <div style={{
          fontFamily: M.mono, fontSize: size * 0.20, letterSpacing: '0.34em',
          color: paper ? M.inkOnPaperDim : M.inkDim,
          paddingLeft: 2,
        }}>{descriptor}</div>
      </div>
    </div>
  );
}

// ── Monogram — the standalone "E" mark for very small surfaces ───────────────
// Drops the dendrite, keeps the arcs. For favicons + dock badges.
function MarkMono({ size = 48, color, paper = false, glow = false }) {
  const id = React.useId().replace(/:/g,'-');
  const p = color || (paper ? M.inkOnPaper : M.cyan);
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} style={{display:'block'}}>
      <defs>
        <radialGradient id={`mm-g-${id}`} cx="0.5" cy="0.5" r="0.55">
          <stop offset="0%" stopColor={p} stopOpacity={glow ? 0.25 : 0}/>
          <stop offset="70%" stopColor={p} stopOpacity="0"/>
        </radialGradient>
      </defs>
      {glow && <circle cx="32" cy="32" r="30" fill={`url(#mm-g-${id})`}/>}
      <path d="M 32 8 A 24 24 0 1 0 32 56"
        fill="none" stroke={p} strokeWidth="3" strokeLinecap="round"/>
      <path d="M 32 16 A 16 16 0 1 0 32 48"
        fill="none" stroke={p} strokeWidth="3" strokeLinecap="round" opacity="0.78"/>
      <path d="M 32 24 A 8 8 0 1 0 32 40"
        fill="none" stroke={p} strokeWidth="3" strokeLinecap="round" opacity="0.55"/>
    </svg>
  );
}

// ── App icon — rounded square containing the mark ───────────────────────────
function AppIcon({ size = 128, radius, dark = true, glow = true }) {
  const r = radius ?? Math.round(size * 0.2237); // macOS squircle radius
  const inset = size * 0.18;
  return (
    <div style={{
      width: size, height: size, borderRadius: r, overflow:'hidden',
      position:'relative',
      background: dark
        ? `radial-gradient(ellipse at 30% 20%, #102031, #05070c 60%, #02030a)`
        : M.paper,
      boxShadow: dark
        ? `0 ${size*0.04}px ${size*0.12}px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(94,234,212,0.18)`
        : `0 ${size*0.04}px ${size*0.12}px rgba(0,0,0,0.18), inset 0 0 0 1px rgba(0,0,0,0.06)`,
    }}>
      {/* faint ring grid hinting at the disc + scanlines */}
      <svg viewBox="0 0 128 128" style={{position:'absolute', inset:0, width:'100%', height:'100%'}}>
        {dark && [50,40,30,20].map((rr, i) => (
          <circle key={rr} cx="64" cy="64" r={rr} fill="none" stroke={M.cyan}
            strokeWidth="0.4" opacity={0.04 + i*0.02}/>
        ))}
      </svg>
      {dark && (
        <div style={{position:'absolute', inset:0, opacity: 0.18,
          backgroundImage:'repeating-linear-gradient(0deg, rgba(94,234,212,0.18) 0 1px, transparent 1px 4px)'}}/>
      )}
      {/* corner tick — borrowed from Synapse v2 chrome */}
      <BIconCorners size={size} color={dark ? 'rgba(94,234,212,0.42)' : 'rgba(0,0,0,0.25)'}/>
      <div style={{position:'absolute', inset: inset, display:'flex',
        alignItems:'center', justifyContent:'center'}}>
        <Mark size={size - inset*2} glow={glow}
          primary={dark ? M.cyan : M.inkOnPaper}
          secondary={dark ? M.magenta : M.inkOnPaper}
        />
      </div>
      {/* tiny version stamp at bottom corner — only on larger icons */}
      {size >= 96 && dark && (
        <div style={{position:'absolute', bottom: size*0.06, right: size*0.08,
          fontFamily: M.mono, fontSize: size*0.05, letterSpacing:'0.18em',
          color: 'rgba(230,236,245,0.32)'}}>v1</div>
      )}
    </div>
  );
}

function BIconCorners({ size, color }) {
  const inset = size * 0.06;
  const tick = size * 0.05;
  const s = { position:'absolute', width: tick, height: tick, borderColor: color,
    borderStyle:'solid', pointerEvents:'none' };
  return (
    <>
      <div style={{...s, top: inset, left: inset, borderWidth:`1.2px 0 0 1.2px`}}/>
      <div style={{...s, top: inset, right: inset, borderWidth:`1.2px 1.2px 0 0`}}/>
      <div style={{...s, bottom: inset, left: inset, borderWidth:`0 0 1.2px 1.2px`}}/>
      <div style={{...s, bottom: inset, right: inset, borderWidth:`0 1.2px 1.2px 0`}}/>
    </>
  );
}

// Animated mark — used in splash + loading states. Mark slowly rotates,
// node pulses, scanline sweeps across.
function MarkAnimated({ size = 96, color, secondary }) {
  const p = color || M.cyan;
  const s = secondary || M.magenta;
  return (
    <div style={{width: size, height: size, position:'relative'}}>
      <svg viewBox="0 0 64 64" width={size} height={size}
        style={{display:'block', position:'absolute', inset: 0,
          animation: 'engSpin 14s linear infinite'}}>
        <path d="M 32 8 A 24 24 0 1 0 32 56"
          fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round"/>
        <path d="M 32 16 A 16 16 0 1 0 32 48"
          fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round" opacity="0.78"/>
        <path d="M 32 24 A 8 8 0 1 0 32 40"
          fill="none" stroke={p} strokeWidth="2.5" strokeLinecap="round" opacity="0.55"/>
      </svg>
      <svg viewBox="0 0 64 64" width={size} height={size}
        style={{display:'block', position:'absolute', inset: 0}}>
        <line x1="32" y1="32" x2="56" y2="32" stroke={s} strokeWidth="2.5" strokeLinecap="round"/>
        <circle cx="56" cy="32" r="3.5" fill={s}>
          <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite"/>
        </circle>
        <circle cx="56" cy="32" r="6.5" fill={s} opacity="0.18">
          <animate attributeName="r" values="6.5;10;6.5" dur="1.6s" repeatCount="indefinite"/>
        </circle>
      </svg>
    </div>
  );
}

// inject animation
if (typeof document !== 'undefined' && !document.getElementById('eng-mark-anim')) {
  const s = document.createElement('style');
  s.id = 'eng-mark-anim';
  s.textContent = `
    @keyframes engSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    @keyframes engBlink { 0%,49%,100% { opacity: 1; } 50%,99% { opacity: 0.2; } }
  `;
  document.head.appendChild(s);
}

Object.assign(window, {
  Mark, MarkMono, MarkAnimated,
  Wordmark, LockupH, LockupV, LockupDescriptor,
  AppIcon, BIconCorners,
});

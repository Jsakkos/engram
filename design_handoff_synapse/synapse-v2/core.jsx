/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — CORE
   Shared tokens, atmosphere wrapper, and primitives for the expanded Synapse
   direction. Supports three context modes that cascade into every component:
     · density       — 'min' | 'med' | 'dense'
     · colorBalance  — 'balanced' | 'cyan' | 'magenta'
     · scanlines     — bool
   These flow via React.Context; read with useSv().
   ═══════════════════════════════════════════════════════════════════════════ */

const sv = {
  bg0:   '#05070c',
  bg1:   '#0a0e18',
  bg2:   '#121827',
  bg3:   '#1a2234',
  ink:   '#e6ecf5',
  inkDim:'#8893a8',
  inkFaint:'#4a5369',
  inkGhost:'#2a3147',
  cyan:   '#5eead4',
  cyanHi: '#9ff8e8',
  cyanDim:'#2dd4bf',
  magenta:'#ff3d7f',
  magentaHi:'#ff7aa5',
  magentaDim:'#d63171',
  yellow: '#fde047',       // third accent — electric yellow
  yellowDim:'#d4b822',
  amber:  '#fcd34d',
  green:  '#86efac',
  greenDim:'#4ade80',
  red:    '#ff5555',
  purple: '#a78bfa',
  line:   'rgba(94,234,212,0.14)',
  lineMid:'rgba(94,234,212,0.24)',
  lineHi: 'rgba(94,234,212,0.42)',
  mono:   '"JetBrains Mono", ui-monospace, monospace',
  sans:   '"Chakra Petch", "Space Grotesk", sans-serif',
  display:'"Chakra Petch", sans-serif',
};

const SvCtx = React.createContext({
  density: 'med',
  colorBalance: 'balanced',
  scanlines: true,
  skyline: true,
});

const useSv = () => React.useContext(SvCtx);

// compute accent color given balance
function useAccent() {
  const { colorBalance } = useSv();
  if (colorBalance === 'magenta') return { primary: sv.magenta, primaryHi: sv.magentaHi, secondary: sv.cyan, secondaryHi: sv.cyanHi };
  if (colorBalance === 'cyan')    return { primary: sv.cyan,    primaryHi: sv.cyanHi,    secondary: sv.magenta, secondaryHi: sv.magentaHi };
  return { primary: sv.cyan, primaryHi: sv.cyanHi, secondary: sv.magenta, secondaryHi: sv.magentaHi };
}

// density scale — returns paddings / font sizes by context
function useDensity() {
  const { density } = useSv();
  if (density === 'min') return {
    pad: 32, gap: 20, fontBase: 13, labelSize: 11, monoSize: 11,
    cardPad: 22, rowHeight: 52, headerPad: 24,
  };
  if (density === 'dense') return {
    pad: 14, gap: 8, fontBase: 11, labelSize: 9, monoSize: 10,
    cardPad: 10, rowHeight: 30, headerPad: 12,
  };
  return {
    pad: 22, gap: 14, fontBase: 12, labelSize: 10, monoSize: 10,
    cardPad: 14, rowHeight: 40, headerPad: 18,
  };
}

// ── atmospheric wrap ────────────────────────────────────────────────────────
function SvAtmosphere({ children, skylineOverride, scanlineOverride }) {
  const { scanlines, skyline } = useSv();
  const showSkyline = skylineOverride ?? skyline;
  const showScan = scanlineOverride ?? scanlines;
  return (
    <div style={{position:'relative', width:'100%', height:'100%', background: sv.bg0, overflow:'hidden'}}>
      {/* ambient haze */}
      <div style={{position:'absolute', inset: 0, pointerEvents:'none', backgroundImage:
        'radial-gradient(ellipse at 15% 20%, rgba(94,234,212,0.10), transparent 55%),' +
        'radial-gradient(ellipse at 85% 90%, rgba(255,61,127,0.07), transparent 50%)'}}/>
      {showSkyline && <SvSkyline/>}
      {/* content */}
      <div style={{position:'relative', zIndex: 2, width:'100%', height:'100%'}}>{children}</div>
      {/* scanlines overlay */}
      {showScan && (
        <div style={{position:'absolute', inset:0, pointerEvents:'none', zIndex: 3, opacity: 0.35,
          backgroundImage:'repeating-linear-gradient(0deg, rgba(94,234,212,0.05) 0 1px, transparent 1px 3px)'}}/>
      )}
      {/* subtle grain */}
      <svg style={{position:'absolute', inset:0, width:'100%', height:'100%',
        pointerEvents:'none', opacity: 0.08, mixBlendMode:'overlay', zIndex: 4}}>
        <filter id="sv-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch"/>
        </filter>
        <rect width="100%" height="100%" filter="url(#sv-grain)"/>
      </svg>
      {/* vignette */}
      <div style={{position:'absolute', inset: 0, pointerEvents:'none', zIndex: 5,
        background: 'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.5) 100%)'}}/>
    </div>
  );
}

// distant Tokyo skyline silhouette — low, atmospheric, at the very bottom
function SvSkyline() {
  return (
    <svg viewBox="0 0 1280 200" preserveAspectRatio="none"
      style={{position:'absolute', bottom: 0, left: 0, width:'100%', height: 180,
        pointerEvents:'none', zIndex: 1, opacity: 0.55}}>
      <defs>
        <linearGradient id="sv-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(94,234,212,0.0)"/>
          <stop offset="70%" stopColor="rgba(94,234,212,0.1)"/>
          <stop offset="100%" stopColor="rgba(255,61,127,0.2)"/>
        </linearGradient>
        <linearGradient id="sv-tower" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a0e18"/>
          <stop offset="100%" stopColor="#020308"/>
        </linearGradient>
      </defs>
      {/* rear rank — hazy */}
      <g opacity="0.4">
        {[...Array(40)].map((_, i) => {
          const x = i * 32;
          const h = 30 + ((i * 37) % 80);
          return <rect key={`r${i}`} x={x} y={200 - h} width="28" height={h} fill="#0a0e18"/>;
        })}
      </g>
      {/* mid rank */}
      <g opacity="0.75">
        {[...Array(24)].map((_, i) => {
          const x = i * 54 + 10;
          const h = 50 + ((i * 53) % 100);
          const w = 44 + (i % 3) * 8;
          return (
            <g key={`m${i}`}>
              <rect x={x} y={200 - h} width={w} height={h} fill="url(#sv-tower)"/>
              {/* window lights */}
              {[...Array(Math.floor(h/8))].map((_, j) => (
                (i + j) % 4 === 0 && (
                  <rect key={j} x={x + 3 + ((j*7)%(w-6))} y={200 - h + 4 + j*8}
                    width="2" height="1.5" fill={(j+i)%7===0 ? sv.magenta : sv.cyan} opacity="0.6"/>
                )
              ))}
            </g>
          );
        })}
      </g>
      {/* foreground spires */}
      <g>
        {[{x:120,h:170,w:18},{x:480,h:180,w:24},{x:820,h:160,w:20},{x:1100,h:175,w:22}].map((t, i) => (
          <g key={`f${i}`}>
            <rect x={t.x} y={200 - t.h} width={t.w} height={t.h} fill="#05070c"/>
            <circle cx={t.x + t.w/2} cy={200 - t.h - 4} r="1.5" fill={sv.magenta}>
              <animate attributeName="opacity" values="1;0.3;1" dur="2s" repeatCount="indefinite"/>
            </circle>
          </g>
        ))}
      </g>
      {/* horizon glow */}
      <rect x="0" y="0" width="1280" height="200" fill="url(#sv-sky)"/>
    </svg>
  );
}

// ── kinetic type primitives ─────────────────────────────────────────────────
// scrolling telemetry band — a strip of mono text that scrolls horizontally
function SvTelemetryBand({ items, speed = 80, color }) {
  const doubled = [...items, ...items];
  return (
    <div style={{overflow:'hidden', width:'100%', position:'relative',
      maskImage:'linear-gradient(90deg, transparent 0, black 5%, black 95%, transparent 100%)',
      WebkitMaskImage:'linear-gradient(90deg, transparent 0, black 5%, black 95%, transparent 100%)'}}>
      <div style={{display:'inline-flex', whiteSpace:'nowrap',
        animation: `svScroll ${speed}s linear infinite`}}>
        {doubled.map((item, i) => (
          <span key={i} style={{fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em',
            color: color || sv.inkFaint, padding:'0 24px'}}>
            {item} <span style={{color: sv.inkGhost}}>·</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// glyph scrambler — briefly reveals with a scramble effect on mount
function SvScramble({ text, color, size = 12, duration = 400 }) {
  const [display, setDisplay] = React.useState(text);
  React.useEffect(() => {
    const chars = '01!@#$%^&*<>ABCDEF0123456789';
    const steps = 10;
    let i = 0;
    const tick = () => {
      i++;
      if (i >= steps) { setDisplay(text); return; }
      const frac = i / steps;
      const cut = Math.floor(text.length * frac);
      let next = '';
      for (let j = 0; j < text.length; j++) {
        next += j < cut ? text[j] : (text[j] === ' ' ? ' ' : chars[Math.floor(Math.random()*chars.length)]);
      }
      setDisplay(next);
      setTimeout(tick, duration / steps);
    };
    tick();
  }, [text]);
  return (
    <span style={{fontFamily: sv.mono, fontSize: size, color: color || sv.cyan, letterSpacing:'0.14em'}}>
      {display}
    </span>
  );
}

// ── panel + ruler + corner ticks ────────────────────────────────────────────
function SvPanel({ children, style, accent, pad, glow = false }) {
  const d = useDensity();
  const border = accent || sv.lineMid;
  return (
    <div style={{
      background: 'linear-gradient(180deg, rgba(18,24,39,0.75), rgba(10,14,24,0.85))',
      border: `1px solid ${border}`,
      padding: pad ?? d.cardPad,
      position:'relative',
      boxShadow: glow ? `0 0 24px ${accent||sv.cyan}22, inset 0 0 32px rgba(94,234,212,0.03)` : 'inset 0 0 24px rgba(94,234,212,0.02)',
      ...style,
    }}>
      <SvCorners color={accent ? `${accent}aa` : sv.lineHi}/>
      {children}
    </div>
  );
}

function SvCorners({ color = sv.lineHi, size = 8 }) {
  const s = { position:'absolute', width:size, height:size, borderColor: color,
    borderStyle:'solid', pointerEvents:'none' };
  return (
    <>
      <div style={{...s, top: -1, left: -1, borderWidth:'1.5px 0 0 1.5px'}}/>
      <div style={{...s, top: -1, right: -1, borderWidth:'1.5px 1.5px 0 0'}}/>
      <div style={{...s, bottom: -1, left: -1, borderWidth:'0 0 1.5px 1.5px'}}/>
      <div style={{...s, bottom: -1, right: -1, borderWidth:'0 1.5px 1.5px 0'}}/>
    </>
  );
}

function SvLabel({ children, color, style, caret = true }) {
  const d = useDensity();
  const c = color || sv.inkDim;
  return (
    <div style={{
      fontFamily: sv.mono, fontSize: d.labelSize, letterSpacing:'0.2em',
      textTransform:'uppercase', color: c,
      display:'inline-flex', alignItems:'center', gap: 6, ...style,
    }}>
      {caret && <span style={{color: color || sv.cyan}}>›</span>}
      {children}
    </div>
  );
}

function SvBar({ value, height = 3, color, track, glow = true }) {
  const a = useAccent();
  const c = color || a.primary;
  const t = track || 'rgba(94,234,212,0.06)';
  return (
    <div style={{position:'relative', height, background: t, overflow:'hidden'}}>
      <div style={{position:'absolute', inset:0, width: `${Math.min(1,Math.max(0,value))*100}%`,
        background: `linear-gradient(90deg, ${c}, ${a.secondary})`,
        boxShadow: glow ? `0 0 10px ${c}` : 'none',
        transition: 'width 0.3s ease',
      }}/>
      <div style={{position:'absolute', inset:0, backgroundImage:
        'repeating-linear-gradient(90deg, transparent 0 calc(10% - 1px), rgba(0,0,0,0.4) calc(10% - 1px) 10%)'}}/>
    </div>
  );
}

function SvBadge({ state, children, style }) {
  const d = useDensity();
  const map = {
    ripping:  { fg: sv.magenta, bg: 'rgba(255,61,127,0.1)', dot: sv.magenta },
    matching: { fg: sv.amber,   bg: 'rgba(252,211,77,0.1)',  dot: sv.amber },
    complete: { fg: sv.green,   bg: 'rgba(134,239,172,0.1)', dot: sv.green },
    queued:   { fg: sv.inkDim, bg: 'rgba(124,134,153,0.08)', dot: sv.inkDim },
    error:    { fg: sv.red,    bg: 'rgba(255,85,85,0.1)',    dot: sv.red },
    live:     { fg: sv.green,  bg: 'rgba(134,239,172,0.1)',  dot: sv.green },
    scanning: { fg: sv.yellow, bg: 'rgba(253,224,71,0.1)',   dot: sv.yellow },
    warn:     { fg: sv.yellow, bg: 'rgba(253,224,71,0.1)',   dot: sv.yellow },
  };
  const c = map[state] || map.queued;
  return (
    <span style={{
      display:'inline-flex', alignItems:'center', gap: 6,
      fontFamily: sv.mono, fontSize: d.labelSize, letterSpacing:'0.2em', textTransform:'uppercase',
      color: c.fg, background: c.bg, padding: '4px 10px',
      border: `1px solid ${c.fg}30`, ...style,
    }}>
      <span style={{width: 6, height: 6, borderRadius:'50%', background: c.dot,
        boxShadow: `0 0 8px ${c.dot}`,
        animation: (state==='ripping'||state==='matching'||state==='scanning')?'svPulse 1.2s infinite':'none'}}/>
      {children}
    </span>
  );
}

function SvRuler({ color, ticks = 40 }) {
  const c = color || sv.lineMid;
  return (
    <svg width="100%" height="8" style={{display:'block'}} preserveAspectRatio="none">
      <line x1="0" y1="4" x2="100%" y2="4" stroke={c} strokeWidth="0.5"/>
      {Array.from({length: ticks + 1}, (_, i) => {
        const x = `${(i/ticks)*100}%`;
        const major = i % 5 === 0;
        return <line key={i} x1={x} y1={major?0:2} x2={x} y2={major?8:6} stroke={c} strokeWidth="0.5"/>;
      })}
    </svg>
  );
}

// animated value — counts up toward target smoothly; for live ripping %
function SvAnimValue({ target, fmt = v => pct(v), interval = 1000 }) {
  const [v, setV] = React.useState(target);
  React.useEffect(() => {
    setV(target);
    const id = setInterval(() => {
      setV(prev => Math.min(1, prev + 0.001 + Math.random()*0.002));
    }, interval);
    return () => clearInterval(id);
  }, [target, interval]);
  return <>{fmt(v)}</>;
}

// ── logo wordmark ───────────────────────────────────────────────────────────
function SvMark({ size = 32, color, hue = 'cyan' }) {
  const c = color || (hue === 'magenta' ? sv.magenta : sv.cyan);
  const c2 = hue === 'magenta' ? sv.cyan : sv.magenta;
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} style={{display:'block'}}>
      <defs>
        <radialGradient id={`sv-m-${hue}`} cx="0.5" cy="0.5" r="0.5">
          <stop offset="0%" stopColor={c} stopOpacity="0.3"/>
          <stop offset="100%" stopColor={c} stopOpacity="0"/>
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="26" fill={`url(#sv-m-${hue})`}/>
      {[28, 20, 12].map((r, i) => (
        <circle key={r} cx="32" cy="32" r={r} fill="none" stroke={c}
          strokeWidth={i===0?1.2:0.6} opacity={1 - i*0.25}/>
      ))}
      <circle cx="32" cy="32" r="3" fill={c}/>
      {/* axon */}
      <line x1="32" y1="32" x2="60" y2="8" stroke={c2} strokeWidth="1"/>
      <circle cx="60" cy="8" r="2.5" fill={c2}/>
    </svg>
  );
}

// ── keyframes (injected once) ───────────────────────────────────────────────
if (typeof document !== 'undefined' && !document.getElementById('sv-anim')) {
  const s = document.createElement('style');
  s.id = 'sv-anim';
  s.textContent = `
    @keyframes svPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
    @keyframes svScroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
    @keyframes svSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    @keyframes svFlicker { 0%, 100% { opacity: 1; } 50% { opacity: 0.92; } 52% { opacity: 0.6; } 54% { opacity: 1; } }
    @keyframes svSweep { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
    @keyframes svRipGlow { 0%,100% { box-shadow: 0 0 8px rgba(255,61,127,0.4); } 50% { box-shadow: 0 0 18px rgba(255,61,127,0.8); } }
  `;
  document.head.appendChild(s);
}

Object.assign(window, {
  sv, SvCtx, useSv, useAccent, useDensity,
  SvAtmosphere, SvSkyline, SvTelemetryBand, SvScramble,
  SvPanel, SvCorners, SvLabel, SvBar, SvBadge, SvRuler, SvAnimValue, SvMark,
});

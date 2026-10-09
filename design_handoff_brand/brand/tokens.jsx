/* ═══════════════════════════════════════════════════════════════════════════
   Engram brand tokens — inherited from Synapse v2 direction.
   Single source of truth for this brand sheet.
   ═══════════════════════════════════════════════════════════════════════════ */

const b = {
  // Surfaces
  bg0:   '#05070c',
  bg1:   '#0a0e18',
  bg2:   '#121827',
  bg3:   '#1a2234',
  paper: '#f3eee4',
  paperDim:'#dbd5c7',

  // Ink
  ink:    '#e6ecf5',
  inkDim: '#8893a8',
  inkFaint:'#4a5369',
  inkGhost:'#2a3147',
  inkOnPaper: '#15161a',
  inkOnPaperDim: '#5a5b62',

  // Brand accents
  cyan:    '#5eead4',
  cyanHi:  '#9ff8e8',
  cyanDim: '#2dd4bf',
  magenta: '#ff3d7f',
  magentaHi:'#ff7aa5',
  yellow:  '#fde047',
  amber:   '#fcd34d',
  green:   '#86efac',
  red:     '#ff5555',

  // Lines
  line:    'rgba(94,234,212,0.14)',
  lineMid: 'rgba(94,234,212,0.24)',
  lineHi:  'rgba(94,234,212,0.42)',

  // Type
  mono:    '"JetBrains Mono", ui-monospace, monospace',
  display: '"Chakra Petch", sans-serif',
  sans:    '"Chakra Petch", "Space Grotesk", sans-serif',
};

// Generic dark panel with corner ticks
function BPanel({ children, style, pad = 22, accent }) {
  const border = accent || b.lineMid;
  return (
    <div style={{
      background:'linear-gradient(180deg, rgba(18,24,39,0.7), rgba(10,14,24,0.85))',
      border:`1px solid ${border}`,
      padding: pad, position:'relative',
      boxShadow:'inset 0 0 32px rgba(94,234,212,0.03)',
      ...style,
    }}>
      <BCorners color={accent ? `${accent}aa` : b.lineHi}/>
      {children}
    </div>
  );
}

function BCorners({ color = b.lineHi, size = 8 }) {
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

function BLabel({ children, color, style, caret = true }) {
  const c = color || b.inkDim;
  return (
    <div style={{
      fontFamily: b.mono, fontSize: 10, letterSpacing:'0.22em',
      textTransform:'uppercase', color: c,
      display:'inline-flex', alignItems:'center', gap: 6, ...style,
    }}>
      {caret && <span style={{color: color || b.cyan, opacity: 0.7}}>›</span>}
      {children}
    </div>
  );
}

// Background haze + scanlines — the Synapse v2 atmosphere, simplified
function BAtmosphere({ children, scanlines = true }) {
  return (
    <div style={{position:'relative', width:'100%', height:'100%', background: b.bg0, overflow:'hidden'}}>
      <div style={{position:'absolute', inset: 0, pointerEvents:'none', backgroundImage:
        'radial-gradient(ellipse at 15% 20%, rgba(94,234,212,0.10), transparent 55%),' +
        'radial-gradient(ellipse at 85% 85%, rgba(255,61,127,0.07), transparent 50%)'}}/>
      <div style={{position:'relative', zIndex: 2, width:'100%', height:'100%'}}>{children}</div>
      {scanlines && (
        <div style={{position:'absolute', inset:0, pointerEvents:'none', zIndex: 3, opacity: 0.3,
          backgroundImage:'repeating-linear-gradient(0deg, rgba(94,234,212,0.05) 0 1px, transparent 1px 3px)'}}/>
      )}
      <div style={{position:'absolute', inset: 0, pointerEvents:'none', zIndex: 5,
        background:'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.5) 100%)'}}/>
    </div>
  );
}

Object.assign(window, { brandTokens: b, BPanel, BCorners, BLabel, BAtmosphere });

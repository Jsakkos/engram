/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Brand sheet
   Lays out marks, construction, color, type, icons and applications across
   a DesignCanvas. Inherits the Synapse v2 atmospheric direction.
   ═══════════════════════════════════════════════════════════════════════════ */

const T = brandTokens;

// ── 1. Identity ──────────────────────────────────────────────────────────────

function ArtboardHero() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: '48px 64px',
        display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
        {/* top bar */}
        <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
          <BLabel style={{color: T.cyan}}>Engram · Brand Identity · v1.0</BLabel>
          <BLabel color={T.inkFaint} caret={false}>2026 · Internal</BLabel>
        </div>

        {/* hero lockup */}
        <div style={{display:'flex', alignItems:'center', gap: 48}}>
          <LockupDescriptor size={96} descriptor="MEDIA ARCHIVE · v1.0"/>
        </div>

        {/* footer rule + tagline */}
        <div>
          <div style={{height: 1, background: T.lineMid, marginBottom: 14}}/>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-end'}}>
            <div style={{fontFamily: T.display, fontSize: 28, color: T.ink,
              letterSpacing:'0.04em', lineHeight: 1.1, maxWidth: 720}}>
              The memory trace, made permanent.
            </div>
            <div style={{display:'flex', gap: 28, alignItems:'center'}}>
              {['CYAN', 'MAGENTA', 'AMBER', 'BONE'].map((n, i) => {
                const c = [T.cyan, T.magenta, T.amber, T.ink][i];
                return (
                  <div key={n} style={{display:'flex', alignItems:'center', gap: 8}}>
                    <div style={{width: 14, height: 14, background: c, boxShadow:`0 0 12px ${c}66`}}/>
                    <span style={{fontFamily: T.mono, fontSize: 10, color: T.inkDim, letterSpacing:'0.18em'}}>{n}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </BAtmosphere>
  );
}

function ArtboardMarkHero() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, display:'flex',
        flexDirection:'column', alignItems:'center', justifyContent:'center', gap: 28}}>
        <Mark size={260}/>
        <BLabel color={T.cyan}>The Mark · Primary</BLabel>
      </div>
      <div style={{position:'absolute', left: 24, bottom: 24, right: 24,
        display:'flex', justifyContent:'space-between',
        fontFamily: T.mono, fontSize: 10, color: T.inkFaint, letterSpacing:'0.16em'}}>
        <span>CONCEPT · CONCENTRIC MEMORY RINGS</span>
        <span>READ-LINE → NODE</span>
      </div>
    </BAtmosphere>
  );
}

function ArtboardWordmark() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 48,
        display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
        <BLabel color={T.cyan}>Wordmark</BLabel>
        <div style={{display:'flex', flexDirection:'column', gap: 28}}>
          <Wordmark size={112} tracking="0.14em"/>
          <div style={{height:1, background: T.line}}/>
          <div style={{display:'flex', alignItems:'baseline', gap: 32, flexWrap:'wrap'}}>
            <Wordmark size={48} tracking="0.16em"/>
            <Wordmark size={28} tracking="0.20em"/>
            <Wordmark size={18} color={T.inkDim} tracking="0.26em"/>
          </div>
        </div>
        <div style={{display:'flex', gap: 32, fontFamily: T.mono, fontSize: 10,
          color: T.inkDim, letterSpacing:'0.16em'}}>
          <span>CHAKRA PETCH · 700</span>
          <span>UPPERCASE</span>
          <span>TRACKING SCALES INVERSELY</span>
        </div>
      </div>
    </BAtmosphere>
  );
}

function ArtboardLockups() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 40,
        display:'flex', flexDirection:'column', gap: 24}}>
        <BLabel color={T.cyan}>Lockups</BLabel>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gridTemplateRows:'1fr 1fr',
          gap: 16, flex: 1}}>
          <LockupSlot label="01 · Horizontal">
            <LockupH size={42}/>
          </LockupSlot>
          <LockupSlot label="02 · Stacked">
            <LockupV size={42}/>
          </LockupSlot>
          <LockupSlot label="03 · With descriptor">
            <LockupDescriptor size={36} descriptor="MEDIA ARCHIVE"/>
          </LockupSlot>
          <LockupSlot label="04 · Mark-only">
            <Mark size={90}/>
          </LockupSlot>
        </div>
      </div>
    </BAtmosphere>
  );
}

function LockupSlot({ children, label }) {
  return (
    <BPanel style={{display:'flex', alignItems:'center', justifyContent:'center',
      position:'relative', minHeight: 0}}>
      <div style={{position:'absolute', top: 10, left: 12,
        fontFamily: T.mono, fontSize: 9, letterSpacing:'0.18em',
        color: T.inkFaint}}>{label}</div>
      {children}
    </BPanel>
  );
}

function ArtboardReversed() {
  return (
    <div style={{width:'100%', height:'100%', background: T.paper,
      padding: 48, display:'flex', flexDirection:'column', justifyContent:'space-between',
      position:'relative'}}>
      <div style={{fontFamily: T.mono, fontSize: 10, letterSpacing:'0.22em',
        color: T.inkOnPaperDim, textTransform:'uppercase'}}>
        › On light — paper edition
      </div>
      <div style={{display:'flex', flexDirection:'column', gap: 28}}>
        <LockupH size={56} paper glow={false}/>
        <div style={{height: 1, background: 'rgba(0,0,0,0.12)'}}/>
        <div style={{display:'flex', alignItems:'center', gap: 36}}>
          <Mark size={64} paper glow={false}/>
          <Mark size={40} paper glow={false}/>
          <MarkMono size={40} paper/>
          <Wordmark size={28} paper tracking="0.20em"/>
        </div>
      </div>
      <div style={{display:'flex', justifyContent:'space-between',
        fontFamily: T.mono, fontSize: 10, color: T.inkOnPaperDim, letterSpacing:'0.16em'}}>
        <span>BACKGROUND #F3EEE4</span>
        <span>INK #15161A</span>
      </div>
    </div>
  );
}

// ── 2. Construction & system ─────────────────────────────────────────────────

function ArtboardConstruction() {
  // Grid view of the mark — 24 unit baseline grid showing the geometry
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 16}}>
        <div style={{display:'flex', justifyContent:'space-between'}}>
          <BLabel color={T.cyan}>Construction · 64-unit grid</BLabel>
          <BLabel color={T.inkFaint} caret={false}>Three radii · 24/16/8</BLabel>
        </div>
        <div style={{flex: 1, display:'flex', alignItems:'center', justifyContent:'center', position:'relative'}}>
          <div style={{position:'relative', width: 480, height: 480}}>
            {/* Grid */}
            <svg viewBox="0 0 64 64" style={{position:'absolute', inset:0, width:'100%', height:'100%'}}>
              {[...Array(9)].map((_,i)=>(
                <line key={`v${i}`} x1={i*8} y1="0" x2={i*8} y2="64" stroke={T.line} strokeWidth="0.15"/>
              ))}
              {[...Array(9)].map((_,i)=>(
                <line key={`h${i}`} x1="0" y1={i*8} x2="64" y2={i*8} stroke={T.line} strokeWidth="0.15"/>
              ))}
              {/* axes */}
              <line x1="32" y1="0" x2="32" y2="64" stroke={T.lineMid} strokeWidth="0.2"/>
              <line x1="0" y1="32" x2="64" y2="32" stroke={T.lineMid} strokeWidth="0.2"/>

              {/* radius guides */}
              {[24, 16, 8].map(r => (
                <circle key={r} cx="32" cy="32" r={r} fill="none"
                  stroke={T.cyan} strokeWidth="0.2" strokeDasharray="1 1.2" opacity="0.5"/>
              ))}

              {/* the mark itself */}
              <g>
                <path d="M 32 8 A 24 24 0 1 0 32 56" fill="none" stroke={T.cyan} strokeWidth="2.2" strokeLinecap="round"/>
                <path d="M 32 16 A 16 16 0 1 0 32 48" fill="none" stroke={T.cyan} strokeWidth="2.2" strokeLinecap="round" opacity="0.78"/>
                <path d="M 32 24 A 8 8 0 1 0 32 40" fill="none" stroke={T.cyan} strokeWidth="2.2" strokeLinecap="round" opacity="0.55"/>
                <line x1="32" y1="32" x2="56" y2="32" stroke={T.magenta} strokeWidth="2.2" strokeLinecap="round"/>
                <circle cx="56" cy="32" r="3" fill={T.magenta}/>
              </g>

              {/* labels */}
              <text x="32" y="6.5" fontFamily={T.mono} fontSize="2.2" fill={T.cyan}
                textAnchor="middle" letterSpacing="0.1em" opacity="0.7">R 24</text>
              <text x="48" y="22" fontFamily={T.mono} fontSize="2.2" fill={T.cyan}
                letterSpacing="0.1em" opacity="0.5">R 16</text>
              <text x="40" y="28" fontFamily={T.mono} fontSize="2.2" fill={T.cyan}
                letterSpacing="0.1em" opacity="0.5">R 8</text>
              <text x="44" y="30.5" fontFamily={T.mono} fontSize="2.2" fill={T.magenta}
                letterSpacing="0.1em">L 24</text>
            </svg>
          </div>
        </div>
      </div>
    </BAtmosphere>
  );
}

function ArtboardClearSpace() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36, display:'flex',
        flexDirection:'column', gap: 20}}>
        <BLabel color={T.cyan}>Clear space + minimum sizes</BLabel>

        <div style={{display:'flex', gap: 24, flex: 1}}>
          {/* Clear space diagram */}
          <BPanel pad={28} style={{flex: 1, display:'flex',
            alignItems:'center', justifyContent:'center', position:'relative'}}>
            <div style={{position:'relative', padding: 32, border:`1px dashed ${T.lineMid}`}}>
              <LockupH size={36}/>
              {/* "X" cap measure */}
              <div style={{position:'absolute', top: -8, left: '50%', transform:'translateX(-50%)',
                fontFamily: T.mono, fontSize: 10, color: T.cyan, letterSpacing:'0.16em',
                background: T.bg0, padding:'0 6px'}}>X</div>
              <div style={{position:'absolute', bottom: -8, left: '50%', transform:'translateX(-50%)',
                fontFamily: T.mono, fontSize: 10, color: T.cyan, letterSpacing:'0.16em',
                background: T.bg0, padding:'0 6px'}}>X</div>
              <div style={{position:'absolute', left: -10, top: '50%', transform:'translateY(-50%)',
                fontFamily: T.mono, fontSize: 10, color: T.cyan, letterSpacing:'0.16em',
                background: T.bg0, padding:'2px 0'}}>X</div>
              <div style={{position:'absolute', right: -10, top: '50%', transform:'translateY(-50%)',
                fontFamily: T.mono, fontSize: 10, color: T.cyan, letterSpacing:'0.16em',
                background: T.bg0, padding:'2px 0'}}>X</div>
            </div>
            <div style={{position:'absolute', bottom: 14, left: 18, right: 18,
              fontFamily: T.mono, fontSize: 10, color: T.inkDim, letterSpacing:'0.16em'}}>
              › Clear space = cap-height of the wordmark (X), on all sides
            </div>
          </BPanel>

          {/* Min sizes */}
          <BPanel pad={20} style={{flex: 1, display:'flex', flexDirection:'column', gap: 18}}>
            <BLabel color={T.cyan} caret={false}>Minimum sizes</BLabel>
            <MinRow label="LOCKUP · 24 PX" minH={24}><LockupH size={14}/></MinRow>
            <MinRow label="WORDMARK · 12 PX"><Wordmark size={12} tracking="0.24em"/></MinRow>
            <MinRow label="MARK · 16 PX"><Mark size={16}/></MinRow>
            <MinRow label="MONOGRAM · 12 PX"><MarkMono size={12}/></MinRow>
          </BPanel>
        </div>
      </div>
    </BAtmosphere>
  );
}

function MinRow({ label, children }) {
  return (
    <div style={{display:'flex', alignItems:'center', gap: 16,
      padding:'10px 0', borderTop:`1px dashed ${T.line}`}}>
      <div style={{width: 48, display:'flex', justifyContent:'center'}}>{children}</div>
      <div style={{fontFamily: T.mono, fontSize: 10, color: T.inkDim,
        letterSpacing:'0.18em'}}>{label}</div>
    </div>
  );
}

function ArtboardColor() {
  const palette = [
    { name: 'BG0',     hex: '#05070C', role: 'Surface · base',     fg: T.ink },
    { name: 'BG1',     hex: '#0A0E18', role: 'Surface · raised',   fg: T.ink },
    { name: 'BG2',     hex: '#121827', role: 'Surface · panel',    fg: T.ink },
    { name: 'CYAN',    hex: '#5EEAD4', role: 'Primary · brand',    fg: T.bg0, glow: true },
    { name: 'MAGENTA', hex: '#FF3D7F', role: 'Active · ripping',   fg: T.ink, glow: true },
    { name: 'AMBER',   hex: '#FCD34D', role: 'Matching · warn',    fg: T.bg0 },
    { name: 'GREEN',   hex: '#86EFAC', role: 'Complete',           fg: T.bg0 },
    { name: 'INK',     hex: '#E6ECF5', role: 'Foreground · type',  fg: T.bg0 },
    { name: 'INKDIM',  hex: '#8893A8', role: 'Foreground · muted', fg: T.bg0 },
    { name: 'PAPER',   hex: '#F3EEE4', role: 'Light edition',      fg: T.bg0 },
  ];
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 18}}>
        <div style={{display:'flex', justifyContent:'space-between'}}>
          <BLabel color={T.cyan}>Color · 10 tokens</BLabel>
          <BLabel color={T.inkFaint} caret={false}>OKLCH balanced · accents share C/L</BLabel>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'repeat(5, 1fr)', gap: 14, flex: 1}}>
          {palette.map(c => (
            <div key={c.name} style={{display:'flex', flexDirection:'column',
              border:`1px solid ${T.lineMid}`, overflow:'hidden'}}>
              <div style={{flex:1, background: c.hex,
                boxShadow: c.glow ? `inset 0 0 32px ${c.hex}, 0 0 16px ${c.hex}55` : 'none',
                minHeight: 80}}/>
              <div style={{padding:'10px 12px', background: T.bg1, borderTop:`1px solid ${T.lineMid}`}}>
                <div style={{fontFamily: T.mono, fontSize: 10, color: T.ink, letterSpacing:'0.16em'}}>
                  {c.name}
                </div>
                <div style={{fontFamily: T.mono, fontSize: 9, color: T.inkDim, marginTop: 4, letterSpacing:'0.06em'}}>
                  {c.hex}
                </div>
                <div style={{fontFamily: T.mono, fontSize: 9, color: T.inkFaint, marginTop: 2, letterSpacing:'0.1em'}}>
                  {c.role}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </BAtmosphere>
  );
}

function ArtboardType() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 18}}>
        <BLabel color={T.cyan}>Typography</BLabel>

        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap: 16, flex: 1}}>
          {/* Display */}
          <BPanel pad={22} style={{display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
            <div>
              <BLabel color={T.cyan} caret={false}>Display · Chakra Petch</BLabel>
              <div style={{fontFamily: T.display, fontWeight: 700, fontSize: 80,
                color: T.ink, letterSpacing:'-0.01em', lineHeight: 0.95, marginTop: 14}}>
                Aa Eg
              </div>
              <div style={{fontFamily: T.display, fontWeight: 700, fontSize: 22,
                color: T.ink, letterSpacing:'0.04em', marginTop: 16, lineHeight: 1.2}}>
                Section heading · 22/26
              </div>
              <div style={{fontFamily: T.display, fontWeight: 500, fontSize: 14,
                color: T.inkDim, marginTop: 8, lineHeight: 1.5}}>
                Body running text. Chakra Petch handles wide tracking and
                technical numerics elegantly — perfect for a UI that wants to
                feel like equipment.
              </div>
            </div>
            <div style={{fontFamily: T.mono, fontSize: 10, color: T.inkFaint, letterSpacing:'0.18em',
              marginTop: 16, paddingTop: 12, borderTop:`1px dashed ${T.line}`}}>
              › WEIGHTS 400 500 600 700 · USE 700 FOR THE WORDMARK
            </div>
          </BPanel>

          {/* Mono */}
          <BPanel pad={22} style={{display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
            <div>
              <BLabel color={T.cyan} caret={false}>Mono · JetBrains Mono</BLabel>
              <div style={{fontFamily: T.mono, fontWeight: 600, fontSize: 80,
                color: T.cyan, letterSpacing:'-0.02em', lineHeight: 0.95, marginTop: 14,
                textShadow:`0 0 16px ${T.cyan}55`}}>
                0123
              </div>
              <div style={{fontFamily: T.mono, fontSize: 11, color: T.ink, marginTop: 16,
                letterSpacing:'0.16em', lineHeight: 1.8}}>
                LABELS · TELEMETRY · CAPS<br/>
                <span style={{color: T.inkDim}}>34.4 MB/s · ETA 04:02 · 7.6×</span>
              </div>
              <div style={{fontFamily: T.mono, fontSize: 11, color: T.cyanDim, marginTop: 14}}>
                $ engram --rip --auto
              </div>
            </div>
            <div style={{fontFamily: T.mono, fontSize: 10, color: T.inkFaint, letterSpacing:'0.18em',
              marginTop: 16, paddingTop: 12, borderTop:`1px dashed ${T.line}`}}>
              › WEIGHTS 300 400 500 600 700 · TRACK 0.16–0.24EM ON CAPS
            </div>
          </BPanel>
        </div>

        <div style={{display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap: 12}}>
          {[
            ['DISPLAY', 'Chakra Petch · 700 · uppercase'],
            ['HEADING', 'Chakra Petch · 700 · normal case'],
            ['BODY',    'Chakra Petch · 500 · normal case'],
            ['SYSTEM',  'JetBrains Mono · 500 · 0.20em track'],
          ].map(([k,v]) => (
            <div key={k} style={{padding:'10px 14px', border:`1px solid ${T.line}`,
              background: T.bg1}}>
              <div style={{fontFamily: T.mono, fontSize: 9, color: T.cyan, letterSpacing:'0.22em'}}>{k}</div>
              <div style={{fontFamily: T.mono, fontSize: 10, color: T.inkDim, marginTop: 4, letterSpacing:'0.1em'}}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </BAtmosphere>
  );
}

Object.assign(window, {
  ArtboardHero, ArtboardMarkHero, ArtboardWordmark, ArtboardLockups, ArtboardReversed,
  ArtboardConstruction, ArtboardClearSpace, ArtboardColor, ArtboardType,
});

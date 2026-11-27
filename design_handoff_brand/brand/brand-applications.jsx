/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Applications
   Where the brand actually lives: app icons, favicons, splash, dock badge,
   terminal banner.
   ═══════════════════════════════════════════════════════════════════════════ */

const A = brandTokens;

// ── App icon family ─────────────────────────────────────────────────────────
function ArtboardAppIcons() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 40,
        display:'flex', flexDirection:'column', gap: 24}}>
        <div style={{display:'flex', justifyContent:'space-between'}}>
          <BLabel color={A.cyan}>App icon · macOS / Windows</BLabel>
          <BLabel color={A.inkFaint} caret={false}>Squircle r ≈ 0.224 · 512 · 256 · 128 · 64</BLabel>
        </div>

        <div style={{display:'flex', alignItems:'flex-end', gap: 40, flex: 1,
          justifyContent:'center'}}>
          <AppIconLabel size={256}/>
          <AppIconLabel size={160}/>
          <AppIconLabel size={96}/>
          <AppIconLabel size={64}/>
        </div>

        <div style={{display:'flex', gap: 14, alignItems:'center'}}>
          <BLabel caret={false} color={A.inkFaint}>Light edition</BLabel>
          <div style={{flex:1, height:1, background: A.line}}/>
          <AppIcon size={64} dark={false}/>
          <AppIcon size={48} dark={false}/>
          <AppIcon size={32} dark={false}/>
        </div>
      </div>
    </BAtmosphere>
  );
}

function AppIconLabel({ size }) {
  return (
    <div style={{display:'flex', flexDirection:'column', alignItems:'center', gap: 10}}>
      <AppIcon size={size}/>
      <div style={{fontFamily: A.mono, fontSize: 10, color: A.inkDim, letterSpacing:'0.18em'}}>
        {size}<span style={{color: A.inkFaint}}> · {size}PX</span>
      </div>
    </div>
  );
}

// ── Favicon strip ───────────────────────────────────────────────────────────
function ArtboardFavicon() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 40,
        display:'flex', flexDirection:'column', gap: 24}}>
        <BLabel color={A.cyan}>Favicon + tab presence</BLabel>

        <div style={{display:'flex', alignItems:'center', gap: 30, flex: 1}}>
          {[64, 48, 32, 24, 16].map(s => (
            <div key={s} style={{display:'flex', flexDirection:'column', alignItems:'center', gap: 10}}>
              <div style={{width: s, height: s, background: A.bg0, border:`1px solid ${A.lineMid}`,
                display:'flex', alignItems:'center', justifyContent:'center'}}>
                <MarkMono size={Math.round(s * 0.72)} glow={s >= 32}/>
              </div>
              <div style={{fontFamily: A.mono, fontSize: 9, color: A.inkDim, letterSpacing:'0.16em'}}>{s}</div>
            </div>
          ))}

          <div style={{flex: 1, marginLeft: 20}}>
            {/* mock browser tabs */}
            <div style={{display:'flex', alignItems:'flex-end', gap: 2}}>
              {['Engram', 'Library', 'Review'].map((t, i) => (
                <div key={t} style={{
                  padding:'10px 14px 10px 12px', display:'flex', alignItems:'center', gap: 8,
                  background: i===0 ? A.bg2 : A.bg1,
                  borderTop: i===0 ? `2px solid ${A.cyan}` : `1px solid ${A.line}`,
                  borderLeft:`1px solid ${A.line}`, borderRight:`1px solid ${A.line}`,
                  minWidth: 140,
                }}>
                  <MarkMono size={14} glow={false} color={i===0?A.cyan:A.inkDim}/>
                  <span style={{fontFamily: A.sans, fontWeight: 500, fontSize: 12,
                    color: i===0?A.ink:A.inkDim}}>{t}</span>
                  <span style={{marginLeft:'auto', color: A.inkFaint, fontSize: 14}}>×</span>
                </div>
              ))}
            </div>
            <div style={{padding:'8px 14px', background: A.bg2, borderBottom:`1px solid ${A.line}`,
              display:'flex', alignItems:'center', gap: 10,
              fontFamily: A.mono, fontSize: 11, color: A.inkDim, letterSpacing:'0.04em'}}>
              <span style={{color: A.cyan}}>⌂</span>
              <span>engram://dashboard</span>
            </div>
          </div>
        </div>

        <div style={{fontFamily: A.mono, fontSize: 10, color: A.inkFaint, letterSpacing:'0.18em'}}>
          › 16PX FAVICON USES MONOGRAM (NO DENDRITE) FOR LEGIBILITY
        </div>
      </div>
    </BAtmosphere>
  );
}

// ── Splash screen ────────────────────────────────────────────────────────────
function ArtboardSplash() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0,
        display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap: 28}}>
        <MarkAnimated size={180}/>
        <Wordmark size={56} tracking="0.22em"/>
        <div style={{fontFamily: A.mono, fontSize: 11, color: A.cyan,
          letterSpacing:'0.32em', marginTop: 4}}>
          INITIALIZING<span style={{animation:'engBlink 1s infinite'}}>...</span>
        </div>
      </div>
      <div style={{position:'absolute', bottom: 30, left: 0, right: 0,
        display:'flex', justifyContent:'space-between', padding:'0 40px',
        fontFamily: A.mono, fontSize: 10, color: A.inkFaint, letterSpacing:'0.18em'}}>
        <span>ENGRAM · MEDIA ARCHIVE</span>
        <span>v1.0.0 · BUILD 2026.05</span>
      </div>
    </BAtmosphere>
  );
}

// ── Dock badge mockup ────────────────────────────────────────────────────────
function ArtboardDock() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 40,
        display:'flex', flexDirection:'column', gap: 22}}>
        <BLabel color={A.cyan}>Dock + notification states</BLabel>

        <div style={{display:'flex', alignItems:'center', justifyContent:'center', gap: 28, flex: 1}}>
          {[
            { label: 'IDLE',     badge: null,        accent: null },
            { label: 'ACTIVE',   badge: 'pulse',     accent: A.magenta },
            { label: 'BADGE 3',  badge: 'count',     accent: A.magenta, count: 3 },
            { label: 'COMPLETE', badge: 'check',     accent: A.green },
            { label: 'ERROR',    badge: 'alert',     accent: A.red },
          ].map(s => (
            <div key={s.label} style={{display:'flex', flexDirection:'column',
              alignItems:'center', gap: 12}}>
              <div style={{position:'relative'}}>
                <AppIcon size={88}/>
                {s.badge === 'pulse' && (
                  <div style={{position:'absolute', top: -2, right: -2,
                    width: 18, height: 18, borderRadius: '50%', background: s.accent,
                    boxShadow:`0 0 12px ${s.accent}`,
                    animation:'engBlink 1s infinite',
                    border:`2px solid ${A.bg0}`}}/>
                )}
                {s.badge === 'count' && (
                  <div style={{position:'absolute', top: -6, right: -6,
                    minWidth: 24, height: 24, borderRadius: '50%', background: s.accent,
                    color: A.bg0, fontFamily: A.mono, fontWeight: 700, fontSize: 12,
                    display:'flex', alignItems:'center', justifyContent:'center',
                    padding:'0 6px',
                    boxShadow:`0 0 12px ${s.accent}66`,
                    border:`2px solid ${A.bg0}`}}>{s.count}</div>
                )}
                {s.badge === 'check' && (
                  <div style={{position:'absolute', top: -6, right: -6,
                    width: 24, height: 24, borderRadius: '50%', background: s.accent,
                    color: A.bg0, fontFamily: A.sans, fontWeight: 700, fontSize: 14,
                    display:'flex', alignItems:'center', justifyContent:'center',
                    border:`2px solid ${A.bg0}`}}>✓</div>
                )}
                {s.badge === 'alert' && (
                  <div style={{position:'absolute', top: -6, right: -6,
                    width: 24, height: 24, borderRadius: '50%', background: s.accent,
                    color: A.bg0, fontFamily: A.sans, fontWeight: 700, fontSize: 14,
                    display:'flex', alignItems:'center', justifyContent:'center',
                    border:`2px solid ${A.bg0}`}}>!</div>
                )}
              </div>
              <div style={{fontFamily: A.mono, fontSize: 10, color: A.inkDim,
                letterSpacing:'0.20em'}}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </BAtmosphere>
  );
}

// ── Terminal banner (CLI / first run) ───────────────────────────────────────
function ArtboardTerminal() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 20}}>
        <BLabel color={A.cyan}>CLI banner · also used for splash logs</BLabel>

        <BPanel pad={22} style={{flex: 1, fontFamily: A.mono, fontSize: 13,
          color: A.cyan, lineHeight: 1.5, letterSpacing: 0}}>
          <pre style={{margin: 0, fontFamily: A.mono,
            textShadow:`0 0 6px ${A.cyan}88`, color: A.cyanHi, fontSize: 14, lineHeight: 1.15}}>
{`  ▓█████ ███▄    █  ▄████  ██▀███   ▄▄▄       ███▄ ▄███▓
  ▓█   ▀ ██ ▀█   █ ██▒ ▀█▒▓██ ▒ ██▒▒████▄    ▓██▒▀█▀ ██▒
  ▒███  ▓██  ▀█ ██▒▒██░▄▄▄░▓██ ░▄█ ▒▒██  ▀█▄  ▓██    ▓██░
  ▒▓█  ▄▓██▒  ▐▌██▒░▓█  ██▓▒██▀▀█▄  ░██▄▄▄▄██ ▒██    ▒██
  ░▒████▒██░   ▓██░░▒▓███▀▒░██▓ ▒██▒ ▓█   ▓██▒▒██▒   ░██▒`}
          </pre>
          <div style={{marginTop: 16, color: A.inkDim}}>
            <span style={{color: A.cyan}}>›</span> ENGRAM v1.0.0 · MEDIA ARCHIVE PIPELINE<br/>
            <span style={{color: A.cyan}}>›</span> drive E:\ ready · 1 disc detected
          </div>
          <div style={{marginTop: 12, color: A.magenta}}>
            $ engram <span style={{color: A.cyanHi}}>--rip</span> <span style={{color: A.inkDim}}>--auto-match</span>
            <span style={{animation:'engBlink 1s infinite', marginLeft: 6}}>▍</span>
          </div>
        </BPanel>
      </div>
    </BAtmosphere>
  );
}

Object.assign(window, {
  ArtboardAppIcons, ArtboardFavicon, ArtboardSplash, ArtboardDock, ArtboardTerminal,
});

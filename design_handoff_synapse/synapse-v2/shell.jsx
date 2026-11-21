/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — SHELL
   Top chrome, nav, and status bar used across all Synapse v2 screens.
   Everything honors density + color balance from SvCtx.
   ═══════════════════════════════════════════════════════════════════════════ */

function SvTopBar({ route = 'dashboard', onRoute }) {
  const d = useDensity();
  const a = useAccent();
  const routes = [
    { id:'dashboard', label:'DASHBOARD' },
    { id:'review',    label:'REVIEW',    badge: 3 },
    { id:'library',   label:'LIBRARY' },
    { id:'history',   label:'HISTORY' },
  ];
  return (
    <div style={{
      display:'flex', alignItems:'center', justifyContent:'space-between',
      padding: `${d.headerPad}px 28px`, borderBottom:`1px solid ${sv.line}`,
      background:'linear-gradient(180deg, rgba(18,24,39,0.7), transparent)',
    }}>
      <div style={{display:'flex', alignItems:'center', gap: 14}}>
        <SvMark size={38}/>
        <div>
          <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 22,
            letterSpacing:'0.18em', color: a.primaryHi,
            textShadow: `0 0 12px ${a.primary}55`}}>ENGRAM</div>
          <div style={{fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.24em',
            color: sv.inkFaint, marginTop: 3}}>
            › MEMORY · ARCHIVAL · v0.6.0
          </div>
        </div>
      </div>

      <nav style={{display:'flex', alignItems:'center', gap: 2}}>
        {routes.map(r => {
          const active = r.id === route;
          return (
            <div key={r.id} onClick={() => onRoute?.(r.id)} style={{
              fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.22em',
              padding:'10px 18px', cursor:'pointer', position:'relative',
              color: active ? a.primaryHi : sv.inkDim,
              display:'flex', alignItems:'center', gap: 8,
            }}>
              {r.label}
              {r.badge && (
                <span style={{
                  fontSize: 9, padding:'1px 6px', background: sv.yellow, color: sv.bg0,
                  fontWeight: 700, letterSpacing:'0.05em',
                }}>{r.badge}</span>
              )}
              {active && <div style={{
                position:'absolute', bottom: 0, left: 12, right: 12, height: 2,
                background: a.primary, boxShadow: `0 0 8px ${a.primary}`,
              }}/>}
            </div>
          );
        })}
      </nav>

      <div style={{display:'flex', alignItems:'center', gap: 12}}>
        <SvBadge state="live">LIVE</SvBadge>
        <div style={{width: 1, height: 20, background: sv.line}}/>
        <button style={{background:'transparent', border:`1px solid ${sv.line}`,
          color: sv.inkDim, padding: 8, cursor:'pointer'}}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="3"/>
            <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/>
          </svg>
        </button>
      </div>
    </div>
  );
}

function SvStatusBar({ variant = 'default' }) {
  const a = useAccent();
  return (
    <div style={{
      display:'flex', alignItems:'stretch', justifyContent:'space-between',
      borderTop:`1px solid ${sv.line}`, background:'rgba(18,24,39,0.55)',
      fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em', color: sv.inkFaint,
    }}>
      <div style={{display:'flex', alignItems:'center', gap: 18, padding:'8px 20px'}}>
        <span style={{color: sv.magenta}}>● 1 ACTIVE</span>
        <span style={{color: sv.green}}>● 0 ARCHIVED</span>
        <span>DRIVE E: READY</span>
      </div>
      <div style={{flex: 1, display:'flex', alignItems:'center', padding:'0 20px'}}>
        <SvTelemetryBand items={SV_TELEMETRY_STRINGS} speed={100}/>
      </div>
      <div style={{display:'flex', alignItems:'center', gap: 18, padding:'8px 20px'}}>
        <span>WS · CONNECTED</span>
        <span style={{color: a.primaryHi}}>v0.6.0</span>
      </div>
    </div>
  );
}

// small utility — outlined icon button
function SvIconBtn({ children, onClick, active, style }) {
  return (
    <button onClick={onClick} style={{
      background: active ? 'rgba(94,234,212,0.08)' : 'transparent',
      border:`1px solid ${active ? sv.lineHi : sv.line}`,
      color: active ? sv.cyan : sv.inkDim,
      padding:'8px 14px', cursor:'pointer',
      fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em',
      display:'inline-flex', alignItems:'center', gap: 8,
      ...style,
    }}>{children}</button>
  );
}

Object.assign(window, { SvTopBar, SvStatusBar, SvIconBtn });

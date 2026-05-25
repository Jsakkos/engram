/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Icon system artboards
   Status / media / action icon grids with usage examples.
   ═══════════════════════════════════════════════════════════════════════════ */

const X = brandTokens;

function IconCell({ label, accent, children }) {
  return (
    <div style={{
      display:'flex', flexDirection:'column', alignItems:'center', gap: 10,
      padding:'18px 8px',
      border:`1px solid ${X.line}`,
      background:'linear-gradient(180deg, rgba(18,24,39,0.45), rgba(10,14,24,0.65))',
      position:'relative',
    }}>
      <div style={{color: accent || X.cyan, filter: accent ? `drop-shadow(0 0 6px ${accent}66)` : `drop-shadow(0 0 6px ${X.cyan}55)`}}>
        {children}
      </div>
      <div style={{fontFamily: X.mono, fontSize: 9, color: X.inkDim,
        letterSpacing:'0.20em', textTransform:'uppercase'}}>{label}</div>
    </div>
  );
}

function IconSection({ title, items, sub }) {
  return (
    <div style={{display:'flex', flexDirection:'column', gap: 14, flex: 1}}>
      <div style={{display:'flex', alignItems:'baseline', gap: 14}}>
        <BLabel color={X.cyan}>{title}</BLabel>
        {sub && <span style={{fontFamily: X.mono, fontSize: 9, color: X.inkFaint, letterSpacing:'0.18em'}}>{sub}</span>}
      </div>
      <div style={{display:'grid', gridTemplateColumns:`repeat(${Math.min(8, items.length)}, 1fr)`, gap: 10}}>
        {items.map(([label, Comp, accent]) => (
          <IconCell key={label} label={label} accent={accent}>
            <Comp size={28}/>
          </IconCell>
        ))}
      </div>
    </div>
  );
}

function ArtboardIconStatus() {
  const items = [
    ['idle',     IcoIdle,     X.inkDim],
    ['scan',     IcoScan,     X.yellow],
    ['ripping',  IcoRipping,  X.magenta],
    ['matching', IcoMatching, X.amber],
    ['complete', IcoComplete, X.green],
    ['paused',   IcoPaused,   X.cyan],
    ['queued',   IcoQueued,   X.inkDim],
    ['error',    IcoError,    X.red],
  ];
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 22}}>
        <IconSection title="Status" sub="STROKE 1.5 · COLOR FROM PALETTE · LIT NODE FOR ACTIVE" items={items}/>

        {/* Inline usage row */}
        <BPanel pad={18} style={{marginTop: 'auto'}}>
          <BLabel color={X.cyan} caret={false} style={{marginBottom: 14}}>In context</BLabel>
          <div style={{display:'flex', gap: 18, flexWrap:'wrap'}}>
            <BadgeRow icon={<IcoRipping size={14}/>} accent={X.magenta} label="RIPPING" detail="34.4 MB/s · ETA 04:02"/>
            <BadgeRow icon={<IcoMatching size={14}/>} accent={X.amber}  label="MATCHING" detail="75% · 3 candidates"/>
            <BadgeRow icon={<IcoComplete size={14}/>} accent={X.green}  label="COMPLETE" detail="7.8 GB archived"/>
            <BadgeRow icon={<IcoQueued size={14}/>}  accent={X.inkDim}  label="QUEUED"   detail="7 titles waiting"/>
          </div>
        </BPanel>
      </div>
    </BAtmosphere>
  );
}

function BadgeRow({ icon, accent, label, detail }) {
  return (
    <div style={{display:'inline-flex', alignItems:'center', gap: 10,
      padding:'8px 14px', border:`1px solid ${accent}40`, background:`${accent}11`}}>
      <span style={{color: accent, display:'flex'}}>{icon}</span>
      <span style={{fontFamily: X.mono, fontSize: 10, color: accent, letterSpacing:'0.22em'}}>{label}</span>
      <span style={{fontFamily: X.mono, fontSize: 10, color: X.inkDim, letterSpacing:'0.12em'}}>{detail}</span>
    </div>
  );
}

function ArtboardIconMedia() {
  const items = [
    ['disc',    IcoDisc],
    ['blu-ray', IcoBluRay],
    ['dvd',     IcoDvd],
    ['tv',      IcoTv],
    ['movie',   IcoMovie],
    ['episode', IcoEpisode],
    ['drive',   IcoDrive],
    ['library', IcoLibrary],
  ];
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 22}}>
        <IconSection title="Media types" sub="USED IN CARDS, BADGES, LIBRARY VIEW" items={items}/>

        <BPanel pad={18} style={{marginTop: 'auto'}}>
          <BLabel color={X.cyan} caret={false} style={{marginBottom: 14}}>Type badges</BLabel>
          <div style={{display:'flex', gap: 12, flexWrap:'wrap'}}>
            <TypeBadge icon={<IcoTv size={14}/>}     label="TV SERIES"/>
            <TypeBadge icon={<IcoMovie size={14}/>}  label="MOVIE"/>
            <TypeBadge icon={<IcoEpisode size={14}/>} label="EPISODE · S01E04"/>
            <TypeBadge icon={<IcoBluRay size={14}/>} label="BLU-RAY · BD50"/>
            <TypeBadge icon={<IcoDvd size={14}/>}    label="DVD"/>
          </div>
        </BPanel>
      </div>
    </BAtmosphere>
  );
}

function TypeBadge({ icon, label }) {
  return (
    <div style={{display:'inline-flex', alignItems:'center', gap: 8,
      padding:'6px 12px', border:`1px solid ${X.lineMid}`, background: X.bg2}}>
      <span style={{color: X.cyan, display:'flex'}}>{icon}</span>
      <span style={{fontFamily: X.mono, fontSize: 10, color: X.ink, letterSpacing:'0.22em'}}>{label}</span>
    </div>
  );
}

function ArtboardIconAction() {
  const items = [
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
  ];
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 22}}>
        <IconSection title="Action + navigation" sub="SAME STROKE · CYAN ON HOVER · MAGENTA ON ACTIVE PRIMARY" items={items}/>

        {/* Tabbar mock */}
        <BPanel pad={0} style={{marginTop: 'auto', overflow:'hidden'}}>
          <div style={{display:'flex'}}>
            {[
              ['Dashboard', IcoDashboard, true],
              ['Review', IcoReview, false],
              ['History', IcoHistory, false],
              ['Library', IcoLibrary, false],
              ['Settings', IcoSettings, false],
            ].map(([t, Comp, active], i) => (
              <div key={t} style={{
                padding:'14px 22px', display:'flex', alignItems:'center', gap: 10,
                borderRight: i<4 ? `1px solid ${X.line}` : 'none',
                background: active ? `${X.cyan}11` : 'transparent',
                borderBottom: active ? `2px solid ${X.cyan}` : '2px solid transparent',
              }}>
                <Comp size={18} color={active ? X.cyan : X.inkDim}/>
                <span style={{fontFamily: X.display, fontSize: 13, fontWeight: 600,
                  letterSpacing:'0.06em',
                  color: active ? X.ink : X.inkDim}}>{t}</span>
              </div>
            ))}
          </div>
        </BPanel>
      </div>
    </BAtmosphere>
  );
}

// ── Combined "in use" mock — a slice of an actual card ───────────────────────
function ArtboardInUse() {
  return (
    <BAtmosphere>
      <div style={{position:'absolute', inset: 0, padding: 36,
        display:'flex', flexDirection:'column', gap: 22}}>
        <BLabel color={X.cyan}>In use · job card</BLabel>

        <BPanel pad={0} style={{display:'flex', minHeight: 0}}>
          {/* disc art */}
          <div style={{padding: 24, borderRight:`1px solid ${X.line}`,
            display:'flex', alignItems:'center', justifyContent:'center',
            background:'linear-gradient(180deg, #08111f, #05070c)'}}>
            <MarkAnimated size={140}/>
          </div>
          <div style={{flex: 1, padding: 24, display:'flex', flexDirection:'column', gap: 16}}>
            <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', gap: 16}}>
              <div style={{display:'flex', flexDirection:'column', gap: 6}}>
                <div style={{fontFamily: X.display, fontWeight: 700, fontSize: 28, color: X.ink,
                  letterSpacing:'0.04em', lineHeight: 1}}>
                  ARRESTED DEVELOPMENT
                </div>
                <div style={{display:'flex', gap: 10, alignItems:'center'}}>
                  <TypeBadge icon={<IcoTv size={14}/>} label="TV SERIES"/>
                  <span style={{fontFamily: X.mono, fontSize: 10, color: X.inkFaint, letterSpacing:'0.18em'}}>
                    SEASON 01 · DISC 1 · 8 TITLES · BD50
                  </span>
                </div>
              </div>
              <BadgeRow icon={<IcoRipping size={14}/>} accent={X.magenta} label="RIPPING" detail="34.4 MB/s"/>
            </div>

            <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline'}}>
              <span style={{fontFamily: X.mono, fontSize: 10, color: X.inkDim, letterSpacing:'0.20em'}}>OVERALL</span>
              <span style={{fontFamily: X.mono, fontSize: 14, color: X.magenta, fontWeight: 600}}>5.7%</span>
            </div>
            <div style={{height: 6, background:'rgba(94,234,212,0.06)', position:'relative', overflow:'hidden'}}>
              <div style={{position:'absolute', inset:0, width:'5.7%',
                background:`linear-gradient(90deg, ${X.magenta}, ${X.cyan})`,
                boxShadow:`0 0 12px ${X.magenta}`}}/>
            </div>

            <div style={{display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap: 14}}>
              <Stat icon={<IcoBytes size={14}/>}      label="SIZE"   value="409.6 MB"/>
              <Stat icon={<IcoConfidence size={14}/>} label="SPEED"  value="7.6×"/>
              <Stat icon={<IcoIdle size={14}/>}       label="ETA"    value="04:02"/>
              <Stat icon={<IcoEpisode size={14}/>}    label="TRACKS" value="0/8"/>
            </div>

            <div style={{display:'flex', gap: 8, marginTop: 'auto', paddingTop: 8,
              borderTop:`1px dashed ${X.line}`}}>
              <ActionPill icon={<IcoPause size={14}/>} label="Pause"/>
              <ActionPill icon={<IcoCancel size={14}/>} label="Cancel"/>
              <div style={{flex: 1}}/>
              <ActionPill icon={<IcoEject size={14}/>} label="Eject"/>
              <ActionPill icon={<IcoMore size={14}/>} label=""/>
            </div>
          </div>
        </BPanel>
      </div>
    </BAtmosphere>
  );
}

function Stat({ icon, label, value }) {
  return (
    <div style={{display:'flex', flexDirection:'column', gap: 6}}>
      <div style={{display:'flex', alignItems:'center', gap: 6,
        fontFamily: X.mono, fontSize: 10, color: X.inkDim, letterSpacing:'0.20em'}}>
        <span style={{color: X.cyan, display:'flex', opacity: 0.7}}>{icon}</span>
        {label}
      </div>
      <div style={{fontFamily: X.display, fontSize: 22, fontWeight: 600, color: X.ink,
        letterSpacing:'-0.01em'}}>{value}</div>
    </div>
  );
}

function ActionPill({ icon, label }) {
  return (
    <button style={{
      display:'inline-flex', alignItems:'center', gap: 8,
      padding: label ? '8px 14px' : '8px 10px',
      background: 'transparent',
      border:`1px solid ${X.lineMid}`,
      color: X.ink, cursor:'pointer',
      fontFamily: X.display, fontSize: 12, fontWeight: 600, letterSpacing:'0.08em',
    }}>
      <span style={{color: X.cyan, display:'flex'}}>{icon}</span>
      {label}
    </button>
  );
}

Object.assign(window, {
  ArtboardIconStatus, ArtboardIconMedia, ArtboardIconAction, ArtboardInUse,
});

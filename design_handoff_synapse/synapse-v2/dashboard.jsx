/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — DASHBOARD
   Three layouts keyed by density context:
     · min    — monumental, one job focal, huge negative space
     · med    — split composition: job detail + track matrix + telemetry rails
     · dense  — Bloomberg-style multi-column terminal with side rails
   Live-animated progress bars; hover states on track cards.
   ═══════════════════════════════════════════════════════════════════════════ */

function SvDashboard({ state = 'ripping' }) {
  const { density } = useSv();
  if (density === 'min')   return <SvDashboardMin state={state}/>;
  if (density === 'dense') return <SvDashboardDense state={state}/>;
  return <SvDashboardMed state={state}/>;
}

// ── MEDIUM — canonical composition ──────────────────────────────────────────
function SvDashboardMed({ state }) {
  const d = useDensity();
  const a = useAccent();
  const isMatching = state === 'matching';
  const isComplete = state === 'complete';

  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="dashboard"/>

        {/* sub-header — filters + sort */}
        <div style={{display:'flex', alignItems:'center', justifyContent:'space-between',
          padding:'10px 28px', borderBottom:`1px solid ${sv.line}`}}>
          <div style={{display:'flex', gap: 6}}>
            {[['ALL',1,true],['ACTIVE',isComplete?0:1,false],['DONE',isComplete?1:0,false],['FLAGGED',0,false]].map(([l,n,act]) => (
              <div key={l} style={{
                fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.22em',
                padding:'6px 14px', cursor:'pointer',
                color: act ? a.primaryHi : sv.inkDim,
                border:`1px solid ${act ? a.primary : 'transparent'}`,
                background: act ? `${a.primary}0e` : 'transparent',
              }}>{l} <span style={{color: sv.inkFaint}}>[{n}]</span></div>
            ))}
          </div>
          <div style={{display:'flex', gap: 14, alignItems:'center',
            fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, letterSpacing:'0.2em'}}>
            <span>SORT · RECENT</span><span>·</span><span>VIEW · CARD</span>
          </div>
        </div>

        {/* body */}
        <div style={{flex:1, display:'grid', gridTemplateColumns:'1fr 280px', gap: 20,
          padding: 20, minHeight: 0, overflow:'hidden'}}>
          {/* main column — job card */}
          <div style={{display:'flex', flexDirection:'column', gap: 16, minWidth: 0, overflow:'auto'}}>
            <SvJobCard state={state}/>
          </div>
          {/* side rail — kinetic telemetry + system */}
          <SvSideRail state={state}/>
        </div>

        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

function SvJobCard({ state }) {
  const a = useAccent();
  const isMatching = state === 'matching';
  const isComplete = state === 'complete';
  return (
    <SvPanel pad={0} style={{overflow:'hidden'}} glow>
      <div style={{display:'flex', alignItems:'stretch'}}>
        <div style={{padding: 18, paddingRight: 0, flexShrink: 0}}>
          <SvDiscArt state={state}/>
        </div>
        <div style={{flex:1, padding: 20, minWidth: 0, display:'flex', flexDirection:'column', gap: 14}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap: 14}}>
            <div style={{minWidth: 0, flex: 1}}>
              <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 24, lineHeight: 1.1,
                color: a.primaryHi, letterSpacing:'0.06em',
                whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis',
                textShadow: `0 0 14px ${a.primary}33`}}>
                {SAMPLE_JOB.title}
              </div>
              <div style={{display:'flex', gap: 10, marginTop: 8, alignItems:'center',
                fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing:'0.2em'}}>
                <span style={{color: sv.magenta}}>TV</span><span>·</span>
                <span>{SAMPLE_JOB.label}</span><span>·</span>
                <span>S{String(SAMPLE_JOB.season).padStart(2,'0')} D{SAMPLE_JOB.disc}</span><span>·</span>
                <span>BD50 · 46.2 GB</span>
              </div>
            </div>
            <div style={{display:'flex', gap: 8, alignItems:'center', flexShrink: 0}}>
              <SvScramble text={isComplete?'TIME · 00:04:12':isMatching?'TIME · 00:00:28':'TIME · 00:00:21'}
                color={sv.inkFaint} size={9}/>
              <SvBadge state={isComplete?'complete':isMatching?'matching':'ripping'}>
                {isComplete?'COMPLETE':isMatching?'MATCHING':'RIPPING'}
              </SvBadge>
            </div>
          </div>

          {isComplete ? <SvCompleteBody/> : isMatching ? <SvMatchingBody/> : <SvRippingBody/>}
        </div>
      </div>
    </SvPanel>
  );
}

function SvRippingBody() {
  const a = useAccent();
  return (
    <>
      <div>
        <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom: 6}}>
          <SvLabel>Overall progress</SvLabel>
          <div style={{fontFamily: sv.mono, fontSize: 11, color: a.secondary, letterSpacing:'0.14em'}}>
            {pct(SAMPLE_JOB.overall)}
          </div>
        </div>
        <SvBar value={SAMPLE_JOB.overall} height={4}/>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr 1fr', gap: 18}}>
        <SvStat label="Speed"  value={`${SAMPLE_JOB.speedX}×`} sub={`${SAMPLE_JOB.speedMBs} MB/s`}/>
        <SvStat label="ETA"    value={`${SAMPLE_JOB.etaMin} min`} sub="remaining"/>
        <SvStat label="Tracks" value={`${SAMPLE_JOB.tracksDone}/${SAMPLE_JOB.tracksTotal}`} sub="completed"/>
        <SvStat label="Buffer" value="1.2 GB" sub="of 8.0"/>
      </div>

      <div>
        <div style={{display:'flex', justifyContent:'space-between', marginBottom: 10}}>
          <SvLabel>Track status</SvLabel>
          <SvLabel color={sv.inkFaint} caret={false}>CLICK TO EXPAND</SvLabel>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap: 8}}>
          {SAMPLE_TRACKS.map(t => <SvTrackCard key={t.id} t={t} mode="rip"/>)}
        </div>
      </div>
    </>
  );
}

function SvMatchingBody() {
  return (
    <>
      <div>
        <SvLabel color={sv.amber}>Matching episodes · audio + chapter fingerprint</SvLabel>
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr 1fr', gap: 18}}>
        <SvStat label="Matched"     value="1/8" sub="high confidence"/>
        <SvStat label="In progress" value="1"   sub="analyzing"/>
        <SvStat label="Pending"     value="6"   sub="awaiting"/>
        <SvStat label="Ambiguous"   value="0"   sub="needs review"/>
      </div>
      <div>
        <div style={{display:'flex', justifyContent:'space-between', marginBottom: 10}}>
          <SvLabel>Match results</SvLabel>
          <SvLabel color={sv.inkFaint} caret={false}>CLICK TO REVIEW</SvLabel>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap: 8}}>
          {MATCHING_TRACKS.map(t => <SvTrackCard key={t.id} t={t} mode="match"/>)}
        </div>
      </div>
    </>
  );
}

function SvCompleteBody() {
  return (
    <>
      <div style={{display:'flex', alignItems:'center', gap: 12}}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={sv.green} strokeWidth="2">
          <path d="M5 12l5 5L20 7"/>
        </svg>
        <div style={{fontFamily: sv.mono, fontSize: 11, color: sv.green, letterSpacing:'0.18em'}}>
          ARCHIVED · 8 EPISODES · 7.8 GB · 00:04:12
        </div>
      </div>
      <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, lineHeight: 1.8, letterSpacing:'0.06em',
        background:'rgba(94,234,212,0.04)', border:`1px solid ${sv.line}`, padding: 12}}>
        <div style={{color: sv.cyan}}>› /library/tv/Arrested Development/Season 01/</div>
        {['Pilot','Top Banana','Bringing Up Buster','Key Decisions','Visiting Ours','Charity Drive','In God We Trust','My Mother the Car']
          .slice(0,5).map((ep,i) => (
            <div key={i}>{'   '}├─ Arrested Development · S01E0{i+1} · {ep}.mkv
              <span style={{color: sv.inkFaint}}> · {['1.01','0.98','0.97','0.99','1.02'][i]} GB</span></div>
        ))}
        <div style={{color: sv.inkFaint}}>{'   '}…3 more</div>
      </div>
    </>
  );
}

function SvStat({ label, value, sub }) {
  return (
    <div>
      <SvLabel>{label}</SvLabel>
      <div style={{fontFamily: sv.display, fontWeight: 600, fontSize: 22,
        color: sv.ink, marginTop: 6, letterSpacing:'0.02em'}}>{value}</div>
      <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, letterSpacing:'0.15em',
        marginTop: 2}}>{sub}</div>
    </div>
  );
}

function SvTrackCard({ t, mode }) {
  const a = useAccent();
  const [hover, setHover] = React.useState(false);
  const active = t.state === 'ripping' || t.state === 'matching' || t.state === 'matched';

  const accent = mode === 'match'
    ? (t.state==='matched' ? sv.green : t.state==='matching' ? sv.amber : null)
    : (t.state==='ripping' ? sv.magenta : null);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `1px solid ${hover ? a.primary : accent || sv.line}`,
        background: hover ? 'rgba(94,234,212,0.06)' : accent ? `${accent}0c` : 'rgba(18,24,39,0.4)',
        padding: 12, position:'relative', minHeight: 84, cursor:'pointer',
        transition: 'all 0.15s ease',
        transform: hover ? 'translateY(-1px)' : 'none',
        boxShadow: hover ? `0 4px 16px ${a.primary}22` : 'none',
      }}>
      <SvCorners color={hover ? `${a.primary}aa` : sv.lineMid}/>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
        <div style={{fontFamily: sv.mono, fontSize: 11, color: sv.ink, fontWeight: 500, letterSpacing:'0.08em'}}>
          {mode === 'match' && t.state === 'matched' ? t.ep : `Title ${t.id}`}
        </div>
        <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing:'0.1em'}}>
          {t.runtime}
        </div>
      </div>
      {mode === 'rip' && t.state === 'ripping' && (
        <>
          <div style={{marginTop: 10}}><SvBar value={t.progress} color={sv.magenta} height={3}/></div>
          <div style={{display:'flex', justifyContent:'space-between', marginTop: 6,
            fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.15em'}}>
            <span style={{color: sv.magenta, animation: 'svPulse 1.2s infinite'}}>RIPPING</span>
            <span style={{color: sv.inkDim}}>{mbOf(t.size)} / {gbOf(t.total)}</span>
            <span style={{color: sv.magentaHi}}>{pct(t.progress)}</span>
          </div>
          {/* sweeping light */}
          <div style={{position:'absolute', inset: 0, pointerEvents:'none', overflow:'hidden'}}>
            <div style={{position:'absolute', top: 0, bottom: 0, width: 40,
              background:`linear-gradient(90deg, transparent, ${sv.magenta}33, transparent)`,
              animation:'svSweep 2.2s ease-in-out infinite'}}/>
          </div>
        </>
      )}
      {mode === 'rip' && t.state === 'queued' && (
        <div style={{marginTop: 14, fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing:'0.18em'}}>
          QUEUED
        </div>
      )}
      {mode === 'match' && t.state === 'matched' && (
        <div style={{marginTop: 10, fontFamily: sv.mono, fontSize: 9, color: sv.green, letterSpacing:'0.16em'}}>
          ✓ CONFIDENCE · {pct(t.confidence)}
        </div>
      )}
      {mode === 'match' && t.state === 'matching' && (
        <>
          <div style={{marginTop: 8}}><SvBar value={t.progress} color={sv.amber} height={3}/></div>
          <div style={{marginTop: 6, display:'flex', flexDirection:'column', gap: 2}}>
            {t.candidates.slice(0,2).map((c,i) => (
              <div key={i} style={{display:'flex', justifyContent:'space-between',
                fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.08em',
                color: i === 0 ? sv.amber : sv.inkDim}}>
                <span>{i === 0 ? '▸ ' : '  '}{c.ep}</span><span>{pct(c.score)}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {mode === 'match' && t.state === 'pending' && (
        <div style={{marginTop: 14, fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing:'0.18em'}}>
          PENDING
        </div>
      )}
    </div>
  );
}

// disc art — spinning rings, tracked head
function SvDiscArt({ state }) {
  const spinning = state === 'ripping' || state === 'scanning';
  return (
    <div style={{
      width: 180, height: 220, flexShrink: 0, position:'relative', overflow:'hidden',
      border: `1px solid ${sv.lineMid}`,
      background:'radial-gradient(ellipse at 50% 40%, rgba(94,234,212,0.08), transparent 60%), #050810',
    }}>
      <SvCorners color={sv.lineHi}/>
      <div style={{position:'absolute', top: 10, left: 10,
        padding:'3px 8px', border:`1px solid ${sv.line}`, background:'rgba(0,0,0,0.4)',
        fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.22em', color: sv.cyan}}>
        TV
      </div>
      <div style={{position:'absolute', top: 10, right: 10,
        fontFamily: sv.mono, fontSize: 8, letterSpacing:'0.2em', color: sv.inkFaint}}>
        BD50
      </div>
      <svg viewBox="0 0 140 140" style={{position:'absolute', top: 34, left: 20, width: 140, height: 140,
        animation: spinning ? 'svSpin 5s linear infinite' : 'none'}}>
        <circle cx="70" cy="70" r="65" fill="none" stroke={sv.cyan} strokeWidth="0.6" opacity="0.7"/>
        <circle cx="70" cy="70" r="55" fill="none" stroke={sv.cyan} strokeWidth="0.4" opacity="0.4"/>
        <circle cx="70" cy="70" r="44" fill="none" stroke={sv.cyan} strokeWidth="0.4" opacity="0.3"/>
        <circle cx="70" cy="70" r="30" fill="none" stroke={sv.cyan} strokeWidth="0.4" opacity="0.25"/>
        <circle cx="70" cy="70" r="14" fill="none" stroke={sv.cyan} strokeWidth="1"/>
        <circle cx="70" cy="70" r="4" fill={sv.cyan}/>
        <circle cx="70" cy="70" r="1.5" fill={sv.bg0}/>
        {/* data track sweep */}
        <path d="M 70 5 A 65 65 0 0 1 115 35" stroke={sv.magenta} strokeWidth="1.2" fill="none"/>
      </svg>
      <div style={{position:'absolute', inset:0, pointerEvents:'none', backgroundImage:
        'repeating-linear-gradient(0deg, rgba(94,234,212,0.04) 0 1px, transparent 1px 3px)'}}/>
    </div>
  );
}

// side rail — live kinetic type + system telemetry
function SvSideRail({ state }) {
  return (
    <SvPanel pad={16} style={{display:'flex', flexDirection:'column', gap: 14, overflow:'hidden'}}>
      <SvLabel>System · Live</SvLabel>
      <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'6px 12px',
        fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.14em'}}>
        <span style={{color: sv.inkFaint}}>CPU</span>  <span style={{color: sv.cyan}}>34% · 6C/12T</span>
        <span style={{color: sv.inkFaint}}>GPU</span>  <span style={{color: sv.inkDim}}>IDLE</span>
        <span style={{color: sv.inkFaint}}>MEM</span>  <span style={{color: sv.cyan}}>1.2 / 8.0 GB</span>
        <span style={{color: sv.inkFaint}}>DISK</span> <span style={{color: sv.cyan}}>162 / 2048 GB</span>
        <span style={{color: sv.inkFaint}}>NET</span>  <span style={{color: sv.green}}>◉ ONLINE</span>
        <span style={{color: sv.inkFaint}}>TEMP</span> <span style={{color: sv.cyan}}>42°C · OK</span>
      </div>
      <SvRuler ticks={20}/>
      <SvLabel>Recent Activity</SvLabel>
      <div style={{display:'flex', flexDirection:'column', gap: 8, fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.08em'}}>
        {SV_HISTORY.slice(0, 5).map(h => (
          <div key={h.id} style={{display:'flex', gap: 8, color: sv.inkDim, lineHeight: 1.4}}>
            <span style={{color: sv.inkFaint, flexShrink: 0}}>{h.at}</span>
            <span style={{color: h.color === 'green' ? sv.green : h.color === 'yellow' ? sv.yellow : sv.cyan,
              flexShrink: 0}}>{h.tag}</span>
            <span style={{whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>{h.msg}</span>
          </div>
        ))}
      </div>
      <SvRuler ticks={20}/>
      <SvLabel>Queue</SvLabel>
      <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing:'0.08em'}}>
        <div style={{color: sv.magenta}}>▸ Arrested Development S01D1</div>
        <div style={{color: sv.inkFaint, marginTop: 4}}>  ↳ 3 review items pending</div>
      </div>
    </SvPanel>
  );
}

// ── MINIMAL — monumental single focus ───────────────────────────────────────
function SvDashboardMin({ state }) {
  const a = useAccent();
  const isMatching = state === 'matching';
  const isComplete = state === 'complete';
  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="dashboard"/>
        <div style={{flex: 1, display:'grid', gridTemplateColumns:'360px 1fr 360px', minHeight: 0}}>
          {/* left gutter */}
          <div style={{padding: 40, borderRight: `1px solid ${sv.line}`,
            display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
            <div>
              <SvLabel style={{marginBottom: 18}}>Subject</SvLabel>
              <div style={{fontFamily: sv.display, fontSize: 36, fontWeight: 700,
                color: a.primaryHi, letterSpacing:'0.04em', lineHeight: 1.05,
                textShadow: `0 0 16px ${a.primary}55`}}>
                {SAMPLE_JOB.title}
              </div>
              <div style={{height: 20}}/>
              <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'10px 18px',
                fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em'}}>
                <span style={{color: sv.inkFaint}}>FORMAT</span><span style={{color: sv.ink}}>TV · SEASON 01</span>
                <span style={{color: sv.inkFaint}}>MEDIUM</span><span style={{color: sv.ink}}>BD50 · DISC 1</span>
                <span style={{color: sv.inkFaint}}>TITLES</span><span style={{color: sv.ink}}>8 DETECTED</span>
                <span style={{color: sv.inkFaint}}>YEAR</span>  <span style={{color: sv.ink}}>2003</span>
                <span style={{color: sv.inkFaint}}>TMDB</span>  <span style={{color: a.primaryHi}}>#4589</span>
              </div>
            </div>
            <SvRuler ticks={30}/>
          </div>
          {/* center monumental */}
          <div style={{padding: 40, display:'flex', flexDirection:'column', alignItems:'center',
            justifyContent:'center', gap: 20, position:'relative'}}>
            <SvLabel color={a.primary}>— {isComplete?'Archive Committed':isMatching?'Fingerprint · In Progress':'Extraction · In Progress'} —</SvLabel>
            <div style={{fontFamily: sv.display, fontSize: 220, fontWeight: 700,
              color: a.primaryHi, lineHeight: 0.9, letterSpacing:'-0.02em',
              textShadow: `0 0 40px ${a.primary}66, 0 0 80px ${a.primary}22`}}>
              {isComplete ? '8/8' : isMatching ? '1/8' : `${Math.round(SAMPLE_JOB.overall*100)}%`}
            </div>
            <div style={{width: '70%'}}>
              <SvBar value={isComplete ? 1 : isMatching ? 0.125 : SAMPLE_JOB.overall} height={4}/>
            </div>
            <SvLabel color={sv.inkDim}>
              {isComplete ? 'Episodes recovered · 7.8 GB' : isMatching ? 'Matched · 1 in progress · 6 pending' : `Overall · ${SAMPLE_JOB.speedX}× · ETA ${SAMPLE_JOB.etaMin} min`}
            </SvLabel>
          </div>
          {/* right track strip */}
          <div style={{padding: 40, borderLeft:`1px solid ${sv.line}`}}>
            <SvLabel style={{marginBottom: 18}}>Track · Status</SvLabel>
            <div style={{display:'flex', flexDirection:'column', gap: 10}}>
              {(isMatching?MATCHING_TRACKS:SAMPLE_TRACKS).slice(0,8).map(t => (
                <div key={t.id} style={{display:'grid', gridTemplateColumns:'28px 48px 1fr 48px', gap: 10, alignItems:'center'}}>
                  <div style={{fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em',
                    color: t.state==='ripping'||t.state==='matching'||t.state==='matched' ? a.primary : sv.inkFaint}}>
                    {String(t.id).padStart(2,'0')}
                  </div>
                  <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing:'0.1em'}}>
                    {t.runtime}
                  </div>
                  <SvBar value={t.progress || (t.state==='matched'?1:0)} height={2}/>
                  <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkDim, textAlign:'right', letterSpacing:'0.1em'}}>
                    {t.state==='matched' ? '✓' : t.state==='ripping'||t.state==='matching' ? pct(t.progress) : '—'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

// ── DENSE — Bloomberg-style terminal ────────────────────────────────────────
function SvDashboardDense({ state }) {
  const a = useAccent();
  const isMatching = state === 'matching';
  const isComplete = state === 'complete';
  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="dashboard"/>
        <div style={{flex: 1, display:'grid', gridTemplateColumns:'240px 1fr 240px',
          gap: 1, background: sv.line, minHeight: 0}}>

          {/* left rail — system */}
          <div style={{background: sv.bg0, padding:'12px 14px', overflow:'auto'}}>
            <SvLabel style={{marginBottom: 10}}>System</SvLabel>
            <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'4px 10px',
              fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.1em'}}>
              {[['CPU','34% · 6C/12T', sv.cyan],['GPU','IDLE',sv.inkDim],['MEM','1.2/8.0 GB',sv.cyan],
                ['DISK','162/2048 GB',sv.cyan],['NET','ONLINE',sv.green],['TEMP','42°C',sv.cyan],
                ['DRIVE E','READY',sv.green],['WS','CONNECTED',sv.green],
                ['TMDB','ONLINE',sv.green],['TRAKT','ONLINE',sv.green]].map(([k,v,c]) => (
                <React.Fragment key={k}>
                  <span style={{color: sv.inkFaint}}>{k}</span>
                  <span style={{color: c}}>{v}</span>
                </React.Fragment>
              ))}
            </div>
            <div style={{height: 14}}/>
            <SvRuler ticks={14}/>
            <div style={{height: 10}}/>
            <SvLabel style={{marginBottom: 10}}>Queue</SvLabel>
            <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkDim, lineHeight: 1.7}}>
              <div style={{color: sv.magenta}}>▸ ARRESTED DEV S01D1</div>
              <div style={{color: sv.inkFaint, marginLeft: 10}}>ripping · 00:21</div>
              <div style={{marginTop: 6, color: sv.inkFaint}}>· cowboy bebop d2</div>
              <div style={{marginLeft: 10, color: sv.inkGhost}}>queued</div>
              <div style={{color: sv.inkFaint}}>· the matrix</div>
              <div style={{marginLeft: 10, color: sv.inkGhost}}>queued</div>
            </div>
            <div style={{height: 14}}/>
            <SvRuler ticks={14}/>
            <div style={{height: 10}}/>
            <SvLabel style={{marginBottom: 10}}>Today</SvLabel>
            <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkDim, lineHeight: 1.7}}>
              <div>RIPS · 1</div>
              <div>MATCHED · 1/8</div>
              <div>ARCHIVED · 0</div>
              <div>REVIEWED · 0</div>
              <div>THROUGHPUT · 34 MB/s</div>
            </div>
          </div>

          {/* center — job detail */}
          <div style={{background: sv.bg0, padding: 14, overflow:'auto',
            display:'flex', flexDirection:'column', gap: 10}}>
            <SvJobCard state={state}/>
            <SvPanel pad={12}>
              <div style={{display:'flex', justifyContent:'space-between'}}>
                <SvLabel>Telemetry · Throughput</SvLabel>
                <SvLabel color={sv.inkFaint} caret={false}>5 MIN ·  SPARKLINE</SvLabel>
              </div>
              <div style={{height: 10}}/>
              <SvSparkline/>
            </SvPanel>
          </div>

          {/* right rail — history */}
          <div style={{background: sv.bg0, padding:'12px 14px', overflow:'auto'}}>
            <SvLabel style={{marginBottom: 10}}>Activity · Live</SvLabel>
            <div style={{display:'flex', flexDirection:'column', gap: 6}}>
              {SV_HISTORY.map(h => (
                <div key={h.id} style={{fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.06em', lineHeight: 1.5}}>
                  <div style={{display:'flex', gap: 6}}>
                    <span style={{color: sv.inkFaint}}>{h.at}</span>
                    <span style={{color: h.color === 'green' ? sv.green : h.color === 'yellow' ? sv.yellow : sv.cyan}}>
                      {h.tag}
                    </span>
                  </div>
                  <div style={{color: sv.inkDim, marginLeft: 0}}>{h.msg}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

// mini sparkline for the dense dashboard
function SvSparkline() {
  const pts = [0.2,0.4,0.35,0.55,0.6,0.5,0.7,0.68,0.78,0.82,0.75,0.88,0.92,0.86,0.95];
  const w = 600, h = 60;
  const path = pts.map((p,i) => `${i===0?'M':'L'} ${(i/(pts.length-1))*w} ${h - p*h}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none">
      <defs>
        <linearGradient id="sv-spark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={sv.cyan} stopOpacity="0.5"/>
          <stop offset="100%" stopColor={sv.cyan} stopOpacity="0"/>
        </linearGradient>
      </defs>
      <path d={`${path} L ${w} ${h} L 0 ${h} Z`} fill="url(#sv-spark)"/>
      <path d={path} fill="none" stroke={sv.cyan} strokeWidth="1.2"/>
      {pts.map((p,i) => i===pts.length-1 && (
        <circle key={i} cx={(i/(pts.length-1))*w} cy={h - p*h} r="3" fill={sv.magenta}>
          <animate attributeName="r" values="2;5;2" dur="1.5s" repeatCount="indefinite"/>
        </circle>
      ))}
    </svg>
  );
}

Object.assign(window, {
  SvDashboard, SvDashboardMin, SvDashboardMed, SvDashboardDense,
  SvJobCard, SvTrackCard, SvDiscArt, SvSideRail, SvStat,
});

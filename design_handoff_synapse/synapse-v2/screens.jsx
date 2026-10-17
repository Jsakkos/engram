/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — EXTENDED SCREENS
   Screens beyond the dashboard: disc-insert transition, review queue,
   review detail (candidate picker), library browser, library detail,
   history log, and error/empty states.
   Each respects density + color balance via SvCtx.
   ═══════════════════════════════════════════════════════════════════════════ */

// helpers (small utilities shared across screens) ───────────────────────────
const pct = v => `${Math.round((v || 0) * 100)}%`;
const mbOf = v => `${Math.round(v * 1024)} MB`;
const gbOf = v => `${v.toFixed(2)} GB`;

// ── DISC INSERT → CLASSIFICATION (animated) ─────────────────────────────────
// A four-phase reveal: detect → scan → classify → ready. On 'classify' the
// system proposes an identification and asks user to confirm.
function SvDiscInsert({ phase = 'classify' }) {
  const a = useAccent();
  const phases = ['detect', 'scan', 'classify', 'ready'];
  const idx = phases.indexOf(phase);

  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="dashboard"/>

        <div style={{flex: 1, display:'grid', gridTemplateColumns:'1fr 1.2fr',
          gap: 28, padding: 28, minHeight: 0}}>

          {/* LEFT — big animated disc */}
          <div style={{
            position:'relative', border: `1px solid ${sv.lineMid}`,
            background:'radial-gradient(ellipse at 50% 40%, rgba(94,234,212,0.08), transparent 60%), rgba(5,7,12,0.7)',
            display:'flex', alignItems:'center', justifyContent:'center', overflow:'hidden',
          }}>
            <SvCorners color={sv.lineHi}/>
            {/* radar ring */}
            <svg viewBox="0 0 400 400" style={{width:'80%', maxHeight:'80%'}}>
              <defs>
                <radialGradient id="sv-disc-g" cx="0.5" cy="0.5" r="0.5">
                  <stop offset="0%" stopColor={a.primary} stopOpacity="0.3"/>
                  <stop offset="60%" stopColor={a.primary} stopOpacity="0.05"/>
                  <stop offset="100%" stopColor={a.primary} stopOpacity="0"/>
                </radialGradient>
                <linearGradient id="sv-disc-sweep" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%"  stopColor={a.primary} stopOpacity="0"/>
                  <stop offset="100%" stopColor={a.primary} stopOpacity="0.7"/>
                </linearGradient>
              </defs>
              <circle cx="200" cy="200" r="180" fill="url(#sv-disc-g)"/>
              {[180,150,120,90,60,30].map((r,i) => (
                <circle key={r} cx="200" cy="200" r={r} fill="none"
                  stroke={a.primary} strokeWidth="0.6" opacity={0.2 + i*0.08}/>
              ))}
              {/* crosshairs */}
              <line x1="200" y1="10" x2="200" y2="390" stroke={a.primary} strokeWidth="0.4" opacity="0.3"/>
              <line x1="10" y1="200" x2="390" y2="200" stroke={a.primary} strokeWidth="0.4" opacity="0.3"/>
              {/* spinning sweep — only during scan */}
              {(phase === 'scan' || phase === 'classify') && (
                <g style={{transformOrigin:'200px 200px', animation:'svSpin 3s linear infinite'}}>
                  <path d="M 200 200 L 380 200 A 180 180 0 0 0 200 20 Z" fill="url(#sv-disc-sweep)" opacity="0.5"/>
                </g>
              )}
              {/* center hub */}
              <circle cx="200" cy="200" r="8" fill={a.primary}/>
              <circle cx="200" cy="200" r="3" fill={sv.bg0}/>
              {/* chapter ticks on outer ring */}
              {[...Array(36)].map((_, i) => {
                const ang = (i / 36) * Math.PI * 2;
                const active = i < Math.floor((idx + 1) * 9);
                return (
                  <line key={i}
                    x1={200 + Math.cos(ang) * 180} y1={200 + Math.sin(ang) * 180}
                    x2={200 + Math.cos(ang) * 170} y2={200 + Math.sin(ang) * 170}
                    stroke={active ? a.primaryHi : sv.inkGhost} strokeWidth="1.2"/>
                );
              })}
              {/* pulse flags for detected titles (classify phase) */}
              {(phase === 'classify' || phase === 'ready') && [0, 2, 5, 7, 11, 14, 17, 22].map(t => {
                const ang = (t / 36) * Math.PI * 2 - Math.PI/2;
                return (
                  <circle key={t} cx={200 + Math.cos(ang)*150} cy={200 + Math.sin(ang)*150}
                    r="3" fill={a.primary}>
                    <animate attributeName="opacity" values="1;0.3;1" dur="1.5s" repeatCount="indefinite"
                      begin={`${t*0.05}s`}/>
                  </circle>
                );
              })}
            </svg>

            {/* phase pill */}
            <div style={{position:'absolute', top: 22, left: 22,
              display:'flex', flexDirection:'column', gap: 6}}>
              <SvLabel color={a.primary}>Drive E:\ </SvLabel>
              <div style={{fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.22em',
                color: sv.ink}}>BD50 · 46.2 GB</div>
            </div>

            {/* phase breadcrumb */}
            <div style={{position:'absolute', bottom: 22, left: 22, right: 22,
              display:'flex', flexDirection:'column', gap: 10}}>
              <SvRuler ticks={24}/>
              <div style={{display:'flex', gap: 0}}>
                {phases.map((p, i) => (
                  <div key={p} style={{
                    flex: 1, padding:'8px 10px',
                    borderTop: `2px solid ${i <= idx ? a.primary : sv.line}`,
                    fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.2em',
                    color: i === idx ? a.primaryHi : i < idx ? sv.cyan : sv.inkFaint,
                  }}>
                    <div style={{opacity: 0.6, fontSize: 8}}>0{i+1}</div>
                    {p.toUpperCase()}
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* RIGHT — classification panel */}
          <SvClassifyPanel phase={phase}/>
        </div>

        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

function SvClassifyPanel({ phase }) {
  const a = useAccent();
  return (
    <SvPanel pad={24} glow style={{display:'flex', flexDirection:'column', gap: 18, overflow:'hidden'}}>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
        <SvLabel color={a.primary}>Disc · classification</SvLabel>
        <SvBadge state="scanning">ANALYZING</SvBadge>
      </div>

      <div>
        <SvLabel style={{marginBottom: 6}}>Best match · 0.94 confidence</SvLabel>
        <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 38,
          color: a.primaryHi, letterSpacing:'0.04em', lineHeight: 1.05,
          textShadow: `0 0 18px ${a.primary}55`}}>
          Arrested Development
        </div>
        <div style={{display:'flex', gap: 12, marginTop: 8, fontFamily: sv.mono, fontSize: 11,
          color: sv.inkDim, letterSpacing:'0.18em'}}>
          <span>TV · SEASON 01 · DISC 1</span><span style={{color: sv.inkFaint}}>·</span>
          <span>2003 · FOX</span><span style={{color: sv.inkFaint}}>·</span>
          <span style={{color: a.primary}}>TMDB #4589</span>
        </div>
      </div>

      <SvRuler ticks={30}/>

      <div>
        <SvLabel style={{marginBottom: 10}}>Detected signals</SvLabel>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px 20px'}}>
          {[
            ['Runtime pattern',  '8 × ~22 min',  0.92],
            ['Volume label',     'AD_S1_D1',     0.98],
            ['Audio fingerprint','tv theme match', 0.89],
            ['Chapter markers',  '14–18 per title', 0.86],
          ].map(([k, v, c]) => (
            <div key={k}>
              <div style={{display:'flex', justifyContent:'space-between', marginBottom: 4,
                fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.14em'}}>
                <span style={{color: sv.inkDim}}>{k}</span>
                <span style={{color: a.primaryHi}}>{pct(c)}</span>
              </div>
              <SvBar value={c} height={2}/>
              <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint,
                marginTop: 4, letterSpacing:'0.06em'}}>{v}</div>
            </div>
          ))}
        </div>
      </div>

      <SvRuler ticks={30}/>

      <div>
        <SvLabel style={{marginBottom: 10}}>Other candidates</SvLabel>
        <div style={{display:'flex', flexDirection:'column', gap: 6}}>
          {[
            ['Arrested Development · S01 Vol 1', 0.81],
            ['Arrested Development · Complete Box', 0.68],
            ['Unknown · Manual entry', 0.10],
          ].map(([l, c]) => (
            <div key={l} style={{display:'flex', justifyContent:'space-between',
              padding:'6px 10px', border:`1px solid ${sv.line}`,
              fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing:'0.08em',
              cursor:'pointer'}}>
              <span>{l}</span><span style={{color: sv.inkFaint}}>{pct(c)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* actions */}
      <div style={{marginTop:'auto', display:'flex', gap: 10, justifyContent:'flex-end'}}>
        <button style={{background:'transparent', border:`1px solid ${sv.lineMid}`,
          color: sv.inkDim, padding:'10px 18px', cursor:'pointer',
          fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.24em'}}>EJECT</button>
        <button style={{background:'transparent', border:`1px solid ${sv.lineMid}`,
          color: sv.ink, padding:'10px 18px', cursor:'pointer',
          fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.24em'}}>EDIT · MANUAL</button>
        <button style={{background: a.primary, border:`1px solid ${a.primary}`,
          color: sv.bg0, padding:'10px 18px', cursor:'pointer',
          fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.24em', fontWeight: 700,
          boxShadow: `0 0 14px ${a.primary}55`}}>CONFIRM · BEGIN RIP</button>
      </div>
    </SvPanel>
  );
}

// ── REVIEW QUEUE ────────────────────────────────────────────────────────────
function SvReviewQueue() {
  const a = useAccent();
  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="review"/>

        {/* header strip */}
        <div style={{padding:'22px 28px', borderBottom:`1px solid ${sv.line}`,
          display:'flex', justifyContent:'space-between', alignItems:'flex-end'}}>
          <div>
            <SvLabel color={sv.yellow}>Needs attention</SvLabel>
            <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 30,
              color: sv.ink, letterSpacing:'0.04em', marginTop: 6}}>
              3 items pending review
            </div>
            <div style={{fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint,
              letterSpacing:'0.18em', marginTop: 4}}>
              AMBIGUOUS MATCHES · MISSING METADATA · EXCEPTIONS
            </div>
          </div>
          <div style={{display:'flex', gap: 10}}>
            <SvIconBtn>FILTER · ALL</SvIconBtn>
            <SvIconBtn>SORT · PRIORITY</SvIconBtn>
          </div>
        </div>

        <div style={{flex: 1, display:'grid', gridTemplateColumns:'1.1fr 1.4fr',
          gap: 20, padding: 20, minHeight: 0}}>
          {/* queue list */}
          <div style={{display:'flex', flexDirection:'column', gap: 12, overflow:'auto', paddingRight: 4}}>
            {SV_REVIEW_QUEUE.map((q, i) => (
              <SvReviewQueueCard key={q.id} q={q} active={i === 0}/>
            ))}
            <div style={{marginTop: 10, padding: 20, border:`1px dashed ${sv.line}`,
              textAlign:'center', fontFamily: sv.mono, fontSize: 10,
              color: sv.inkFaint, letterSpacing:'0.2em'}}>
              END · NO FURTHER ITEMS
            </div>
          </div>
          {/* detail — candidate picker for selected item */}
          <SvReviewDetail/>
        </div>

        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

function SvReviewQueueCard({ q, active }) {
  const a = useAccent();
  return (
    <div style={{
      padding: 16, position:'relative', cursor:'pointer',
      border:`1px solid ${active ? a.primary : sv.lineMid}`,
      background: active ? 'rgba(94,234,212,0.06)' : 'rgba(18,24,39,0.6)',
      boxShadow: active ? `0 0 16px ${a.primary}22` : 'none',
    }}>
      <SvCorners color={active ? a.primary : sv.lineMid}/>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom: 10}}>
        <div style={{display:'flex', gap: 10, alignItems:'center'}}>
          <SvBadge state={q.state}>{q.state === 'error' ? 'ERROR' : 'REVIEW'}</SvBadge>
          <span style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint,
            letterSpacing:'0.18em'}}>{q.type.toUpperCase()}</span>
        </div>
        <span style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint,
          letterSpacing:'0.16em'}}>{q.flagged}/{q.tracks} FLAGGED</span>
      </div>
      <div style={{fontFamily: sv.display, fontWeight: 600, fontSize: 18,
        color: active ? a.primaryHi : sv.ink, letterSpacing:'0.04em', marginBottom: 6}}>
        {q.title}
      </div>
      <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkDim,
        letterSpacing:'0.08em'}}>
        ↳ {q.need}
      </div>
    </div>
  );
}

function SvReviewDetail() {
  const a = useAccent();
  const [selected, setSelected] = React.useState(0);
  return (
    <SvPanel pad={20} glow style={{display:'flex', flexDirection:'column', gap: 16, overflow:'hidden'}}>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start'}}>
        <div>
          <SvLabel color={a.primary}>Track · under review</SvLabel>
          <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 22,
            color: a.primaryHi, letterSpacing:'0.04em', marginTop: 6}}>
            Title 3 · 00:22:14
          </div>
          <div style={{fontFamily: sv.mono, fontSize: 11, color: sv.inkDim,
            letterSpacing:'0.14em', marginTop: 4}}>
            COWBOY BEBOP · DISC 2 · BD50 · CHAPTER COUNT 8
          </div>
        </div>
        <SvBadge state="warn">AMBIGUOUS</SvBadge>
      </div>

      <SvRuler ticks={28}/>

      <div>
        <SvLabel style={{marginBottom: 10}}>Candidate episodes</SvLabel>
        <div style={{display:'flex', flexDirection:'column', gap: 8}}>
          {SV_CANDIDATES.map((c, i) => (
            <div key={i} onClick={() => setSelected(i)} style={{
              padding: 12, cursor:'pointer', position:'relative',
              border:`1px solid ${selected === i ? a.primary : sv.line}`,
              background: selected === i ? 'rgba(94,234,212,0.08)' : 'rgba(10,14,24,0.5)',
              transition:'all 0.15s',
            }}>
              {selected === i && <SvCorners color={a.primary}/>}
              <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom: 8}}>
                <div style={{display:'flex', gap: 10, alignItems:'center'}}>
                  <span style={{fontFamily: sv.mono, fontSize: 10,
                    color: selected === i ? a.primaryHi : sv.inkFaint, letterSpacing:'0.2em'}}>
                    {selected === i ? '▸ SELECTED' : `· ${String(i+1).padStart(2,'0')}`}
                  </span>
                  <span style={{fontFamily: sv.display, fontSize: 15, fontWeight: 600,
                    color: selected === i ? sv.ink : sv.inkDim, letterSpacing:'0.04em'}}>
                    {c.ep}
                  </span>
                </div>
                <span style={{fontFamily: sv.mono, fontSize: 14, color: a.primaryHi,
                  letterSpacing:'0.1em'}}>{pct(c.score)}</span>
              </div>
              <SvBar value={c.score} height={2}/>
              <div style={{display:'flex', gap: 14, marginTop: 8}}>
                {c.sources.map(s => (
                  <span key={s} style={{fontFamily: sv.mono, fontSize: 9,
                    color: sv.inkFaint, letterSpacing:'0.12em'}}>· {s}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{display:'flex', gap: 10, marginTop: 'auto', justifyContent:'flex-end'}}>
        <SvIconBtn>SKIP · LATER</SvIconBtn>
        <SvIconBtn>MARK UNMATCHED</SvIconBtn>
        <button style={{background: a.primary, border:`1px solid ${a.primary}`,
          color: sv.bg0, padding:'10px 18px', cursor:'pointer',
          fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.24em', fontWeight: 700,
          boxShadow: `0 0 14px ${a.primary}55`}}>CONFIRM MATCH</button>
      </div>
    </SvPanel>
  );
}

// ── LIBRARY BROWSER ─────────────────────────────────────────────────────────
function SvLibrary() {
  const a = useAccent();
  const { density } = useSv();
  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="library"/>

        <div style={{padding:'20px 28px', borderBottom:`1px solid ${sv.line}`,
          display:'flex', justifyContent:'space-between', alignItems:'center'}}>
          <div>
            <SvLabel color={a.primary}>Library</SvLabel>
            <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 24,
              color: sv.ink, letterSpacing:'0.04em', marginTop: 4}}>
              23 titles · 142 GB archived
            </div>
          </div>
          <div style={{display:'flex', gap: 8}}>
            {['ALL · 23', 'MOVIES · 15', 'TV · 8', 'UHD · 4'].map((t, i) => (
              <div key={t} style={{
                fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em',
                padding:'8px 14px', cursor:'pointer',
                color: i === 0 ? a.primaryHi : sv.inkDim,
                border:`1px solid ${i === 0 ? a.primary : sv.line}`,
                background: i === 0 ? 'rgba(94,234,212,0.06)' : 'transparent',
              }}>{t}</div>
            ))}
          </div>
        </div>

        <div style={{flex: 1, overflow:'auto', padding: 20}}>
          <div style={{display:'grid',
            gridTemplateColumns: density === 'dense' ? 'repeat(6, 1fr)' : 'repeat(4, 1fr)',
            gap: 14}}>
            {SV_LIBRARY.map(item => <SvLibraryCard key={item.id} item={item}/>)}
            <SvLibraryAddCard/>
          </div>
          {/* recent archive log strip */}
          <div style={{marginTop: 24}}>
            <SvLabel style={{marginBottom: 12}}>Recent archives</SvLabel>
            <div style={{display:'flex', flexDirection:'column', gap: 4}}>
              {SV_LIBRARY.slice(0, 5).map((item, i) => (
                <div key={item.id} style={{display:'grid',
                  gridTemplateColumns:'80px 1fr 120px 100px 100px',
                  padding:'10px 14px', alignItems:'center', gap: 14,
                  borderBottom: `1px solid ${sv.line}`,
                  fontFamily: sv.mono, fontSize: 11, color: sv.inkDim, letterSpacing:'0.1em'}}>
                  <span style={{color: sv.inkFaint}}>{item.added.toUpperCase()}</span>
                  <span style={{color: sv.ink}}>{item.title}{item.subtitle ? ` · ${item.subtitle}` : ''}</span>
                  <span style={{color: sv.inkFaint}}>{item.year}</span>
                  <span>{item.quality}</span>
                  <span style={{textAlign:'right', color: a.primary}}>{item.size}</span>
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

function SvLibraryCard({ item }) {
  const a = useAccent();
  const [hover, setHover] = React.useState(false);
  const posterColors = {
    cyan:    [sv.cyan, sv.bg0],
    magenta: [sv.magenta, sv.bg0],
    yellow:  [sv.yellow, sv.bg0],
  };
  const [fg, bg] = posterColors[item.color] || posterColors.cyan;
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position:'relative', cursor:'pointer',
        border: `1px solid ${hover ? a.primary : sv.line}`,
        background:'rgba(18,24,39,0.5)',
        transform: hover ? 'translateY(-2px)' : 'none',
        boxShadow: hover ? `0 8px 24px ${a.primary}33` : 'none',
        transition: 'all 0.18s',
      }}>
      <SvCorners color={hover ? a.primary : sv.lineMid}/>
      {/* poster */}
      <div style={{
        aspectRatio: '2/3',
        background: `linear-gradient(135deg, ${fg}22, ${bg}), radial-gradient(ellipse at 30% 20%, ${fg}44, transparent 60%)`,
        border: `1px solid ${sv.line}`, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
        display:'flex', alignItems:'center', justifyContent:'center', position:'relative',
        overflow:'hidden',
      }}>
        <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 72,
          color: fg, letterSpacing:'0.04em',
          textShadow: `0 0 20px ${fg}66`}}>{item.poster}</div>
        {/* grid overlay */}
        <div style={{position:'absolute', inset: 0, pointerEvents:'none',
          backgroundImage: `linear-gradient(${sv.line} 1px, transparent 1px), linear-gradient(90deg, ${sv.line} 1px, transparent 1px)`,
          backgroundSize: '20px 20px'}}/>
        {/* type pill */}
        <div style={{position:'absolute', top: 8, left: 8,
          fontFamily: sv.mono, fontSize: 9, letterSpacing:'0.22em',
          padding:'3px 6px', background: 'rgba(0,0,0,0.6)', color: fg}}>
          {item.type.toUpperCase()}
        </div>
        {item.added === 'just now' && (
          <div style={{position:'absolute', top: 8, right: 8}}>
            <SvBadge state="live" style={{fontSize: 8, padding:'2px 6px'}}>NEW</SvBadge>
          </div>
        )}
      </div>
      {/* meta */}
      <div style={{padding: 12}}>
        <div style={{fontFamily: sv.display, fontSize: 14, fontWeight: 600,
          color: sv.ink, letterSpacing:'0.03em', whiteSpace:'nowrap',
          overflow:'hidden', textOverflow:'ellipsis'}}>
          {item.title}
        </div>
        {item.subtitle && (
          <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkDim,
            letterSpacing:'0.14em', marginTop: 2}}>{item.subtitle}</div>
        )}
        <div style={{display:'flex', justifyContent:'space-between', marginTop: 8,
          fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing:'0.12em'}}>
          <span>{item.year}</span>
          <span>{item.size}</span>
        </div>
      </div>
    </div>
  );
}

function SvLibraryAddCard() {
  const a = useAccent();
  return (
    <div style={{
      position:'relative', border:`1px dashed ${sv.lineMid}`,
      background:'rgba(10,14,24,0.3)', cursor:'pointer',
      display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center',
      minHeight: 260, gap: 14, color: sv.inkDim,
    }}>
      <div style={{width: 54, height: 54, border:`1px solid ${a.primary}`,
        display:'flex', alignItems:'center', justifyContent:'center',
        color: a.primary, fontSize: 30, fontFamily: sv.mono, fontWeight: 300,
        boxShadow:`inset 0 0 20px ${a.primary}22`}}>+</div>
      <SvLabel color={a.primary}>Insert disc to add</SvLabel>
      <div style={{fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint,
        letterSpacing:'0.15em'}}>DRIVE E: IDLE</div>
    </div>
  );
}

// ── HISTORY / ACTIVITY LOG ──────────────────────────────────────────────────
function SvHistory() {
  const a = useAccent();
  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="history"/>

        <div style={{padding:'20px 28px', borderBottom:`1px solid ${sv.line}`,
          display:'flex', justifyContent:'space-between', alignItems:'center'}}>
          <div>
            <SvLabel color={a.primary}>Activity log</SvLabel>
            <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 24,
              color: sv.ink, letterSpacing:'0.04em', marginTop: 4}}>
              System events · last 14 days
            </div>
          </div>
          <div style={{display:'flex', gap: 8}}>
            {['ALL', 'INFO', 'WARN', 'ERROR', 'DONE'].map((t, i) => (
              <div key={t} style={{
                fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.2em',
                padding:'8px 14px', cursor:'pointer',
                color: i === 0 ? a.primaryHi : sv.inkDim,
                border:`1px solid ${i === 0 ? a.primary : sv.line}`,
              }}>{t}</div>
            ))}
          </div>
        </div>

        <div style={{flex: 1, display:'grid', gridTemplateColumns:'1fr 320px',
          gap: 20, padding: 20, minHeight: 0}}>
          {/* log stream */}
          <SvPanel pad={0} style={{overflow:'auto'}}>
            <div style={{padding:'14px 18px', borderBottom:`1px solid ${sv.line}`,
              display:'grid', gridTemplateColumns:'90px 70px 220px 1fr',
              fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint, letterSpacing:'0.22em'}}>
              <span>TIME</span><span>LEVEL</span><span>SUBJECT</span><span>MESSAGE</span>
            </div>
            {SV_HISTORY.map((h, i) => (
              <div key={h.id} style={{
                padding:'12px 18px', display:'grid',
                gridTemplateColumns:'90px 70px 220px 1fr',
                borderBottom:`1px solid ${sv.line}`, alignItems:'center',
                background: i === 0 ? 'rgba(94,234,212,0.04)' : 'transparent',
                fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.06em',
              }}>
                <span style={{color: sv.inkFaint, letterSpacing:'0.1em'}}>{h.at}</span>
                <span style={{color: h.color === 'green' ? sv.green
                  : h.color === 'yellow' ? sv.yellow
                  : h.color === 'cyan' ? sv.cyan : sv.red, letterSpacing:'0.2em'}}>
                  {h.tag}
                </span>
                <span style={{color: sv.ink, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>
                  {h.title}
                </span>
                <span style={{color: sv.inkDim, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>
                  {h.msg}
                </span>
              </div>
            ))}
            <div style={{padding:'20px', textAlign:'center',
              fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, letterSpacing:'0.2em'}}>
              END OF STREAM · 8 EVENTS
            </div>
          </SvPanel>

          {/* stats rail */}
          <div style={{display:'flex', flexDirection:'column', gap: 14}}>
            <SvPanel pad={18}>
              <SvLabel style={{marginBottom: 10}}>Last 14 days</SvLabel>
              <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap: 14}}>
                <SvStat label="Archived" value="7" sub="titles"/>
                <SvStat label="Volume"   value="53.4" sub="GB"/>
                <SvStat label="Matched"  value="94%" sub="auto"/>
                <SvStat label="Flagged"  value="2"   sub="manual"/>
              </div>
            </SvPanel>
            <SvPanel pad={18}>
              <SvLabel style={{marginBottom: 12}}>Throughput · 14d</SvLabel>
              <SvBarChart/>
            </SvPanel>
            <SvPanel pad={18} style={{flex: 1}}>
              <SvLabel style={{marginBottom: 10}}>Distribution</SvLabel>
              <div style={{display:'flex', flexDirection:'column', gap: 10, marginTop: 10}}>
                {[['Movies', 15, 0.65, sv.cyan], ['TV seasons', 8, 0.34, sv.magenta]].map(([l, n, f, c]) => (
                  <div key={l}>
                    <div style={{display:'flex', justifyContent:'space-between', marginBottom: 4,
                      fontFamily: sv.mono, fontSize: 10, letterSpacing:'0.12em'}}>
                      <span style={{color: sv.inkDim}}>{l}</span>
                      <span style={{color: c}}>{n}</span>
                    </div>
                    <SvBar value={f} height={3} color={c}/>
                  </div>
                ))}
              </div>
            </SvPanel>
          </div>
        </div>

        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

function SvBarChart() {
  const data = [0.3, 0.6, 0.45, 0.8, 0.55, 0.35, 0.9, 0.7, 0.5, 0.65, 0.4, 0.85, 0.95, 0.6];
  return (
    <div style={{display:'flex', alignItems:'flex-end', gap: 4, height: 70, marginTop: 4}}>
      {data.map((v, i) => (
        <div key={i} style={{flex: 1, height: `${v * 100}%`,
          background: `linear-gradient(180deg, ${sv.cyan}, ${sv.cyan}33)`,
          boxShadow: i === data.length - 1 ? `0 0 8px ${sv.cyan}` : 'none'}}/>
      ))}
    </div>
  );
}

// ── ERROR / EMPTY STATES ────────────────────────────────────────────────────
function SvErrorState({ kind = 'no-match' }) {
  const a = useAccent();
  const cases = {
    'no-match': {
      tag: 'NO MATCH FOUND',
      title: 'Unable to classify disc',
      subtitle: 'TMDB and audio-fingerprint services returned zero results.',
      details: [
        ['Disc ID',       'unknown · volume label blank'],
        ['Runtime pattern','1 title · 01:42:18 · no chapter markers'],
        ['TMDB',          'no candidates within confidence threshold'],
        ['Trakt',         'no candidates'],
        ['Audio FP',      'no matches in database'],
      ],
      actions: [['EJECT DISC', false], ['ENTER MANUALLY', true]],
      state: 'error',
      color: sv.red,
    },
    'no-drive': {
      tag: 'NO DRIVE DETECTED',
      title: 'Optical drive not available',
      subtitle: 'No suitable drive was found on this host.',
      details: [
        ['Host',        'UNIT 07 · local'],
        ['USB devices', '3 · none optical'],
        ['SATA',        '0 optical devices'],
        ['Fallback',    'Remote rip agent · offline'],
      ],
      actions: [['RETRY SCAN', false], ['CONFIGURE DRIVE', true]],
      state: 'error',
      color: sv.red,
    },
    'empty-library': {
      tag: 'ARCHIVE EMPTY',
      title: 'Your library is waiting',
      subtitle: 'Insert a disc to begin your first archive.',
      details: [
        ['Drive E:',     'ready · 0 discs inserted'],
        ['Library path', '/library · writable · 2048 GB free'],
        ['TMDB',         'online · authenticated'],
        ['Audio FP',     'indexed · 4.2M tracks'],
      ],
      actions: [['VIEW SETTINGS', false], ['READ THE GUIDE', true]],
      state: 'live',
      color: a.primary,
    },
  };
  const c = cases[kind] || cases['no-match'];

  return (
    <SvAtmosphere>
      <div style={{width:'100%', height:'100%', color: sv.ink, fontFamily: sv.sans,
        display:'flex', flexDirection:'column'}}>
        <SvTopBar route="dashboard"/>

        <div style={{flex: 1, display:'grid', gridTemplateColumns:'1.2fr 1fr',
          gap: 40, padding: 60, minHeight: 0, alignItems:'center'}}>

          {/* Big tag, title, subtitle */}
          <div>
            <SvLabel color={c.color}>— {c.tag} —</SvLabel>
            <div style={{fontFamily: sv.display, fontWeight: 700, fontSize: 64,
              color: c.color, letterSpacing:'0.01em', lineHeight: 1.02, marginTop: 20,
              textShadow: `0 0 24px ${c.color}44`, textWrap:'balance'}}>
              {c.title}
            </div>
            <div style={{fontFamily: sv.sans, fontSize: 18, color: sv.inkDim,
              lineHeight: 1.4, marginTop: 18, maxWidth: 520}}>
              {c.subtitle}
            </div>
            <div style={{display:'flex', gap: 12, marginTop: 32}}>
              {c.actions.map(([label, primary]) => (
                <button key={label} style={{
                  background: primary ? a.primary : 'transparent',
                  border: `1px solid ${primary ? a.primary : sv.lineMid}`,
                  color: primary ? sv.bg0 : sv.ink,
                  padding:'12px 22px', cursor:'pointer',
                  fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.24em',
                  fontWeight: primary ? 700 : 400,
                  boxShadow: primary ? `0 0 14px ${a.primary}55` : 'none',
                }}>{label}</button>
              ))}
            </div>
          </div>

          {/* Technical readout panel */}
          <SvPanel pad={22} style={{display:'flex', flexDirection:'column', gap: 14}}>
            <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
              <SvLabel color={c.color}>Diagnostics</SvLabel>
              <SvBadge state={c.state}>{c.tag.split(' ')[0]}</SvBadge>
            </div>
            <SvRuler ticks={28}/>
            <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'10px 18px',
              fontFamily: sv.mono, fontSize: 11, letterSpacing:'0.1em'}}>
              {c.details.map(([k, v]) => (
                <React.Fragment key={k}>
                  <span style={{color: sv.inkFaint, letterSpacing:'0.2em'}}>
                    {k.toUpperCase()}
                  </span>
                  <span style={{color: sv.inkDim}}>{v}</span>
                </React.Fragment>
              ))}
            </div>
            <SvRuler ticks={28}/>
            <div style={{fontFamily: sv.mono, fontSize: 9, color: sv.inkFaint,
              letterSpacing:'0.2em'}}>
              TRACE · {Math.random().toString(36).slice(2,10).toUpperCase()}
            </div>
          </SvPanel>
        </div>

        <SvStatusBar/>
      </div>
    </SvAtmosphere>
  );
}

Object.assign(window, {
  pct, mbOf, gbOf,
  SvDiscInsert, SvClassifyPanel,
  SvReviewQueue, SvReviewQueueCard, SvReviewDetail,
  SvLibrary, SvLibraryCard, SvLibraryAddCard,
  SvHistory, SvBarChart,
  SvErrorState,
});

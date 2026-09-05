/* ═══════════════════════════════════════════════════════════════════════════
   SYNAPSE v2 — FRESH CANVAS APP
   Standalone mount for the Synapse v2 direction. Six screens on one
   DesignCanvas with a Tweaks panel for density, balance, atmosphere,
   workflow state, disc phase, and error case.
   ═══════════════════════════════════════════════════════════════════════════ */

function SvApp() {
  const [values, setTweak] = useTweaks(/*EDITMODE-BEGIN*/{
    "workflowState": "ripping",
    "svDensity": "med",
    "svColorBalance": "balanced",
    "svScanlines": true,
    "svSkyline": true,
    "svErrorCase": "no-match",
    "svDiscPhase": "classify"
  }/*EDITMODE-END*/);

  const state    = values.workflowState   || 'ripping';
  const density  = values.svDensity       || 'med';
  const balance  = values.svColorBalance  || 'balanced';
  const scan     = values.svScanlines     ?? true;
  const skyline  = values.svSkyline       ?? true;
  const errCase  = values.svErrorCase     || 'no-match';
  const phase    = values.svDiscPhase     || 'classify';

  const svCtx = React.useMemo(() => ({
    density, colorBalance: balance, scanlines: scan, skyline,
  }), [density, balance, scan, skyline]);

  const W = 1280, H = 820;

  const Wrap = ({ children }) => (
    <SvCtx.Provider value={svCtx}>
      <div className="artboard-root" style={{width: W, height: H}}>{children}</div>
    </SvCtx.Provider>
  );

  return (
    <>
      <DesignCanvas>
        <DCSection
          id="sv2-flow"
          title="Synapse v2 — Main workflow"
          subtitle={`Density · ${density}  ·  Balance · ${balance}  ·  State · ${state}`}>
          <DCArtboard id="sv2-disc"   label={`01 · Disc insert — ${phase}`} width={W} height={H}>
            <Wrap><SvDiscInsert phase={phase}/></Wrap>
          </DCArtboard>
          <DCArtboard id="sv2-dash"   label={`02 · Dashboard — ${state}`} width={W} height={H}>
            <Wrap><SvDashboard state={state}/></Wrap>
          </DCArtboard>
          <DCArtboard id="sv2-review" label="03 · Review queue"  width={W} height={H}>
            <Wrap><SvReviewQueue/></Wrap>
          </DCArtboard>
          <DCArtboard id="sv2-lib"    label="04 · Library"       width={W} height={H}>
            <Wrap><SvLibrary/></Wrap>
          </DCArtboard>
          <DCArtboard id="sv2-hist"   label="05 · History"       width={W} height={H}>
            <Wrap><SvHistory/></Wrap>
          </DCArtboard>
          <DCArtboard id="sv2-err"    label={`06 · Error — ${errCase}`} width={W} height={H}>
            <Wrap><SvErrorState kind={errCase}/></Wrap>
          </DCArtboard>
        </DCSection>
      </DesignCanvas>

      <TweaksPanel title="Synapse v2">
        <TweakSection label="Workflow">
          <TweakRadio
            label="State"
            value={state}
            onChange={v => setTweak('workflowState', v)}
            options={[
              { value: 'ripping',  label: 'Rip' },
              { value: 'matching', label: 'Match' },
              { value: 'complete', label: 'Done' },
            ]}
          />
        </TweakSection>
        <TweakSection label="Layout">
          <TweakRadio
            label="Density"
            value={density}
            onChange={v => setTweak('svDensity', v)}
            options={[
              { value: 'min',   label: 'Min' },
              { value: 'med',   label: 'Med' },
              { value: 'dense', label: 'Dense' },
            ]}
          />
          <TweakRadio
            label="Balance"
            value={balance}
            onChange={v => setTweak('svColorBalance', v)}
            options={[
              { value: 'balanced', label: 'Both' },
              { value: 'cyan',     label: 'Cyan' },
              { value: 'magenta',  label: 'Mag' },
            ]}
          />
        </TweakSection>
        <TweakSection label="Atmosphere">
          <TweakToggle label="Scanlines"       value={scan}    onChange={v => setTweak('svScanlines', v)}/>
          <TweakToggle label="Distant skyline" value={skyline} onChange={v => setTweak('svSkyline',   v)}/>
        </TweakSection>
        <TweakSection label="Disc insert">
          <TweakRadio
            label="Phase"
            value={phase}
            onChange={v => setTweak('svDiscPhase', v)}
            options={[
              { value: 'detect',   label: 'Detect' },
              { value: 'scan',     label: 'Scan' },
              { value: 'classify', label: 'Class' },
              { value: 'ready',    label: 'Ready' },
            ]}
          />
        </TweakSection>
        <TweakSection label="Error state">
          <TweakRadio
            label="Case"
            value={errCase}
            onChange={v => setTweak('svErrorCase', v)}
            options={[
              { value: 'no-match',      label: 'No match' },
              { value: 'no-drive',      label: 'No drive' },
              { value: 'empty-library', label: 'Empty' },
            ]}
          />
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<SvApp/>);

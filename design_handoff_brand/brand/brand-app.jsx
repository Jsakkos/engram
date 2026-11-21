/* ═══════════════════════════════════════════════════════════════════════════
   Engram — Brand sheet entry
   Wires every brand artboard onto a DesignCanvas. Use the canvas pan/zoom
   and the focus mode to view any one in full.
   ═══════════════════════════════════════════════════════════════════════════ */

function BrandApp() {
  return (
    <DesignCanvas>
      <DCSection id="identity" title="Identity" subtitle="The primary mark, wordmark, and lockups">
        <DCArtboard id="hero" label="01 · Hero" width={1280} height={720}>
          <ArtboardHero/>
        </DCArtboard>
        <DCArtboard id="mark-hero" label="02 · Mark" width={640} height={640}>
          <ArtboardMarkHero/>
        </DCArtboard>
        <DCArtboard id="wordmark" label="03 · Wordmark" width={1040} height={520}>
          <ArtboardWordmark/>
        </DCArtboard>
        <DCArtboard id="lockups" label="04 · Lockups" width={1040} height={640}>
          <ArtboardLockups/>
        </DCArtboard>
        <DCArtboard id="reversed" label="05 · Paper edition" width={1040} height={520}>
          <ArtboardReversed/>
        </DCArtboard>
      </DCSection>

      <DCSection id="system" title="System" subtitle="Geometry, spacing, color, type">
        <DCArtboard id="construction" label="06 · Construction" width={720} height={720}>
          <ArtboardConstruction/>
        </DCArtboard>
        <DCArtboard id="clear-space" label="07 · Clear space + minimums" width={1040} height={520}>
          <ArtboardClearSpace/>
        </DCArtboard>
        <DCArtboard id="color" label="08 · Color" width={1280} height={580}>
          <ArtboardColor/>
        </DCArtboard>
        <DCArtboard id="type" label="09 · Type" width={1280} height={720}>
          <ArtboardType/>
        </DCArtboard>
      </DCSection>

      <DCSection id="applications" title="Applications" subtitle="Where the brand actually lives">
        <DCArtboard id="app-icons" label="10 · App icon" width={1280} height={580}>
          <ArtboardAppIcons/>
        </DCArtboard>
        <DCArtboard id="favicon" label="11 · Favicon + tabs" width={1280} height={420}>
          <ArtboardFavicon/>
        </DCArtboard>
        <DCArtboard id="splash" label="12 · Splash" width={1280} height={720}>
          <ArtboardSplash/>
        </DCArtboard>
        <DCArtboard id="dock" label="13 · Dock states" width={1280} height={420}>
          <ArtboardDock/>
        </DCArtboard>
        <DCArtboard id="terminal" label="14 · Terminal banner" width={1040} height={520}>
          <ArtboardTerminal/>
        </DCArtboard>
      </DCSection>

      <DCSection id="iconography" title="Iconography" subtitle="A consistent line set across status, media, and action">
        <DCArtboard id="ico-status" label="15 · Status icons" width={1280} height={560}>
          <ArtboardIconStatus/>
        </DCArtboard>
        <DCArtboard id="ico-media" label="16 · Media icons" width={1280} height={500}>
          <ArtboardIconMedia/>
        </DCArtboard>
        <DCArtboard id="ico-action" label="17 · Action + nav icons" width={1280} height={620}>
          <ArtboardIconAction/>
        </DCArtboard>
        <DCArtboard id="ico-inuse" label="18 · In use · job card" width={1280} height={620}>
          <ArtboardInUse/>
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<BrandApp/>);

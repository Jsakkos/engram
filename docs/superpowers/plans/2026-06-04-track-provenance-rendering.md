# Per-track provenance rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `DiscCard` per-track cards render match provenance consistently — every matched track shows a provider chip + a normalized confidence, with vote detail only when votes exist and a `FULL-FILE` tag on voteless full-file matches.

**Architecture:** Frontend-only. The adapter (`adapters.ts`) sources the displayed confidence from the reliable `match_confidence` column instead of the votes-gated `match_details`, and derives a `matchMethod` flag. `TrackGrid` renders an icon-only Engram mark (cyan ASR / magenta fingerprint) for Engram-engine sources, text chips for DiscDB/AI/manual, and a `FULL-FILE` method tag. The `MarkMono` brand mark gains a `node` prop for the fingerprint variant. No backend change.

**Tech Stack:** React 18 + TypeScript, Vitest + @testing-library/react, Radix tooltip, Synapse v2 design tokens.

**Design spec:** `docs/superpowers/specs/2026-06-04-track-provenance-rendering-design.md`

---

## File Structure

- **Modify** `frontend/src/app/components/DiscCard.tsx` — add `matchMethod` to the `Track` interface (lines 26–54).
- **Modify** `frontend/src/types/adapters.ts` — source confidence from the column, add `deriveMatchMethod`, add `method` to the `MatchDetails` interface.
- **Modify** `frontend/src/types/__tests__/adapters.test.ts` — provenance fixtures.
- **Modify** `frontend/src/app/components/synapse/MarkMono.tsx` — add `node?: boolean` prop.
- **Modify** `frontend/src/app/components/synapse/__tests__/primitives.test.tsx` — `node` renders a center circle.
- **Modify** `frontend/src/app/components/TrackGrid.tsx` — source-descriptor map, icon/text chip, `FULL-FILE` tag, confidence rendering.
- **Create** `frontend/src/app/components/TrackGrid.test.tsx` — provenance chip rendering.

All commands run from `frontend/`. Single-file test runs use `npx vitest run <path>`.

---

### Task 1: Adapter — confidence from the column + `matchMethod` derivation

**Files:**
- Modify: `frontend/src/app/components/DiscCard.tsx:26-54` (Track interface)
- Modify: `frontend/src/types/adapters.ts`
- Test: `frontend/src/types/__tests__/adapters.test.ts`

- [ ] **Step 1: Add `matchMethod` to the `Track` interface**

In `frontend/src/app/components/DiscCard.tsx`, inside `export interface Track { ... }`, add the field directly after the existing `matchSource?: string;` line (currently line 46):

```ts
  matchSource?: string;
  /** Which Engram matcher produced this result, when distinguishable:
   *  'chunk_vote' (ranked voting, has votes) | 'full_file' (whole-file fallback,
   *  no votes by construction). Undefined for DiscDB/AI/manual matches. */
  matchMethod?: "chunk_vote" | "full_file";
```

- [ ] **Step 2: Write the failing adapter tests**

In `frontend/src/types/__tests__/adapters.test.ts`, append this block at the end of the file (after the last `describe`):

```ts
// ---------------------------------------------------------------------------
// transformDiscTitleToTrack — match provenance
// ---------------------------------------------------------------------------

describe("track provenance mapping", () => {
  function track(title: Partial<DiscTitle>) {
    return transformJobToDiscData(makeJob({ state: "matching" }), [makeTitle(title)])
      .tracks![0];
  }

  it("chunk-vote match: confidence + votes from match_details, method chunk_vote", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E17",
      match_source: "engram",
      match_confidence: 0.71,
      match_details: JSON.stringify({ score: 0.71, vote_count: 3, target_votes: 10 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.71);
    expect(t.finalMatchVotes).toBe(3);
    expect(t.finalMatchTargetVotes).toBe(10);
    expect(t.matchMethod).toBe("chunk_vote");
  });

  it("full-file fallback: confidence from column, no votes, method full_file", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E18",
      match_source: "engram",
      match_confidence: 0.93,
      match_details: JSON.stringify({ method: "full_transcription", score: 0.93 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.93);
    expect(t.finalMatchVotes).toBeUndefined();
    expect(t.matchMethod).toBe("full_file");
  });

  it("discdb match: confidence from column, no method (carries its own chip)", () => {
    const t = track({
      state: "matched",
      matched_episode: "S02E05",
      match_source: "discdb",
      match_confidence: 0.99,
      match_details: JSON.stringify({ source: "discdb", episode: "S02E05" }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.99);
    expect(t.finalMatchVotes).toBeUndefined();
    expect(t.matchMethod).toBeUndefined();
  });

  it("manual match: confidence from column even with no match_details", () => {
    const t = track({
      state: "completed",
      matched_episode: "S02E07",
      match_source: "user",
      match_confidence: 1.0,
      match_details: null,
    });
    expect(t.finalMatchConfidence).toBeCloseTo(1.0);
    expect(t.matchMethod).toBeUndefined();
  });

  it("review best-guess: column is 0, so confidence falls back to match_details", () => {
    const t = track({
      state: "review",
      matched_episode: "S02E09",
      match_source: null,
      match_confidence: 0,
      match_details: JSON.stringify({ score: 0.58, vote_count: 2, target_votes: 10 }),
    });
    expect(t.finalMatchConfidence).toBeCloseTo(0.58);
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npx vitest run src/types/__tests__/adapters.test.ts`
Expected: FAIL — the full-file case gets `finalMatchConfidence === undefined` (current code requires `vote_count`), and `matchMethod` is undefined everywhere (`Track.matchMethod` is never populated).

- [ ] **Step 4: Add `method` to the `MatchDetails` interface**

In `frontend/src/types/adapters.ts`, in the `interface MatchDetails` block (currently lines 177–186), add a `method` field:

```ts
interface MatchDetails {
  runner_ups?: RunnerUp[];
  score?: number;        // raw ranked_voting_score
  confidence?: number;   // calibrated 0-1, reviewer-facing
  vote_count?: number;
  target_votes?: number;
  total_chunks?: number;
  episode?: string;
  reason?: string;
  method?: string;       // e.g. "full_transcription" for the whole-file fallback
}
```

- [ ] **Step 5: Add the `deriveMatchMethod` helper**

In `frontend/src/types/adapters.ts`, add this function immediately after `extractFinalMatchInfo` (ends at line 109):

```ts
/**
 * Which Engram matcher produced this result, inferred from match_details shape.
 * Only Engram-engine ASR sources carry a method worth surfacing — DiscDB / AI /
 * manual matches return undefined (their provider chip already says enough).
 *  - chunk_vote: ranked voting (has vote_count)
 *  - full_file:  whole-file fallback ({method:"full_transcription"}, or a bare
 *                score with no votes)
 */
function deriveMatchMethod(title: DiscTitle): "chunk_vote" | "full_file" | undefined {
  const source = title.match_source;
  if (source && source !== "engram" && source !== "engram_chromaprint") return undefined;
  const details = parseMatchDetails(title);
  if (details.vote_count !== undefined) return "chunk_vote";
  if (details.method === "full_transcription" || details.score !== undefined) return "full_file";
  return undefined;
}
```

- [ ] **Step 6: Source confidence from the column and set `matchMethod`**

In `frontend/src/types/adapters.ts`, in `transformDiscTitleToTrack`, replace the `finalMatchConfidence` line (currently line 143) and add `matchMethod`. The returned object's match block becomes:

```ts
    matchCandidates: extractMatchCandidates(title),
    finalMatch: title.matched_episode || undefined,
    // Displayed confidence comes from the reliable match_confidence COLUMN, which
    // is set on every matched path (ASR result.confidence, DiscDB 0.99, manual
    // 1.0). For REVIEW the column is 0.0, so fall back to the match_details
    // best-guess score. This is what un-breaks the bare full-file card.
    finalMatchConfidence:
      title.match_confidence > 0 ? title.match_confidence : finalMatchInfo?.confidence,
    finalMatchVotes: finalMatchInfo?.votes,
    finalMatchTargetVotes: finalMatchInfo?.targetVotes,
    matchMethod: deriveMatchMethod(title),
```

(Leave the `finalMatchInfo` declaration on line 124 as-is — it still feeds votes and the review fallback.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `npx vitest run src/types/__tests__/adapters.test.ts`
Expected: PASS (all provenance cases plus the pre-existing tests).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/app/components/DiscCard.tsx frontend/src/types/adapters.ts frontend/src/types/__tests__/adapters.test.ts
git commit -m "fix(ui): source track confidence from match_confidence column + derive matchMethod"
```

---

### Task 2: `MarkMono` fingerprint variant (`node` prop)

**Files:**
- Modify: `frontend/src/app/components/synapse/MarkMono.tsx`
- Test: `frontend/src/app/components/synapse/__tests__/primitives.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/app/components/synapse/__tests__/primitives.test.tsx`, add `MarkMono` to the import from `".."` (it is exported from the barrel) and append this `describe` block at the end of the file:

```tsx
describe("MarkMono — fingerprint node", () => {
  it("renders a filled center node when node is set", () => {
    const { container } = render(<MarkMono size={12} color="#ff3d7f" node />);
    // The node is the only <circle> when glow is off.
    expect(container.querySelector("circle")).not.toBeNull();
  });

  it("renders no center node by default", () => {
    const { container } = render(<MarkMono size={12} color="#5eead4" />);
    expect(container.querySelector("circle")).toBeNull();
  });
});
```

Update the import line near the top of the file:

```tsx
import {
  SvAtmosphere,
  SvBadge,
  SvBar,
  SvBarChart,
  SvLabel,
  SvMark,
  MarkMono,
  SvPanel,
  SvRuler,
  SvTelemetryBand,
  sv,
} from "..";
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/app/components/synapse/__tests__/primitives.test.tsx`
Expected: FAIL on the `node` case — `MarkMono` ignores an unknown `node` prop, so no `<circle>` renders. (The "no node by default" case passes because `glow` defaults to false.)

- [ ] **Step 3: Add the `node` prop**

In `frontend/src/app/components/synapse/MarkMono.tsx`, add `node` to `Props` and render the center node. Update the interface:

```tsx
interface Props {
  size?: number;
  color?: string;
  paper?: boolean;
  glow?: boolean;
  /** Render a filled center node — the audio-fingerprint variant of the mark. */
  node?: boolean;
}
```

Update the function signature:

```tsx
export function MarkMono({ size = 32, color, paper = false, glow = false, node = false }: Props) {
```

Then, just before the closing `<title>Engram</title>` line, add the node circle:

```tsx
      {node && <circle cx="32" cy="32" r="5" fill={p} />}
      <title>Engram</title>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/app/components/synapse/__tests__/primitives.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/synapse/MarkMono.tsx frontend/src/app/components/synapse/__tests__/primitives.test.tsx
git commit -m "feat(ui): add fingerprint-node variant to MarkMono"
```

---

### Task 3: TrackGrid — provider chip, method tag, consistent confidence

**Files:**
- Modify: `frontend/src/app/components/TrackGrid.tsx`
- Test (create): `frontend/src/app/components/TrackGrid.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/app/components/TrackGrid.test.tsx`:

```tsx
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrackGrid } from "./TrackGrid";
import type { Track } from "./DiscCard";

function makeTrack(overrides: Partial<Track> = {}): Track {
  return {
    id: "1",
    title: "Hammers and Veils",
    duration: "43:51",
    state: "matched",
    progress: 100,
    ...overrides,
  };
}

describe("TrackGrid — provenance rendering", () => {
  it("full-file engram match: shows confidence, FULL-FILE tag, Engram icon chip, no votes", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            finalMatch: "S02E18",
            finalMatchConfidence: 0.93,
            matchSource: "engram",
            matchMethod: "full_file",
          }),
        ]}
      />,
    );
    // The episode span renders "→ S02E18" as one node, so match a substring.
    expect(screen.getByText(/S02E18/)).toBeInTheDocument();
    expect(screen.getByText("93%")).toBeInTheDocument();
    expect(screen.getByText("FULL-FILE")).toBeInTheDocument();
    expect(screen.getByTestId("source-badge-engram")).toBeInTheDocument();
  });

  it("chunk-vote engram match: shows votes and no FULL-FILE tag", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "The Bracebridge Dinner",
            finalMatch: "S02E17",
            finalMatchConfidence: 0.71,
            finalMatchVotes: 3,
            finalMatchTargetVotes: 10,
            matchSource: "engram",
            matchMethod: "chunk_vote",
          }),
        ]}
      />,
    );
    expect(screen.getByText("71%")).toBeInTheDocument();
    expect(screen.getByText("3/10")).toBeInTheDocument();
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
  });

  it("discdb match: text chip, confidence, no method tag", () => {
    render(
      <TrackGrid
        tracks={[
          makeTrack({
            title: "Nick and Nora",
            finalMatch: "S02E05",
            finalMatchConfidence: 0.99,
            matchSource: "discdb",
          }),
        ]}
      />,
    );
    expect(screen.getByText("DISCDB")).toBeInTheDocument();
    expect(screen.getByText("99%")).toBeInTheDocument();
    expect(screen.queryByText("FULL-FILE")).not.toBeInTheDocument();
  });

  it("matched track with missing match_source still gets an Engram chip", () => {
    render(
      <TrackGrid
        tracks={[makeTrack({ finalMatch: "S02E19", finalMatchConfidence: 0.9 })]}
      />,
    );
    expect(screen.getByTestId("source-badge-engram")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/app/components/TrackGrid.test.tsx`
Expected: FAIL — `FULL-FILE` never renders, the icon chip has no `source-badge-engram` testid for the missing-source case, and the missing-source matched track renders no chip at all.

- [ ] **Step 3: Add imports and the source-descriptor map**

In `frontend/src/app/components/TrackGrid.tsx`, update the imports at the top:

```tsx
import React from "react";
import { motion } from "motion/react";
import { IcoRipping, IcoMatching, IcoComplete, IcoError } from "./icons";
import type { Track, TrackState } from "./DiscCard";
import { sv, SvBadge, SvBar, SvLabel, MarkMono } from "./synapse";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
import { formatBytesBinary } from "../../utils/formatting";
```

Replace the two helpers `matchSourceColor` and `matchSourceLabel` (currently lines 42–52) with a single descriptor map and a `SourceChip` component:

```tsx
type SourceDesc = {
  kind: "icon" | "text";
  label: string;
  tone: string;
  tooltip: string;
  node?: boolean;
};

// Engram-engine sources render the brand mark (icon-only, label on hover);
// external/manual sources render a text chip. Unknown sources fall back to a
// generic purple text chip so nothing renders blank.
const SOURCE_DESC: Record<string, SourceDesc> = {
  engram:             { kind: "icon", label: "ENGRAM", tone: sv.cyan,    tooltip: "Matched by Engram (ASR)" },
  engram_chromaprint: { kind: "icon", label: "ENGRAM", tone: sv.magenta, tooltip: "Matched by Engram (audio fingerprint)", node: true },
  discdb:             { kind: "text", label: "DISCDB", tone: "#60a5fa",  tooltip: "Matched from TheDiscDB" },
  ai_llm:             { kind: "text", label: "AI",     tone: sv.purple,  tooltip: "Identified by AI" },
  user:               { kind: "text", label: "MANUAL", tone: sv.green,   tooltip: "Assigned manually" },
};

function sourceDesc(source: string): SourceDesc {
  return (
    SOURCE_DESC[source] ?? {
      kind: "text",
      label: source.toUpperCase(),
      tone: sv.purple,
      tooltip: `Matched by ${source}`,
    }
  );
}

/** Provider chip — Engram mark (icon + tooltip) or a text source badge. */
function SourceChip({ source }: { source: string }) {
  const desc = sourceDesc(source);
  if (desc.kind === "text") {
    return (
      <SvBadge size="sm" tone={desc.tone} testid={`source-badge-${source}`}>
        {desc.label}
      </SvBadge>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid={`source-badge-${source}`}
          aria-label={desc.tooltip}
          title={desc.tooltip}
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "1px 5px",
            border: `1px solid ${desc.tone}55`,
            background: `${desc.tone}10`,
          }}
        >
          <MarkMono size={12} color={desc.tone} node={desc.node} />
        </span>
      </TooltipTrigger>
      <TooltipContent>{desc.tooltip}</TooltipContent>
    </Tooltip>
  );
}
```

- [ ] **Step 4: Compute the chip source and render the chip + method tag**

In `frontend/src/app/components/TrackGrid.tsx`, inside the `tracks.map((track, index) => { ... })` body, just after the `const ripPct = ...` declaration (currently ends line 73), add:

```tsx
          // A confidently-matched, non-extra track always carries a provider
          // chip; default to the Engram (ASR) mark when match_source is missing
          // (older/edge rows). Review/pending/ripping tracks get no chip.
          const isConfidentMatch =
            (track.state === "matched" || track.state === "completed") && !!track.finalMatch;
          const chipSource = track.isExtra
            ? undefined
            : track.matchSource ?? (isConfidentMatch ? "engram" : undefined);
```

Then replace the badges-row block (currently lines 134–163, the comment `{/* Quality / source / extra / deep-rematch badges */}` through its closing `)}`) with:

```tsx
                  {/* Quality / source / method / extra / deep-rematch badges */}
                  {(track.videoResolution || track.edition || track.isExtra || chipSource || (conflictStatus && track.state === "matching")) && (
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
                      {conflictStatus && track.state === "matching" && (
                        <SvBadge
                          size="sm"
                          tone={sv.magenta}
                          testid={`deep-rematch-chip-${track.id}`}
                        >
                          {passInfo ? `DEEP · ${passInfo}` : "DEEP RE-MATCH"}
                        </SvBadge>
                      )}
                      {chipSource && <SourceChip source={chipSource} />}
                      {track.matchMethod === "full_file" && (
                        <SvBadge size="sm" tone={sv.inkDim} testid="method-tag-full-file">
                          FULL-FILE
                        </SvBadge>
                      )}
                      {track.videoResolution && (
                        <SvBadge size="sm" tone={sv.cyan}>{track.videoResolution}</SvBadge>
                      )}
                      {track.edition && (
                        <SvBadge size="sm" tone={sv.magenta}>{track.edition}</SvBadge>
                      )}
                      {track.isExtra && <SvBadge size="sm" tone={sv.yellow}>EXTRA</SvBadge>}
                    </div>
                  )}
```

(The matched-body confidence/votes rendering at lines 346–386 already shows the confidence whenever `finalMatchConfidence !== undefined` and votes whenever `finalMatchVotes !== undefined` — no change needed there; Task 1 makes those values populate correctly.)

- [ ] **Step 5: Run the component test to verify it passes**

Run: `npx vitest run src/app/components/TrackGrid.test.tsx`
Expected: PASS (all four cases).

- [ ] **Step 6: Run the full unit suite, lint, and type-check**

Run: `npx vitest run`
Expected: PASS — no regressions (existing `DiscCard.test.tsx`, `adapters.test.ts`, `primitives.test.tsx` still green).

Run: `npm run lint`
Expected: PASS — no new ESLint warnings (max-warnings 0).

Run: `npm run build`
Expected: PASS — `tsc` clean (the new `matchMethod` field and `node` prop type-check).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/components/TrackGrid.tsx frontend/src/app/components/TrackGrid.test.tsx
git commit -m "fix(ui): consistent per-track provider chip, method tag, and confidence"
```

---

## Manual verification (optional, after Task 3)

With the dev stack running (`VITE_PORT`/`VITE_BACKEND_PORT` per CLAUDE.md), simulate a TV disc and watch a track grid in the `matching`/`organizing` state:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"GILMORE_GIRLS_S2D1","content_type":"tv","simulate_ripping":true}'
```

Confirm: every matched track shows a confidence %, an icon/text provider chip, votes only where present, and a `FULL-FILE` tag on voteless Engram matches. (Simulation may not exercise every provenance — the unit tests are the source of truth for the matrix.)

---

## Self-Review

**Spec coverage:**
- Confidence from column (matched) + match_details fallback (review) → Task 1 Steps 6, plus tests Steps 2.
- Votes only when present → unchanged `extractFinalMatchInfo` gate; asserted in Task 1 (full-file → undefined) and Task 3 (chunk-vote shows `3/10`).
- `matchMethod` derivation → Task 1 Step 5.
- Icon chip (cyan ASR / magenta fingerprint) + text chips → Task 3 Step 3 (`SOURCE_DESC`, `SourceChip`) + Task 2 (`node`).
- Always-on chip for matched, default ENGRAM for missing source → Task 3 Step 4 (`chipSource`); asserted Task 3 Step 1 (missing-source case).
- `FULL-FILE` method tag → Task 3 Step 4; asserted Step 1.
- Extras/Review unchanged → `chipSource` is undefined for extras (keeps `EXTRA`) and for review (no chip); matched-body gating unchanged.
- Tests for each provenance → Task 1 + Task 3 test blocks.

**Placeholder scan:** none — every step shows the exact code/command.

**Type consistency:** `matchMethod?: "chunk_vote" | "full_file"` defined on `Track` (Task 1 Step 1), produced by `deriveMatchMethod` (Task 1 Step 5), consumed in `TrackGrid` (Task 3 Step 4). `node?: boolean` defined on `MarkMono` Props (Task 2 Step 3), passed by `SourceChip` (Task 3 Step 3). `SOURCE_DESC`/`sourceDesc`/`SourceChip`/`chipSource` names are consistent across Task 3.

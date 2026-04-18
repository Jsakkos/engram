# Engram UI/UX Review — 2026-04-13

**Scope:** Dashboard, ReviewQueue (TV + Movie), ConfigWizard, HistoryPage, Contribute, error/offline states.
**Method:** Live Playwright session against `localhost:5173` (Vite) + `localhost:8000` (FastAPI, `DEBUG=true`), exercising simulation endpoints to drive jobs through all states. Screenshots in `assets/`.
**Lens:** `frontend-design` skill rubric — aesthetic commitment, typographic craft, cohesion, motion, spatial intention.

---

## TL;DR

Engram has a **confident, distinctive aesthetic** — cyan/magenta cyberpunk with terminal-prompt typography. It's the opposite of AI-slop: there's a clear point-of-view and it's executed well on the primary surfaces (Dashboard, ConfigWizard, HistoryPage). The app does what it says: discs flow through identify → rip → match → organize with live feedback, and Human-in-the-Loop intervenes when needed.

The weakest link — by a wide margin — is the **ReviewQueue**. It's the screen users are forced onto precisely because something's ambiguous, and it's currently the least polished, least guided, and least on-brand surface in the product. Fixing it is the single highest-leverage UI improvement available.

The **Contribute page** is the second cohesion gap: it feels bolted on, with a different visual language than the rest of the app.

Everything else is in solid shape with tightenings available around empty/error states and a few dense screens.

---

## 1. Does the UI do what it's supposed to do?

**Yes — the happy path is fully functional.**

| Flow | Observed behavior | Verdict |
|---|---|---|
| Disc inserted → job card appears | Card animates in; `LIVE` indicator present; state = `SCANNING` | ✅ |
| Ripping progress | TrackGrid expands, per-title state badges, `> RIPPING TITLE N...` terminal prompt | ✅ |
| Matching | `> MATCHING EPISODES...` banner, title states update in place | ✅ |
| Review needed | Amber `REVIEW NEEDED` button surfaces on card, clicks through to `/review/:id` | ✅ |
| Completed | Card moves to `DONE` tab with green `ARCHIVED IN LIBRARY` badge | ✅ |
| History deep-link | `/history/:jobId` opens the right-side detail panel directly | ✅ |
| Settings | Modal wizard with 4 steps (Paths / Tools / TMDB / Preferences) | ✅ |
| Offline | `LIVE` indicator flips off; dashboard still renders cached state | ✅ |

### Functional issues found

1. **Episode dropdowns show codes only, no titles.** In the TV review queue (`07-review-tv.png`), every title offers `S01E01` … `S01E24` as plain episode codes. Because the matcher just failed, the user is being asked to do the *hardest* mapping (Title 5 @ 27:00 → which episode?) with the *least* information. Episode titles and runtimes from TMDB must appear here. This is the largest functional gap in the app.
2. **Dropdown always exposes a fixed 24-episode range**, even for a disc that has 8 titles on a show with 13 episodes per season. The options should be scoped to the season's actual episode count from TMDB.
3. **`REVIEW NEEDED` button is exposed while the job state is still `scanning`** (see `06-dashboard-review-needed.png` — the card text reads `> SCANNING DISC STRUCTURE...` but the review button is already clickable). The button should only appear once identification has actually completed. (This may be a simulation artifact of `force_review_needed=true`, but worth confirming against real disc flows.)
4. **No episode-confidence signal in the review row.** Every title is labeled "Low confidence" with no gradation. When Curator returns *some* matches above threshold and *some* below, the review UI should surface the runner-up suggestions, not force the user to pick from a blind dropdown.
5. **`START RIP` and `RE-MATCH ALL` sit adjacent on the review page** (`07-review-tv.png`, top right). They perform very different actions and shouldn't be side-by-side with equal weight. `RE-MATCH ALL` is a correction loop, `START RIP` is a commitment — separate them and change the emphasis.
6. **Empty review queue — not tested**, but worth confirming: if a user clears every row, the page should have a meaningful terminal state rather than a bare "0 titles" heading.

---

## 2. Ease of use and design quality

### What's working

- **Dense-but-legible dashboard cards.** The DiscCard packs poster, content-type badge, title, volume label, elapsed time, state, and up to three action buttons into a single row without feeling cramped. The expanded **TrackGrid** (`04-dashboard-identifying.png`) is the cleverest move in the app: it turns a 30-title disc into a 2-column scannable table while keeping real-time per-track state visible. This is the pattern the ReviewQueue should learn from.
- **Color semantics are rigorously consistent.** Cyan = neutral/info, magenta = primary action, amber = needs attention, green = success. I didn't see a single place these drift. This is the backbone of the aesthetic.
- **Terminal-prompt status lines (`> MATCHING EPISODES...`)** are a beautiful thematic thread. They carry the cyberpunk voice into otherwise boring state text and cost nothing.
- **HistoryPage stats strip** (`12-history-page.png`) is a nice editorial moment — six stat tiles across the top give the page a proper identity rather than just being "the archive."
- **Compact view toggle** (`05-dashboard-compact.png`) is a real power-user affordance. It instantly turns the card list into a dense one-row-per-job table. Keep it.
- **Offline state is graceful** — the `LIVE` badge flips off and the UI keeps rendering cached job state rather than blanking. Good reactive design.

### Friction points

1. **ReviewQueue is the weakest screen in the app.** Compare `07-review-tv.png` to `04-dashboard-identifying.png`: the dashboard cards are framed, bracketed, layered. The review queue is a flat row list with small icon buttons and an unlabeled dropdown. The page lives at the exact moment the user most needs guidance ("something went wrong, help me fix it") and it's the screen with the *least* visual warmth and the *least* affordance. Specific problems:
   - Icon-only action buttons (`EXTRA`, discard, skip) with no hover tooltips visible in the snapshot.
   - No guidance copy. The heading is `NEEDS REVIEW [8]` and then immediately rows — no "Pick the episode that matches each title. Titles below 5 min are usually extras" type of coaching.
   - `Low confidence` is shown as a label but it's the *same* label on every row — it carries no information.
   - Season spinbox + episode select sit in a single cramped cluster; there's no visual hierarchy to show that episode is the primary decision.
   - The expanded detail panel (`08-review-tv-expanded.png`) opens a mostly-empty area that says "no details available" — expansion should reveal waveform/thumbnails/top-3 candidate suggestions, not nothing.

2. **Config wizard's `Save Changes` button is ambiguous.** The stepper shows four steps but `Save Changes` is always at the bottom of each step. Does it save this step only, or the whole wizard? Standard affordances would be `Next` / `Back` on intermediate steps and a final `Save & Finish`.

3. **History detail panel is dense and underlabeled.** `13-history-detail.png` shows many small paired values (classification, tmdb_id, etc.) without strong section headers or grouping. It works for a power user but could be broken into visual groups (*Classification* / *Processing* / *Files*) with real headings and more whitespace.

4. **No tooltips / help on icon buttons.** Every icon-only affordance (cancel, re-identify, review-needed, expand, skip, discard) should have a hover title. These exist on the dashboard card row per the accessible names in the snapshot (`aria-label` is populated), but they rely on hover-native behavior rather than a consistent tooltip component — worth a small audit.

5. **Track grid lacks state hierarchy.** In `04-dashboard-identifying.png` every track tile looks the same size/weight; a matched track, an unmatched track, and an extra are visually near-identical. Matched tracks could fade, unmatched could pulse, extras could go gray — same layout, more information.

6. **Empty/loading states are inconsistent.** The dashboard empty state (`01-dashboard-empty.png`) has a nice centered illustration + tagline. The history page with no results, the review queue on load, and the contribute page don't get the same treatment.

---

## 3. Cross-page cohesion

| Surface | Framing | Typography | Color system | Verdict |
|---|---|---|---|---|
| Dashboard | Corner-bracket cards, layered | ALL CAPS display + terminal `>` | Cyan/magenta/amber/green | **On-brand** |
| DiscCard / TrackGrid | Corner-bracket cards | Terminal prompts | Full palette | **On-brand** |
| ReviewQueue | Flat rows, no frame | Heading only, no prompts | Palette present but muted | **Drifting** |
| ConfigWizard | Dialog frame with stepper | ALL CAPS section heads | Full palette | **On-brand** |
| HistoryPage (list) | Stats strip + data table | ALL CAPS + terminal `>` in header | Full palette | **On-brand** |
| HistoryPage (detail) | Side panel | Labels small, value-heavy | Palette present | **Slightly drifting** (dense, flat) |
| Contribute | Plain table | No prompts, no brackets, smaller text | Muted | **Off-brand** |
| Offline state | Dashboard retained, `LIVE` → off | Same | Same | **On-brand** |

**Pattern:** the app has a clear visual system, and it's applied rigorously on the surfaces built with the most care. The outliers (ReviewQueue and Contribute) feel like they were built earlier or under more time pressure. Bringing them into the system is mostly additive — they already use the color palette, they just don't use the framing/typography.

### Cohesion recommendations

1. **Wrap every ReviewQueue row in the same corner-bracket card frame as DiscCard.** Keep the row-based layout, but give it the thematic wrapper. This alone will fix most of the drift.
2. **Use terminal prompts for ReviewQueue guidance.** `> 8 TITLES AWAITING ASSIGNMENT`, `> SELECT EPISODE OR MARK AS EXTRA` — this carries the voice into the screen for free.
3. **Contribute page needs a proper header** matching History (`CONTRIBUTE TO THEDISCDB` + stats strip already exists, but the table below it is unstyled). Style the table with the same density/accent rules as the history table.
4. **HistoryPage detail panel should group** fields under three headings (*Classification*, *Processing*, *Files*) with the same section-head treatment as ConfigWizard.
5. **Build a shared `<EmptyState>` component** that handles dashboard / review / history / contribute uniformly (centered illustration, tagline, terminal prompt, optional CTA).

---

## Priority recommendations

Ordered by impact ÷ effort:

1. **Episode dropdowns show real episode titles + runtimes from TMDB.** (High impact, low effort — data is already in TMDB client.) Without this, HITL is actually harder than doing nothing.
2. **Rebuild ReviewQueue rows inside the cyberpunk card frame + add guidance copy.** (High impact, medium effort.) Bring the weakest screen into the design system.
3. **Show top-3 candidate matches per title** on the expanded review row, not "no details available." (High impact, medium effort — Curator already returns ranked candidates.)
4. **Separate `START RIP` and `RE-MATCH ALL`** on the review page header; de-emphasize `RE-MATCH ALL` and make `START RIP` the obvious primary action once the user has assigned titles. (Low effort.)
5. **Bring Contribute page into the visual system.** (Medium impact, low effort.)
6. **Replace `Save Changes` in the wizard with `Next` / `Back` / `Save & Finish`.** (Low impact, low effort.) Removes ambiguity.
7. **Track grid: visually differentiate matched vs unmatched vs extra** via color/opacity. (Low effort.)
8. **Shared EmptyState component** for all four surfaces. (Medium effort.)
9. **Tooltip audit** for all icon-only buttons — standardize on a single tooltip primitive. (Low effort.)
10. **Confirm `REVIEW NEEDED` button only surfaces after identification completes** (not during scanning). (Bug-check.)

---

## Screenshots

All captured at 1440×900 against simulated jobs.

| # | File | State |
|---|---|---|
| 01 | `assets/01-dashboard-empty.png` | Dashboard, no jobs |
| 02 | `assets/02-dashboard-active.png` | Dashboard, TV job matching |
| 03 | `assets/03-dashboard-all.png` | Dashboard, ALL tab, two completed jobs |
| 04 | `assets/04-dashboard-identifying.png` | Dashboard, TV job ripping, expanded TrackGrid |
| 05 | `assets/05-dashboard-compact.png` | Dashboard, compact view toggle |
| 06 | `assets/06-dashboard-review-needed.png` | Dashboard, amber REVIEW NEEDED surfaced |
| 07 | `assets/07-review-tv.png` | ReviewQueue, TV disc, 8 unassigned titles |
| 08 | `assets/08-review-tv-expanded.png` | ReviewQueue, row expanded (empty detail) |
| 09 | `assets/09-review-movie.png` | ReviewQueue, movie edition selector |
| 10 | `assets/10-config-wizard.png` | ConfigWizard, Paths step |
| 11 | `assets/11-config-preferences.png` | ConfigWizard, Preferences step |
| 12 | `assets/12-history-page.png` | HistoryPage, stats + table |
| 13 | `assets/13-history-detail.png` | HistoryPage, detail panel |
| 14 | `assets/14-contribute.png` | Contribute page |
| 15 | `assets/15-disconnected.png` | Offline state, backend killed |

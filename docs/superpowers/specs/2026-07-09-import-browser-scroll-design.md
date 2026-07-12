# Import folder browser: scroll, path entry, and navigation affordances

**Date:** 2026-07-09
**Status:** Approved, pending implementation
**Component:** `frontend/src/components/ImportModal.tsx`

## Problem

A user cannot reach `/Volumes/TV Shows/Engram` through the Import modal's folder
browser. The parent directory `/Volumes/TV Shows` holds several hundred
subfolders (their Plex TV library). Reported symptoms:

1. The list does not scroll.
2. The browser "opens around the Ms", partway down the alphabet.
3. There is no visible way to go up a level.
4. There is no way to type or paste an exact path.

Symptoms 1 through 3 are a single CSS defect. Symptom 4 is a missing feature.

## Root cause

The file list at `ImportModal.tsx:193` is `{ flex: 1, overflow: "auto" }`.
`overflow: auto` only produces a scroll container when the box is smaller than
its content, so it depends on an ancestor capping the height. No ancestor does:

| Element | Line | Height constraint |
|---|---|---|
| List container | 193 | `flex: 1` (resolves against parent) |
| Navigator column | 171 | none (`width: 46%` only) |
| Two-pane row | 169 | `minHeight: 340` (a floor, not a ceiling) |
| `SvPanel` | `SvPanel.tsx:35` | none |
| Dialog wrapper | 121 | `maxWidth: 820` (width only) |

With ~400 subfolders the panel grows to roughly 15,000px tall. The list box is
never smaller than its content, so it never scrolls.

The outer container is `fixed inset-0 flex items-center justify-center`
(line 103). `items-center` vertically centers that oversized panel in the
viewport, clipping it symmetrically at top and bottom. The visible slice is
therefore the middle of an alphabetically sorted list, which is why the user
lands near "M". The `..` row (line 195), the header, the destination toggle, and
the START IMPORT button all render correctly but sit thousands of pixels outside
the viewport.

## Design

### 1. Bound the height (fixes symptoms 1 through 3)

Make the panel a bounded flex column so header and footer pin, and the two panes
scroll independently.

- Dialog wrapper (line 121): add `maxHeight: 82vh`.
- `SvPanel`: pass `display: flex, flexDirection: column, minHeight: 0` through
  its existing `style` prop.
- Two-pane row (line 169): replace `minHeight: 340` with `flex: 1, minHeight: 0`.
  The 340px floor moves to the dialog wrapper as `minHeight: 340`, where it does
  not fight the shrink chain. (`minHeight: 340` and `minHeight: 0` cannot coexist
  on one element, and the row is the element that must shrink.)
- Navigator column (line 171) and preview column (line 218): add `minHeight: 0`.
- Header (131), destination block (289), and footer (327): add `flexShrink: 0`.

**`minHeight: 0` is load-bearing.** Flex items default to `min-height: auto`,
which refuses to shrink a box below its content's intrinsic height. Without it
on every ancestor between the capped dialog and the scrolling list, the middle
row stays 15,000px tall and overflows the `maxHeight` cap.

This also repairs the preview pane (line 231), which carries the identical
unbounded `flex: 1, overflow: auto` and would overflow once a previewed folder
contains enough shows.

### 2. Path input (fixes symptom 4)

A new row between the header and the two panes:

- Monospace text field, seeded with `cwd` and re-synced to `cwd` on every
  successful navigation. It therefore doubles as the current-location display
  and replaces the truncated `cwd` label at line 191.
- A GO button. Enter in the field is equivalent.

**Submit semantics: navigate and preview.** This is exactly what clicking a
directory row does today (line 207: `navigate(e.path), choose(e.path)`), so a
typed path is a pure shortcut for "scroll to that folder and click it". No new
gesture for the user to learn, and one code path.

Sequencing differs from the click handler in one respect. The click handler
fires `navigate` and `choose` in parallel against a path known to exist. A typed
path may not exist, and firing both in parallel yields two errors for one typo.
So on submit: `await navigate(path)` first, and call `choose(path)` only if it
succeeded. This requires `navigate` to report success rather than swallowing the
failure into state; it should return `boolean`.

### 3. Scroll position on upward navigation

Navigating up via `..` currently lands at the top of the parent's list, losing
the user's place. After a successful `navigate`, if the previous `cwd` appears
among the new `entries`, scroll that row into view with `block: "center"` and
render it as active.

### Out of scope

- **Type-to-filter over the current directory.** Overlaps with the path input.
- **Backend `mkv_count` cost.** `routes.py:2583` runs an `os.scandir` on every
  direct child to count MKVs, so listing a 400-entry directory performs 400
  extra directory reads. On a network volume this likely makes the modal slow to
  open, independent of the layout bug. Worth its own issue; not required to
  resolve this report.

## Error handling

No new error plumbing. `GET /api/import/browse` already returns
`400 Not a directory: {path}` (`routes.py:2574`), `navigate` already catches and
writes to the `error` state (line 48), and `error` already renders through the
`Notice` component (line 285). A typo surfaces as a red notice in the preview
pane.

The existing `navSeq` / `chooseSeq` monotonic request tokens (lines 33 and 34)
must keep guarding the typed-path route, since a slow browse of a large
directory can still be superseded by a subsequent click.

## Testing

**Unit** (`ImportModal.test.tsx`, existing file, vitest + RTL):

- Typing a valid path and pressing Enter calls `browseDir` then `previewImport`
  with that path.
- Typing an invalid path renders the error notice and does **not** call
  `previewImport`.
- With a 400-entry `entries` fixture, the `..` row and the START IMPORT button
  are both present in the document.
- Navigating up scrolls the previously visited child into view.

jsdom has no layout engine and reports every height as zero, so it cannot verify
the layout fix. That needs a browser.

**E2E** (`import-flow.spec.ts`, existing file, Playwright):

- With a seeded large directory, assert the panel's `boundingBox().height` does
  not exceed the viewport height, and that the START IMPORT button is in the
  viewport.
- Assert the list pane scrolls: its `scrollHeight` exceeds its `clientHeight`.

## Files touched

| File | Change |
|---|---|
| `frontend/src/components/ImportModal.tsx` | Flex-column layout, `minHeight: 0` chain, path input row, `navigate` returns boolean, scroll-into-view |
| `frontend/src/components/ImportModal.test.tsx` | Path input and large-list tests |
| `frontend/e2e/import-flow.spec.ts` | Bounded-height and scrollability assertions |

No backend change. No API change.

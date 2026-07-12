# Import Folder Browser Scroll + Path Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Import modal's folder browser scroll inside a viewport-bounded panel, and let the user type an exact path instead of clicking through hundreds of folders.

**Architecture:** The folder list already has `overflow: auto`, but no ancestor caps a height, so the panel grows to its content and `items-center` centers the overflow off-screen. We cap the dialog at `82vh`, turn `SvPanel` into a flex column, and thread `minHeight: 0` down every flex ancestor so the list becomes the shrinking, scrolling box. Separately we add a path `<form>` under the header whose submit reuses the existing `navigate` + `choose` pair that a directory click already fires.

**Tech Stack:** React 18, TypeScript, inline styles + Synapse tokens (`sv`), Vitest + React Testing Library (`npm run test:unit`), Playwright (`npm run test:e2e`).

**Spec:** `docs/superpowers/specs/2026-07-09-import-browser-scroll-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `frontend/src/components/ImportModal.tsx` | The whole modal: browser pane, preview pane, destination, footer | Modify |
| `frontend/src/components/ImportModal.test.tsx` | Unit tests for path entry, error routing, large lists, scroll restore | Modify |
| `frontend/e2e/import-flow.spec.ts` | Browser-level proof the panel is bounded and the list scrolls | Modify |

`ImportModal.tsx` is ~450 lines with two small local components (`Row`, `Notice`). It stays one file: the additions are ~40 lines and splitting it would not serve this change.

No backend change. No API change.

---

## Task 1: Bound the panel height so the list scrolls

The layout defect. jsdom reports every height as zero, so this task is proven by Playwright, not Vitest.

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx:121-231`, `:289`, `:327`
- Test: `frontend/e2e/import-flow.spec.ts`

- [ ] **Step 1: Write the failing E2E test**

Add this helper below the existing `seedShowTree()` in `frontend/e2e/import-flow.spec.ts`:

```ts
/** A directory with enough children to overflow any viewport, plus one real show. */
function seedManyDirs(count = 400): string {
    const root = mkdtempSync(join(tmpdir(), 'engram-import-big-'));
    for (let i = 0; i < count; i++) {
        mkdirSync(join(root, `Show ${String(i).padStart(3, '0')}`), { recursive: true });
    }
    const season = join(root, 'Zulu Show', 'Season 1');
    mkdirSync(season, { recursive: true });
    writeFileSync(join(season, 't00.mkv'), Buffer.alloc(1024));
    return root;
}
```

Add this test inside the existing `test.describe('Manual media import', ...)` block:

```ts
test('folder browser stays within the viewport and scrolls', async ({ page, request }) => {
    const root = seedManyDirs();
    await request.put('/api/config', { data: { import_watch_path: root } });

    await page.goto('/');
    await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('sv-import-btn').click();
    await expect(page.getByText('IMPORT MEDIA')).toBeVisible();

    const list = page.getByTestId('import-nav-list');
    await expect(list.getByText('Show 000', { exact: true })).toBeVisible({ timeout: 15000 });

    // The panel must not exceed the viewport.
    const viewport = page.viewportSize()!;
    const panel = await page.getByTestId('import-panel').boundingBox();
    expect(panel!.height).toBeLessThanOrEqual(viewport.height);

    // Header, the up-a-level row, and the footer button all stay reachable.
    await expect(page.getByText('..', { exact: true })).toBeVisible();
    await expect(page.getByTestId('import-start-btn')).toBeInViewport();

    // The list itself is the scroll container.
    const scrolls = await list.evaluate((el) => el.scrollHeight > el.clientHeight + 1);
    expect(scrolls).toBe(true);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend
npx playwright test e2e/import-flow.spec.ts -g "stays within the viewport"
```

Expected: FAIL. Either `getByTestId('import-panel')` resolves 0 elements (the testid does not exist yet), or, once you add the testid, `expect(panel.height).toBeLessThanOrEqual(viewport.height)` fails with a height in the thousands.

- [ ] **Step 3: Cap the dialog wrapper and make the panel a flex column**

In `ImportModal.tsx`, replace the dialog wrapper and `SvPanel` opening tags (currently lines 121-129):

```tsx
      <motion.div
        className="relative w-full"
        style={{ maxWidth: 820, maxHeight: "82vh", minHeight: 340, display: "flex" }}
        initial={{ opacity: 0, scale: 0.96, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 16 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
      >
        <SvPanel
          glow
          pad={0}
          testid="import-panel"
          style={{
            background: sv.bg1,
            display: "flex",
            flexDirection: "column",
            flex: 1,
            minHeight: 0,
          }}
        >
```

The `minHeight: 340` floor moves here from the two-pane row, where it would otherwise fight the shrink chain.

- [ ] **Step 4: Thread `minHeight: 0` down the flex chain**

Four edits in the same file. `minHeight: 0` is load-bearing: flex items default to `min-height: auto` and refuse to shrink below their content, which would blow through the `82vh` cap.

Two-pane row (was `style={{ display: "flex", minHeight: 340 }}` at line 169):

```tsx
          <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
```

Navigator column (line 171), add `minHeight: 0` to the existing style object:

```tsx
            <div
              style={{
                width: "46%",
                borderRight: `1px solid ${sv.line}`,
                display: "flex",
                flexDirection: "column",
                minHeight: 0,
              }}
            >
```

Scrolling list container (line 193), add `minHeight: 0` and the testid:

```tsx
              <div data-testid="import-nav-list" style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
```

Preview column (line 218) and its scrolling body (line 231):

```tsx
            <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
```

```tsx
              <div style={{ flex: 1, overflow: "auto", padding: 14, minHeight: 0 }}>
```

- [ ] **Step 5: Pin the header, destination block, and footer**

Add `flexShrink: 0` to the existing style objects of the header (line 131), the destination block (line 289), and the footer (line 327). For example the destination block becomes:

```tsx
              <div style={{ padding: "10px 14px", borderTop: `1px solid ${sv.line}`, flexShrink: 0 }}>
```

- [ ] **Step 6: Run the E2E test to verify it passes**

```bash
cd frontend
npx playwright test e2e/import-flow.spec.ts -g "stays within the viewport"
```

Expected: PASS.

- [ ] **Step 7: Verify the existing import test still passes**

```bash
cd frontend
npx playwright test e2e/import-flow.spec.ts
```

Expected: PASS, both tests. If `browse, preview, and start an import` regressed, the shrink chain is over-constrained: check that the preview pane still renders `SEASON 1`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ImportModal.tsx frontend/e2e/import-flow.spec.ts
git commit -m "fix(import): bound folder browser height so the list scrolls"
```

---

## Task 2: `navigate` reports success

`navigate` currently swallows failures into `error` state and returns `void`. Task 3's path submit must not fire a preview against a path that failed to browse, so it needs a signal. Pure refactor, no behavior change; Task 3's tests cover it.

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx:36-51`

- [ ] **Step 1: Change the return type**

Replace `navigate` in full:

```tsx
  const navigate = useCallback(async (path: string): Promise<boolean> => {
    const seq = ++navSeq.current;
    setError(null);
    try {
      const res = await browseDir(path);
      if (seq !== navSeq.current) return false; // a newer navigation superseded this one
      setCwd(res.cwd);
      setParent(res.parent);
      setEntries(res.entries);
      setRoots(res.roots);
      return true;
    } catch (e) {
      if (seq === navSeq.current) {
        setError(e instanceof Error ? e.message : "Could not read directory");
      }
      return false;
    }
  }, []);
```

A superseded navigation returns `false`, so a stale submit never previews. That is correct: the newer navigation owns the pane.

- [ ] **Step 2: Verify nothing broke**

```bash
cd frontend
npm run test:unit -- ImportModal
npx tsc --noEmit -p tsconfig.json
```

Expected: existing 2 unit tests PASS, no type errors. Existing callers ignore the return value, which TypeScript permits.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ImportModal.tsx
git commit -m "refactor(import): navigate() returns whether the browse succeeded"
```

---

## Task 3: Typed path input

Submit runs `navigate` then, only on success, `choose`. Same pair a directory click fires (line 207), so typing is a shortcut for scrolling and clicking.

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx` (state, submit handler, JSX at `:179-192`)
- Test: `frontend/src/components/ImportModal.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to the `describe("ImportModal", ...)` block in `ImportModal.test.tsx`:

```tsx
  it("navigates to and previews a typed path", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));

    fireEvent.change(screen.getByTestId("import-path-input"), {
      target: { value: "/Volumes/TV Shows/Engram" },
    });
    fireEvent.submit(screen.getByTestId("import-path-form"));

    await waitFor(() =>
      expect(client.browseDir).toHaveBeenCalledWith("/Volumes/TV Shows/Engram"),
    );
    await waitFor(() =>
      expect(client.previewImport).toHaveBeenCalledWith("/Volumes/TV Shows/Engram"),
    );
  });

  it("shows an error and skips the preview when the typed path is bad", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));

    vi.mocked(client.browseDir).mockRejectedValueOnce(new Error("Not a directory: /nope"));
    fireEvent.change(screen.getByTestId("import-path-input"), { target: { value: "/nope" } });
    fireEvent.submit(screen.getByTestId("import-path-form"));

    await waitFor(() => expect(screen.getByText("Not a directory: /nope")).toBeInTheDocument());
    expect(client.previewImport).not.toHaveBeenCalled();
  });

  it("keeps the up-a-level row and start button present with a large directory", async () => {
    vi.mocked(client.browseDir).mockResolvedValue({
      cwd: "/media",
      parent: "/",
      roots: [],
      entries: Array.from({ length: 400 }, (_, i) => ({
        name: `Show ${i}`,
        path: `/media/Show ${i}`,
        type: "dir" as const,
        mkv_count: 0,
      })),
    });
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);

    await waitFor(() => expect(screen.getByText("Show 399")).toBeInTheDocument());
    expect(screen.getByText("..")).toBeInTheDocument();
    expect(screen.getByTestId("import-start-btn")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend
npm run test:unit -- ImportModal
```

Expected: the first two FAIL with `Unable to find an element by: [data-testid="import-path-input"]`. The third PASSES already (the rows render; they were merely off-screen in a real browser, which jsdom cannot model). Keep it as a regression guard.

- [ ] **Step 3: Add the input state and submit handler**

After the `chooseSeq` ref declaration (line 34), add:

```tsx
  const [pathInput, setPathInput] = useState(defaultPath || "");
```

After the `choose` callback (line 75), add:

```tsx
  const submitPath = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const target = pathInput.trim();
      if (!target) return;
      // Mirrors the directory-click gesture: browse into it, and preview it.
      // Only preview if the browse resolved, so one typo yields one error.
      if (await navigate(target)) await choose(target);
    },
    [pathInput, navigate, choose],
  );
```

Keep the input in sync with wherever navigation lands, so it doubles as the current-location display. Add below the existing focus effect (line 59):

```tsx
  useEffect(() => {
    if (cwd) setPathInput(cwd);
  }, [cwd]);
```

- [ ] **Step 4: Replace the `cwd` label with the path form**

Delete the `cwd` label div (lines 179-192) and put this form directly under the header, as a sibling of the two-pane row rather than inside the navigator column, so it spans the full modal width:

```tsx
          <form
            onSubmit={submitPath}
            data-testid="import-path-form"
            style={{
              display: "flex",
              gap: 6,
              padding: "8px 12px",
              borderBottom: `1px solid ${sv.line}`,
              flexShrink: 0,
            }}
          >
            <input
              data-testid="import-path-input"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              spellCheck={false}
              aria-label="Path"
              placeholder="Type or paste a folder path"
              style={{
                flex: 1,
                minWidth: 0,
                fontFamily: sv.mono,
                fontSize: 11,
                padding: "5px 8px",
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
                color: sv.ink,
                outline: "none",
              }}
            />
            <button
              type="submit"
              data-testid="import-path-go"
              style={{
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.1em",
                padding: "5px 12px",
                border: `1px solid ${sv.cyan}`,
                background: "transparent",
                color: sv.cyan,
                cursor: "pointer",
              }}
            >
              GO
            </button>
          </form>
```

Tokens used here are all exported from
`frontend/src/app/components/synapse/tokens.ts`: `sv.mono`, `sv.bg0`,
`sv.lineMid`, `sv.ink`, `sv.line`, `sv.cyan`. Note there is no `inkHi` token;
`sv.ink` (`#e6ecf5`) is the brightest ink.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd frontend
npm run test:unit -- ImportModal
npm run lint
```

Expected: all 5 unit tests PASS, lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ImportModal.tsx frontend/src/components/ImportModal.test.tsx
git commit -m "feat(import): type or paste an exact path in the folder browser"
```

---

## Task 4: Restore scroll position when navigating up

Clicking `..` out of `/Volumes/TV Shows/Engram` lands at the top of a 400-row list with no indication of where you were. Scroll the folder you came from into view and highlight it.

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx` (`navigate`, entries render, `Row`)
- Test: `frontend/src/components/ImportModal.test.tsx`

- [ ] **Step 1: Write the failing test**

jsdom does not implement `scrollIntoView`, so stub it. Add to the top of the existing `beforeEach` in `ImportModal.test.tsx`:

```tsx
  Element.prototype.scrollIntoView = vi.fn();
```

Then append this test to the `describe` block:

```tsx
  it("scrolls the folder you came from into view when navigating up", async () => {
    vi.mocked(client.browseDir)
      .mockResolvedValueOnce({
        cwd: "/media/King of Queens",
        parent: "/media",
        roots: [],
        entries: [],
      })
      .mockResolvedValueOnce({
        cwd: "/media",
        parent: "/",
        roots: [],
        entries: [
          { name: "King of Queens", path: "/media/King of Queens", type: "dir", mkv_count: 0 },
        ],
      });

    render(
      <ImportModal
        onClose={() => {}}
        defaultPath="/media/King of Queens"
        defaultDestinationMode="library"
      />,
    );
    await waitFor(() => expect(screen.getByText("..")).toBeInTheDocument());

    fireEvent.click(screen.getByText(".."));

    await waitFor(() => expect(screen.getByText("King of Queens")).toBeInTheDocument());
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend
npm run test:unit -- ImportModal
```

Expected: FAIL with `expected "spy" to be called with arguments: [ { block: 'center' } ]`, `Number of calls: 0`.

- [ ] **Step 3: Track the directory we left**

Add next to the other refs (after line 34):

```tsx
  const cwdRef = useRef<string | null>(null);
```

Add the landmark state next to the other state (after line 25):

```tsx
  const [landmark, setLandmark] = useState<string | null>(null);
```

In `navigate`'s success branch (from Task 2), record the previous cwd and set the
landmark only when it is a child of where we just landed:

```tsx
      const res = await browseDir(path);
      if (seq !== navSeq.current) return false; // a newer navigation superseded this one
      const prev = cwdRef.current;
      cwdRef.current = res.cwd;
      setLandmark(prev && res.entries.some((e) => e.path === prev) ? prev : null);
      setCwd(res.cwd);
      setParent(res.parent);
      setEntries(res.entries);
      setRoots(res.roots);
      return true;
```

- [ ] **Step 4: Scroll and highlight the landmark row**

Pass it down in the entries map (line 200), highlighting it the same way a selection is:

```tsx
                {entries.map((e) => (
                  <Row
                    key={e.path}
                    label={e.name}
                    count={e.type === "dir" ? e.mkv_count : undefined}
                    kind={e.type}
                    active={selected === e.path || landmark === e.path}
                    scrollTo={landmark === e.path}
                    onClick={() =>
                      e.type === "dir"
                        ? (navigate(e.path), choose(e.path))
                        : choose(e.path)
                    }
                  />
                ))}
```

Extend `Row` to take `scrollTo` and act on it. Its signature becomes:

```tsx
function Row({
  label,
  count,
  kind,
  active,
  scrollTo,
  onClick,
}: {
  label: string;
  count?: number;
  kind: "dir" | "mkv";
  active?: boolean;
  scrollTo?: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (scrollTo) ref.current?.scrollIntoView({ block: "center" });
  }, [scrollTo]);

  return (
    <button
      ref={ref}
      onClick={onClick}
```

The rest of `Row`'s body is unchanged. `Row` already lives in this file below the
default export, so `useRef` and `useEffect` are already imported at line 1.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd frontend
npm run test:unit -- ImportModal
npm run lint
npx tsc --noEmit -p tsconfig.json
```

Expected: all 6 unit tests PASS, lint clean, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ImportModal.tsx frontend/src/components/ImportModal.test.tsx
git commit -m "feat(import): restore scroll position when navigating up a level"
```

---

## Task 5: Full verification and changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the whole frontend suite**

```bash
cd frontend
npm run test:unit
npm run lint
npm run build
npx playwright test e2e/import-flow.spec.ts
```

Expected: all PASS. `npm run build` runs `tsc` then Vite, so it catches type errors the per-file check missed.

- [ ] **Step 2: Drive the real UI**

Start the backend and frontend (from `CLAUDE.md`, and note the parallel-session port rules if another stack is live), then point the import default at a directory with several hundred subfolders and confirm by eye:

- the modal fits the window, header and START IMPORT both visible
- the folder list scrolls with the wheel
- `..` sits at the top of the list and goes up a level
- pasting an absolute path into the field and pressing Enter jumps there and previews it
- pasting a nonexistent path shows a red notice and no preview

- [ ] **Step 3: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, in `### Fixed` (create the subsection if absent):

```markdown
- Import folder browser now scrolls instead of overflowing the window when a directory has many subfolders, keeping the up-a-level row and the start button reachable. You can also type or paste an exact folder path.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for import folder browser scroll fix"
```

- [ ] **Step 5: Stop this session's servers**

Per `CLAUDE.md`, kill the `uvicorn`/`python` and any `makemkvcon` processes this session started, scoped to the ports you launched on, before opening the PR.

---

## Notes for the implementer

- **Do not "fix" the scroll by putting `overflow-y: auto` on the backdrop.** That scrolls the header and the START IMPORT button off with the content, which is worse than the bug.
- **Do not drop `minHeight: 0`** from any ancestor in Task 1. Removing one silently restores the original bug, and no unit test will catch it because jsdom has no layout engine. Only the Playwright assertion in Task 1 will.
- **`navSeq`/`chooseSeq`** are monotonic request tokens guarding against a slow earlier browse overwriting a newer one. The typed-path route goes through the same `navigate`/`choose`, so it inherits the guard. Do not bypass them.
- The backend computes `mkv_count` by running `os.scandir` on **every** direct child (`backend/app/api/routes.py:2583`), so listing a 400-entry directory does 400 extra directory reads. On a network volume that likely makes the modal slow to open. Out of scope here; worth a separate issue.

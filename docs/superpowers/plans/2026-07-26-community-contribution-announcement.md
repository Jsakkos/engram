# Community Data Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Announce TheDiscDB contributions and the Engram acoustic fingerprint network through a new user-guide page, a reusable once-per-version "What's new" modal, and curated release notes.

**Architecture:** The in-app surface reuses the existing update pipeline rather than adding a parallel announcement system. `UpdateChecker` gains a second, separately-cached GitHub fetch for the *running* version's release notes (the existing `/releases/latest` call returns the wrong release for a user who is behind). Those notes flow through the same REST and WebSocket serialisers to a new `useWhatsNewModal` hook, which reuses `UpdateModal` in a read-only variant. Because the release body is generated from `CHANGELOG.md`, the 0.28.0 changelog section becomes the announcement copy, and every future release announces itself for free.

**Tech Stack:** Python 3.11+ / FastAPI / httpx / pytest (backend); React 18 / TypeScript / vitest / Testing Library (frontend); MkDocs Material (docs).

**Design spec:** `docs/superpowers/specs/2026-07-26-community-contribution-announcement-design.md`

---

## Before you start

**Worktree frontend setup.** `node_modules` is absent in a fresh worktree. Run `npm install` from `frontend/` once before any frontend task. That rewrites `package-lock.json` with a large diff; run `git checkout frontend/package-lock.json` before committing any frontend task.

**Do not** change any feature flag, config default, or anything in `backend/app/core/features.py`. This plan announces existing behaviour; it does not alter it.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/core/updater.py` | Add current-version notes fetch and two new state fields | 1, 2 |
| `backend/tests/unit/test_updater.py` | Cover the fetch, its failure modes, and the REST serialiser | 1, 2 |
| `backend/app/services/event_broadcaster.py` | Carry the two new fields on the WS push | 2 |
| `backend/tests/unit/test_event_broadcaster.py` | Cover the WS serialiser | 2 |
| `frontend/src/types/index.ts` | Add the fields to both update-status types | 3 |
| `frontend/src/app/hooks/useJobManagement.ts` | Normalise the new fields in `toUpdateStatus` | 3 |
| `frontend/src/components/UpdateModal.tsx` | Read-only `whatsNew` variant | 4 |
| `frontend/src/components/UpdateModal.test.tsx` | Cover the variant | 4 |
| `frontend/src/app/hooks/useWhatsNewModal.ts` | Once-per-version show/seed/dedupe decision | 5 |
| `frontend/src/app/hooks/__tests__/useWhatsNewModal.test.ts` | Cover all four decision paths | 5 |
| `frontend/src/app/App.tsx` | Wire the hook and the second modal instance | 6 |
| `frontend/src/app/components/synapse/SvTopBar.tsx` | Make the version string a manual re-entry point | 6 |
| `docs/guide/contributing-data.md` | The user-facing explanation of both networks | 7 |
| `mkdocs.yml` | Nav entry for the new page | 7 |
| `README.md`, `docs/index.md` | Terminology disambiguation and a community-data bullet | 8 |
| `frontend/src/components/FingerprintDisclosureModal.tsx` | Link to the privacy document it asks you to accept | 8 |
| `CHANGELOG.md` | The announcement copy itself | 9 |

Tasks 1 to 6 are sequential. Tasks 7 to 9 are independent of the code and of each other.

---

### Task 1: Backend fetches release notes for the running version

**Why:** `Updater._check()` returns at `if self._is_older_or_equal(tag, self._current_version)` before ever assigning `self.release_notes`, so a user who is up to date has no notes. And `GITHUB_API_URL` is `/releases/latest`, which is the wrong release for a user on 0.27.0 while 0.28.0 is out. The running version's notes need their own tag-specific call.

**Files:**
- Modify: `backend/app/core/updater.py`
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_updater.py`:

```python
class TestCurrentReleaseNotes:
    """Notes for the version already running, used by the what's-new modal."""

    def _mock_client(self, response):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=response)
        return mock_client

    async def test_fetches_notes_for_the_running_version(self):
        """The tag-specific endpoint is queried, and body/html_url are stored."""
        checker = UpdateChecker()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "tag_name": f"v{checker._current_version}",
            "html_url": "https://github.com/Jsakkos/engram/releases/tag/v1.2.3",
            "body": "## Highlights\n- Community data",
        }
        mock_client = self._mock_client(mock_response)

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            await checker._fetch_current_release_notes()

        requested_url = mock_client.get.call_args[0][0]
        assert f"/releases/tags/v{checker._current_version}" in requested_url
        assert checker.current_release_notes == "## Highlights\n- Community data"
        assert checker.current_release_url == (
            "https://github.com/Jsakkos/engram/releases/tag/v1.2.3"
        )

    async def test_missing_tag_leaves_fields_none(self):
        """A dev version with no GitHub release must not raise or set anything."""
        import httpx as _httpx

        checker = UpdateChecker()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=_httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
        )
        mock_client = self._mock_client(mock_response)

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            await checker._fetch_current_release_notes()

        assert checker.current_release_notes is None
        assert checker.current_release_url is None

    async def test_network_failure_leaves_fields_none(self):
        """Offline installs degrade silently."""
        import httpx as _httpx

        checker = UpdateChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_httpx.ConnectError("timeout"))

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            await checker._fetch_current_release_notes()

        assert checker.current_release_notes is None

    async def test_second_call_does_not_refetch(self):
        """Notes are immutable for a given build; one call per process is enough."""
        checker = UpdateChecker()
        checker.current_release_notes = "already here"

        mock_client = self._mock_client(MagicMock())

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            await checker._fetch_current_release_notes()

        mock_client.get.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_updater.py::TestCurrentReleaseNotes -v
```

Expected: FAIL with `AttributeError: 'UpdateChecker' object has no attribute '_fetch_current_release_notes'`.

- [ ] **Step 3: Add the URL template**

In `backend/app/core/updater.py`, directly below the existing `GITHUB_API_URL` (line 34):

```python
GITHUB_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/latest"
GITHUB_TAG_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/tags/v{version}"
STAGING_BASE = Path.home() / ".engram" / "update"
```

- [ ] **Step 4: Add the two state fields**

In `UpdateChecker.__init__`, immediately after `self.release_url: str | None = None` (line 68):

```python
        self.release_url: str | None = None
        # Notes for the version ALREADY RUNNING, for the what's-new modal. Kept
        # separate from latest_version/release_notes so nothing in the update flow
        # can mistake the running build for an available update.
        self.current_release_notes: str | None = None
        self.current_release_url: str | None = None
```

- [ ] **Step 5: Add the fetch method**

In `backend/app/core/updater.py`, immediately after `_check()` ends (before the `@staticmethod def _is_older_or_equal`):

```python
    async def _fetch_current_release_notes(self) -> None:
        """Populate release notes for the version already running.

        ``GITHUB_API_URL`` points at /releases/latest, which is the wrong release for
        a user sitting on 0.27.0 while 0.28.0 is out. The what's-new modal shows what
        changed in the build you are running, so it needs the tag-specific release.

        Failure is expected and silent: dev builds have no published release (404),
        and offline installs cannot reach GitHub at all. Both leave the fields None,
        which the frontend treats as "do not show the modal".
        """
        if self.current_release_notes is not None:
            return

        url = GITHUB_TAG_API_URL.format(version=self._current_version)
        try:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"engram/{self._current_version}",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug(f"Release notes for v{self._current_version} unavailable: {exc}")
            return

        self.current_release_notes = data.get("body")
        self.current_release_url = data.get("html_url")
```

- [ ] **Step 6: Call it from `start()`**

Replace the body of `start()` (lines 82 to 89):

```python
    async def start(self) -> None:
        """Entry point — call once from the FastAPI lifespan as asyncio.create_task()."""
        self._consume_update_result_marker()
        self._prune_staging()
        skipped_version = await self._load_skipped_version()
        await self._check(skipped_version)
        await self._fetch_current_release_notes()
        if (
            self.last_update_error
            or self.last_update_success_version
            or self.current_release_notes
        ):
            await self._broadcast()
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_updater.py -v
```

Expected: PASS, including the pre-existing tests (the `_check` path is untouched).

- [ ] **Step 8: Lint**

```bash
cd backend && uv run ruff check app/core/updater.py tests/unit/test_updater.py
```

Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py && git commit -m "feat(updater): fetch release notes for the running version"
```

---

### Task 2: Expose the new fields on both REST and WebSocket

**Why:** `get_status()` (REST seed) and `broadcast_update_status()` (WS push) are two independent serialisers that have drifted before: a dropped `is_frozen` on the WS side once hid the "Restart now" button on frozen builds. A field added to one and not the other produces a UI that works on reload and not on live update.

**Files:**
- Modify: `backend/app/core/updater.py` (`get_status`, `_broadcast`)
- Modify: `backend/app/services/event_broadcaster.py:229-267`
- Test: `backend/tests/unit/test_updater.py`, `backend/tests/unit/test_event_broadcaster.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_updater.py`:

```python
def test_get_status_carries_current_release_fields():
    """REST seed must expose the running version's notes."""
    checker = UpdateChecker()
    checker.current_release_notes = "## Highlights"
    checker.current_release_url = "https://example.com/tag/v1.2.3"

    status = checker.get_status()

    assert status["current_release_notes"] == "## Highlights"
    assert status["current_release_url"] == "https://example.com/tag/v1.2.3"


@pytest.mark.asyncio
async def test_broadcast_carries_current_release_fields():
    """WS push must expose the same fields as the REST seed (drift regression)."""
    ws = _FakeWS()
    eb = EventBroadcaster(ws)

    await eb.broadcast_update_status(
        state="up_to_date",
        current_release_notes="## Highlights",
        current_release_url="https://example.com/tag/v1.2.3",
    )

    payload = ws.sent[-1]
    assert payload["current_release_notes"] == "## Highlights"
    assert payload["current_release_url"] == "https://example.com/tag/v1.2.3"
```

Place both after the existing `test_broadcast_update_status_carries_marker_fields` (line 1109) so `_FakeWS` and the `EventBroadcaster` import are already in scope.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_updater.py -k current_release_fields -v
```

Expected: FAIL. The first with `KeyError: 'current_release_notes'`, the second with `TypeError: broadcast_update_status() got an unexpected keyword argument 'current_release_notes'`.

- [ ] **Step 3: Add the fields to `get_status()`**

In `backend/app/core/updater.py`, inside the dict returned by `get_status()`, after `"release_url": self.release_url,` (line 505):

```python
            "release_url": self.release_url,
            "current_release_notes": self.current_release_notes,
            "current_release_url": self.current_release_url,
```

- [ ] **Step 4: Add the parameters to the broadcaster**

In `backend/app/services/event_broadcaster.py`, extend the `broadcast_update_status` signature (after `release_url`, line 234):

```python
        release_url: str | None = None,
        current_release_notes: str | None = None,
        current_release_url: str | None = None,
        error: str | None = None,
```

and add the guards after the existing `release_url` guard (line 260):

```python
        if release_url is not None:
            data["release_url"] = release_url
        if current_release_notes is not None:
            data["current_release_notes"] = current_release_notes
        if current_release_url is not None:
            data["current_release_url"] = current_release_url
```

- [ ] **Step 5: Pass them from `_broadcast()`**

In `backend/app/core/updater.py`, inside `_broadcast()` after `release_url=self.release_url,` (line 854):

```python
                release_url=self.release_url,
                current_release_notes=self.current_release_notes,
                current_release_url=self.current_release_url,
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_updater.py tests/unit/test_event_broadcaster.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/updater.py backend/app/services/event_broadcaster.py backend/tests/unit/test_updater.py && git commit -m "feat(updater): expose current-version notes on REST and WS"
```

---

### Task 3: Frontend types and normaliser

**Why:** `toUpdateStatus` in `useJobManagement.ts` is the single normaliser shared by the WS handler and the REST seed, added specifically so the two channels cannot disagree. New fields must go through it or the REST seed will carry them and the WS push will silently drop them to `undefined`.

**Files:**
- Modify: `frontend/src/types/index.ts:193-219`
- Modify: `frontend/src/app/hooks/useJobManagement.ts:39-52`

- [ ] **Step 1: Add the fields to both types**

In `frontend/src/types/index.ts`, in `UpdateStatusMessage` after `release_url?: string | null;` (line 199):

```ts
    release_url?: string | null;
    current_release_notes?: string | null;
    current_release_url?: string | null;
```

and in `UpdateStatus` after `release_url: string | null;` (line 213):

```ts
    release_url: string | null;
    /** Notes for the version currently running (what's-new modal). */
    current_release_notes: string | null;
    current_release_url: string | null;
```

- [ ] **Step 2: Normalise them**

In `frontend/src/app/hooks/useJobManagement.ts`, inside `toUpdateStatus` after `release_url: raw.release_url ?? null,` (line 45):

```ts
        release_url: raw.release_url ?? null,
        current_release_notes: raw.current_release_notes ?? null,
        current_release_url: raw.current_release_url ?? null,
```

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx tsc --noEmit
```

Expected: errors in `useUpdateSuccessToast.test.ts` reporting the two new required properties are missing from its `status()` helper. That is the type system catching every construction site, which is the point.

- [ ] **Step 4: Fix the existing test fixture**

In `frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts`, extend the `status` helper (lines 9 to 13):

```ts
const status = (v: string | null): UpdateStatus => ({
    state: "up_to_date", current_version: "9.9.9", latest_version: null, release_notes: null,
    release_url: null, current_release_notes: null, current_release_url: null,
    download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: v,
});
```

- [ ] **Step 5: Typecheck again, then run the unit tests**

```bash
cd frontend && npx tsc --noEmit && npm run test:unit
```

Expected: no type errors, all tests pass.

- [ ] **Step 6: Commit**

```bash
git checkout frontend/package-lock.json 2>/dev/null; git add frontend/src/types/index.ts frontend/src/app/hooks/useJobManagement.ts frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts && git commit -m "feat(update): carry current-version release notes through the update status type"
```

---

### Task 4: Read-only `whatsNew` variant of `UpdateModal`

**Why:** The modal already renders release-notes markdown in the right visual language. What it must not do in this context is offer "Skip this version" or "Restart to update", which are meaningless for the version you are already running.

**Files:**
- Modify: `frontend/src/components/UpdateModal.tsx`
- Create: `frontend/src/components/UpdateModal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/UpdateModal.test.tsx`:

`vitest.config.ts` has `setupFiles: []`, so jest-dom matchers are **not** global. Every component test in this repo imports them itself; this one must too.

```tsx
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import UpdateModal from "./UpdateModal";
import type { UpdateStatus } from "../types";

vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn() } }));

const base: UpdateStatus = {
    state: "up_to_date",
    current_version: "0.28.0",
    latest_version: null,
    release_notes: null,
    release_url: null,
    current_release_notes: "## Highlights\n\nCommunity data features.",
    current_release_url: "https://example.com/tag/v0.28.0",
    download_progress: null,
    error: null,
    last_update_error: null,
    last_update_success_version: null,
    is_frozen: true,
};

describe("UpdateModal whatsNew variant", () => {
    it("shows the running version's notes and no update actions", () => {
        render(
            <UpdateModal
                open
                variant="whatsNew"
                updateStatus={base}
                onClose={() => {}}
            />,
        );

        expect(screen.getByText(/What's new in 0\.28\.0/)).toBeInTheDocument();
        expect(screen.getByText(/Community data features/)).toBeInTheDocument();
        // Queried by test id, not by role+name: the header's X button already has
        // aria-label="Close", so getByRole("button", { name: "Close" }) matches two
        // elements and throws.
        expect(screen.getByTestId("whats-new-close")).toBeInTheDocument();
        expect(screen.queryByText("Skip this version")).not.toBeInTheDocument();
        expect(screen.queryByText(/Restart to update/)).not.toBeInTheDocument();
    });

    it("defaults to the update variant with its actions intact", () => {
        render(
            <UpdateModal
                open
                updateStatus={{ ...base, latest_version: "0.29.0", release_notes: "Newer" }}
                onClose={() => {}}
                onDismiss={() => {}}
            />,
        );

        expect(screen.getByText(/What's new in 0\.29\.0/)).toBeInTheDocument();
        expect(screen.getByText("Skip this version")).toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm run test:unit -- UpdateModal
```

Expected: FAIL. TypeScript rejects the unknown `variant` prop, and the assertions on `Close` do not match.

- [ ] **Step 3: Add the variant prop and derived values**

In `frontend/src/components/UpdateModal.tsx`, replace the props interface and destructuring (lines 17 to 30):

```tsx
interface UpdateModalProps {
    open: boolean;
    updateStatus: UpdateStatus | null;
    onClose: () => void;
    onDismiss?: () => void;
    /**
     * "update" (default): notes for an available update, with skip and restart actions.
     * "whatsNew": notes for the version already running, read-only.
     */
    variant?: "update" | "whatsNew";
}

export default function UpdateModal({
    open,
    updateStatus,
    onClose,
    onDismiss,
    variant = "update",
}: UpdateModalProps) {
    const [restarting, setRestarting] = useState(false);

    const isWhatsNew = variant === "whatsNew";
    const version = isWhatsNew ? updateStatus?.current_version : updateStatus?.latest_version;
    const notes = isWhatsNew ? updateStatus?.current_release_notes : updateStatus?.release_notes;
    const releaseUrl = isWhatsNew ? updateStatus?.current_release_url : updateStatus?.release_url;
```

- [ ] **Step 4: Make `onDismiss` optional at its call site**

In `handleSkip` (line 70), replace `onDismiss();` with:

```tsx
            onDismiss?.();
```

- [ ] **Step 5: Use the derived values in the header, body, and link**

Replace line 161:

```tsx
                                        What's new in {version ?? "…"}
```

Replace the body conditional (line 189) and its markdown child (line 198):

```tsx
                                {notes ? (
```

```tsx
                                        <ReactMarkdown>{notes}</ReactMarkdown>
```

Replace the GitHub link guard and href (lines 211 and 213):

```tsx
                                {releaseUrl && (
                                    <a
                                        href={releaseUrl}
```

- [ ] **Step 6: Branch the footer**

Replace the entire contents of the footer `div` (lines 246 to 287, from `<button type="button" onClick={handleSkip}` through the closing `)}` of the `isFrozen` ternary):

```tsx
                                {isWhatsNew ? (
                                    <button
                                        type="button"
                                        onClick={onClose}
                                        data-testid="whats-new-close"
                                        style={{
                                            ...buttonBase,
                                            marginLeft: "auto",
                                            color: sv.bg0,
                                            background: sv.cyan,
                                        }}
                                    >
                                        Close
                                    </button>
                                ) : (
                                    <>
                                        <button
                                            type="button"
                                            onClick={handleSkip}
                                            style={{
                                                ...buttonBase,
                                                color: sv.inkDim,
                                                background: "transparent",
                                                border: `1px solid ${sv.line}`,
                                            }}
                                        >
                                            Skip this version
                                        </button>

                                        {isFrozen ? (
                                            <button
                                                type="button"
                                                onClick={handleRestart}
                                                disabled={restarting}
                                                style={{
                                                    ...buttonBase,
                                                    color: sv.bg0,
                                                    background: restarting ? `${sv.cyan}99` : sv.cyan,
                                                    opacity: restarting ? 0.8 : 1,
                                                }}
                                            >
                                                {restarting ? "Restarting…" : "Restart to update →"}
                                            </button>
                                        ) : (
                                            <a
                                                href={releaseUrl ?? "#"}
                                                target="_blank"
                                                rel="noreferrer"
                                                style={{
                                                    ...buttonBase,
                                                    color: sv.bg0,
                                                    background: sv.cyan,
                                                    textDecoration: "none",
                                                }}
                                            >
                                                Download from GitHub →
                                            </a>
                                        )}
                                    </>
                                )}
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
cd frontend && npm run test:unit -- UpdateModal
```

Expected: both tests PASS.

- [ ] **Step 8: Commit**

```bash
git checkout frontend/package-lock.json 2>/dev/null; git add frontend/src/components/UpdateModal.tsx frontend/src/components/UpdateModal.test.tsx && git commit -m "feat(update): add read-only whatsNew variant to UpdateModal"
```

---

### Task 5: `useWhatsNewModal` hook

**Why:** The trigger must not be `last_update_success_version`. That field is only set by a successful self-update, so Docker users and anyone who downloads a release manually would never see the modal. Comparing a persisted "last seen version" against `current_version` covers every install path.

Decision table the hook implements:

| `engram:lastSeenVersion` | Notes available | Behaviour |
|---|---|---|
| absent | either | Seed the key with the current version, show nothing. A fresh install must not get a what's-new modal during the setup wizard. |
| equals current version | either | Nothing. |
| differs from current version | no | Nothing, and **do not** advance the key. Notes may arrive later in this session via the WS push, or on a later launch when the machine is online. |
| differs from current version | yes | Show once, then advance the key. |

**Files:**
- Create: `frontend/src/app/hooks/useWhatsNewModal.ts`
- Create: `frontend/src/app/hooks/__tests__/useWhatsNewModal.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/app/hooks/__tests__/useWhatsNewModal.test.ts`:

```ts
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import { useWhatsNewModal } from "../useWhatsNewModal";
import type { UpdateStatus } from "../../../types";

const KEY = "engram:lastSeenVersion";

const status = (version: string, notes: string | null): UpdateStatus => ({
    state: "up_to_date", current_version: version, latest_version: null, release_notes: null,
    release_url: null, current_release_notes: notes, current_release_url: null,
    download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: null,
});

describe("useWhatsNewModal", () => {
    beforeEach(() => localStorage.clear());

    it("seeds silently on a fresh install", () => {
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBe("0.28.0");
    });

    it("opens once when the version changed and notes are available", () => {
        localStorage.setItem(KEY, "0.27.0");
        const { result, rerender } = renderHook(() =>
            useWhatsNewModal(status("0.28.0", "notes")),
        );
        expect(result.current.open).toBe(true);
        expect(localStorage.getItem(KEY)).toBe("0.28.0");

        act(() => result.current.close());
        rerender();
        expect(result.current.open).toBe(false);
    });

    it("does not open, and does not advance the key, when notes are missing", () => {
        localStorage.setItem(KEY, "0.27.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", null)));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBe("0.27.0");
    });

    it("does nothing when already on the last seen version", () => {
        localStorage.setItem(KEY, "0.28.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        expect(result.current.open).toBe(false);
    });

    it("does nothing when there is no status yet", () => {
        const { result } = renderHook(() => useWhatsNewModal(null));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBeNull();
    });

    it("can be opened manually", () => {
        localStorage.setItem(KEY, "0.28.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        act(() => result.current.show());
        expect(result.current.open).toBe(true);
    });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend && npm run test:unit -- useWhatsNewModal
```

Expected: FAIL with `Failed to resolve import "../useWhatsNewModal"`.

- [ ] **Step 3: Write the hook**

Create `frontend/src/app/hooks/useWhatsNewModal.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import type { UpdateStatus } from "../../types";

const KEY = "engram:lastSeenVersion";

/**
 * Show the "What's new in vX" modal once per version.
 *
 * Keyed on current_version rather than last_update_success_version: the latter is
 * only set by a successful self-update, so Docker users and anyone who downloads a
 * release manually would never see it. Deduped via localStorage because an
 * in-memory ref would be wiped by the post-update page reload.
 *
 * A fresh install seeds the key and shows nothing: nobody wants a changelog during
 * the setup wizard. When the version changed but notes have not arrived yet (slow or
 * rate-limited GitHub, offline install) the key is deliberately NOT advanced, so the
 * modal still appears once the notes land, either later this session via the
 * WebSocket push or on a later launch.
 */
export function useWhatsNewModal(updateStatus: UpdateStatus | null): {
    open: boolean;
    show: () => void;
    close: () => void;
} {
    const [open, setOpen] = useState(false);
    const version = updateStatus?.current_version ?? null;
    const notes = updateStatus?.current_release_notes ?? null;

    useEffect(() => {
        if (!version) return;

        let seen: string | null;
        try {
            seen = localStorage.getItem(KEY);
        } catch {
            return; // localStorage unavailable, never nag
        }

        const remember = () => {
            try {
                localStorage.setItem(KEY, version);
            } catch {
                // best effort only
            }
        };

        if (seen === null) {
            remember(); // fresh install
            return;
        }
        if (seen === version) return;
        if (!notes) return; // notes not fetched yet, try again when they arrive

        remember();
        setOpen(true);
    }, [version, notes]);

    const show = useCallback(() => setOpen(true), []);
    const close = useCallback(() => setOpen(false), []);

    return { open, show, close };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npm run test:unit -- useWhatsNewModal
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
git checkout frontend/package-lock.json 2>/dev/null; git add frontend/src/app/hooks/useWhatsNewModal.ts frontend/src/app/hooks/__tests__/useWhatsNewModal.test.ts && git commit -m "feat(update): add once-per-version whats-new hook"
```

---

### Task 6: Wire the modal into the app and add a manual entry point

**Why:** Without a manual re-entry point, a user who dismisses the modal can never read the notes again: `UpdateBanner` returns `null` unless `state === 'ready'`, so its "What's new" button disappears the moment you are up to date. The version string in the top bar is already on every screen.

**Files:**
- Modify: `frontend/src/app/App.tsx:11, 165-169, 322-329, 969-977`
- Modify: `frontend/src/app/components/synapse/SvTopBar.tsx:10-20, 67-78`

- [ ] **Step 1: Add the optional click handler to `SvTopBar`**

In `frontend/src/app/components/synapse/SvTopBar.tsx`, extend `Props` after `version: string;` (line 13):

```tsx
  /** App version, shown beneath the wordmark. */
  version: string;
  /** When provided, the version string becomes a button that opens the what's-new modal. */
  onVersionClick?: () => void;
```

and the destructuring after `version,` (line 31):

```tsx
  version,
  onVersionClick,
```

- [ ] **Step 2: Render the version as a button when the handler is present**

Replace line 77:

```tsx
            <span style={{ color: sv.cyan }}>›</span> MEMORY · ARCHIVAL ·{" "}
            {onVersionClick ? (
              <button
                type="button"
                onClick={onVersionClick}
                title="What's new in this version"
                data-testid="sv-topbar-version"
                style={{
                  font: "inherit",
                  letterSpacing: "inherit",
                  textTransform: "uppercase",
                  color: "inherit",
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                }}
              >
                v{version}
              </button>
            ) : (
              <>v{version}</>
            )}
```

- [ ] **Step 3: Wire the hook in `App.tsx`**

Add the import after line 11 (`import { useUpdateSuccessToast } ...`):

```tsx
import { useWhatsNewModal } from "./hooks/useWhatsNewModal";
```

and call it directly after `useUpdateSuccessToast(updateStatus);` (line 166):

```tsx
  useUpdateSuccessToast(updateStatus);
  const whatsNew = useWhatsNewModal(updateStatus);
```

- [ ] **Step 4: Pass the handler to the top bar**

In `App.tsx`, in the `SvTopBar` element, after `version={__APP_VERSION__}` (line 324):

```tsx
        version={__APP_VERSION__}
        onVersionClick={whatsNew.show}
```

- [ ] **Step 5: Render the second modal instance**

In `App.tsx`, directly after the closing `/>` of the existing `UpdateModal` (line 977 area, following the `{/* Update Modal — release notes opened from UpdateBanner */}` block):

```tsx
      {/* What's new: release notes for the version currently running, shown once
          per version and re-openable from the top-bar version string. */}
      <UpdateModal
        open={whatsNew.open}
        variant="whatsNew"
        updateStatus={updateStatus}
        onClose={whatsNew.close}
      />
```

- [ ] **Step 6: Typecheck and run the full unit suite**

```bash
cd frontend && npx tsc --noEmit && npm run test:unit
```

Expected: no type errors; all tests pass, including the existing `SvTopBar.test.tsx` (the new prop is optional, so its current render is unaffected).

- [ ] **Step 7: Build**

```bash
cd frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 8: Commit**

```bash
git checkout frontend/package-lock.json 2>/dev/null; git add frontend/src/app/App.tsx frontend/src/app/components/synapse/SvTopBar.tsx && git commit -m "feat(update): show whats-new once per version, reopenable from the top bar"
```

---

### Task 7: The `Contributing Data` documentation page

**Why:** Neither feature has any user-facing documentation. The only fingerprint document is `docs/development/fingerprint-privacy.md`, an engineering-grade wire-format reference filed under Development, and nothing in the app links to it.

**Files:**
- Create: `docs/guide/contributing-data.md`
- Modify: `mkdocs.yml` (nav, after `Job History`)

**Verified facts to write from.** Do not re-derive these; they were checked against the code while writing the spec.

*TheDiscDB half:*
- Per-user setting `discdb_contributions_enabled`, default `False`. Path: Settings → TheDiscDB Contributions.
- `discdb_contribution_tier`, default `2`. Tier 1 = do not share, 2 = automatic, 3 = full, including UPC/ASIN and cover images.
- A contribution carries: disc content hash, title to episode mapping, MakeMKV scan log, and at tier 3 the UPC/ASIN and cover art.
- No account and no API key required. The `discdb_api_key` config field exists but is optional.
- Content hash = MD5 of the Int64 file sizes of `BDMV/STREAM/*.m2ts`, sorted by filename. It identifies a factory pressing, not a person.
- Lookup has been on for everyone since PR #430 and is independent of contributing.
- The Contribute page workflow: export, group multi-disc sets, enhance, submit.

*Fingerprint half:*
- `enable_fingerprint_contributions` defaults `True`, but nothing uploads until `fingerprint_disclosure_accepted` is set by accepting the disclosure dialog, which appears when contributions are queued.
- `enable_fingerprint_identification` now defaults `True`. On a miss, identification falls back to the existing TMDB, AI, and heuristic paths.
- What is sent per fingerprint: `wire_format_version`, `pseudonym` (random per-install UUIDv4), `tmdb_id`, `season`, `episode`, `fingerprint_b64`, `fingerprint_sha256_b64`, `disc_content_hash_b64`, `match_confidence`, `match_source`, `client_version`.
- The fingerprint itself: 30-second audio windows at 16 kHz mono, reduced by chromaprint to a stream of 32-bit integers, zstd-compressed and base64-encoded. No audio or video is transmitted and none can be reconstructed.
- Canonical tier requires contributions from at least three distinct disc releases.
- Deletion: `/v1/forget` removes everything sent under a pseudonym. The pseudonym can be rotated.

- [ ] **Step 1: Write the page**

Create `docs/guide/contributing-data.md` with this exact heading structure. Write the prose from the verified facts above. **Do not use em dashes or en dashes anywhere in this file**; use colons, commas, semicolons, or parentheses.

```markdown
# Contributing Data

## Two networks, two questions
[One paragraph plus a table contrasting the two: TheDiscDB answers "which disc is
this?", the fingerprint network answers "which episode is this track?". Columns:
network, question answered, default, what it identifies.]

## TheDiscDB: sharing disc layouts
### What a contribution contains
### Turning it on
[Settings → TheDiscDB Contributions. State that it is off by default and that no
account or API key is needed.]
### Sharing tiers
[Table of tiers 1, 2, 3.]
### The Contribute page
[Export, group multi-disc sets, enhance, submit.]

## The Engram fingerprint network: sharing episode fingerprints
### This is not the same as episode matching
[Disambiguate chromaprint from the ASR-versus-subtitles matcher. This section exists
because the README used to call the ASR matcher "audio fingerprinting".]
### What a contribution contains
[Summary table of the eleven fields. Link to the full wire format.]
### What is never sent
[No audio, no video, no filenames, no paths.]
### How a fingerprint becomes canonical
[Three distinct disc releases.]
### Turning it off, and deleting what you sent
[Settings toggle, pseudonym rotation, /v1/forget.]

## Frequently asked questions
### Does Engram upload my media?
### Do I need an account for either of these?
### Can I turn contributions off?
### Can I delete data I have already contributed?
### Does contributing slow down ripping?

## Further reading
- [Fingerprint Network Privacy](../development/fingerprint-privacy.md) for the
  field-by-field wire format.
```

- [ ] **Step 2: Add the nav entry**

In `mkdocs.yml`, under `User Guide`, immediately after the `Job History` line:

```yaml
    - Job History: guide/history.md
    - Contributing Data: guide/contributing-data.md
    - LLM Episode Matcher: guide/llm-episode-matcher.md
```

- [ ] **Step 3: Build the docs**

From the repository root, not from `docs/`:

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Expected: build succeeds. Roughly 18 warnings are pre-existing and expected. Do not add `--strict`. Confirm no warning names `contributing-data.md`.

- [ ] **Step 4: Verify no em or en dashes slipped in**

Use this exact command. The obvious `grep -n "[—–]"` is **wrong** in Git Bash: the bracket expression matches individual *bytes*, and every one of `—`, `–`, `→`, `…`, `·` begins with the byte `0xE2`, so it reports arrows and ellipses as dashes. Alternating the full three-byte sequences avoids that.

```bash
grep -nE "$(printf '\xe2\x80\x94|\xe2\x80\x93')" docs/guide/contributing-data.md
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add docs/guide/contributing-data.md mkdocs.yml && git commit -m "docs: add Contributing Data guide for TheDiscDB and the fingerprint network"
```

---

### Task 8: Terminology fix and the disclosure link

**Why:** `README.md` and `docs/index.md` both describe the ASR-versus-subtitles matcher as "Audio fingerprint matching". Chromaprint is the thing that is actually a fingerprint, so a reader of the new page will conflate two different systems. Separately, `FingerprintDisclosureModal` asks users to accept terms it never links to.

**Files:**
- Modify: `README.md` (Features list)
- Modify: `docs/index.md` (Features list)
- Modify: `frontend/src/components/FingerprintDisclosureModal.tsx`

**Punctuation note.** Every sibling bullet in the README feature list already uses an em dash as its separator, and `docs/index.md` uses `--` in the same position. The replacements below match each file's existing house style deliberately, so the new bullets do not stand out from the ones around them. This is the one place in this plan where em dashes are intended.

- [ ] **Step 1: Fix the README feature bullets**

In `README.md`, replace the bullet reading `- **Audio fingerprint matching** — identifies TV episodes via ASR transcription matched against subtitles` with these two:

```markdown
- **Episode matching** — identifies TV episodes by transcribing the audio and matching it against reference subtitles
- **Acoustic fingerprint network** — a shared catalog of chromaprint audio fingerprints that identifies episodes without needing subtitles for the show. No audio or video leaves your machine; see [Contributing Data](docs/guide/contributing-data.md)
```

Then add this bullet directly after the existing `- **TheDiscDB integration** …` bullet:

```markdown
- **Contribute back** — share disc layouts with [TheDiscDB](https://thediscdb.com) so others can identify the same pressings automatically. Opt in under Settings → TheDiscDB Contributions
```

- [ ] **Step 2: Apply the same three edits to `docs/index.md`**

The Features list in `docs/index.md` mirrors the README but uses `--` rather than an em dash, and its links are site-relative. Use:

```markdown
- **Episode matching** -- identifies TV episodes by transcribing the audio and matching it against reference subtitles
- **Acoustic fingerprint network** -- a shared catalog of chromaprint audio fingerprints that identifies episodes without needing subtitles for the show. No audio or video leaves your machine; see [Contributing Data](guide/contributing-data.md)
```

and after the TheDiscDB bullet:

```markdown
- **Contribute back** -- share disc layouts with [TheDiscDB](https://thediscdb.com) so others can identify the same pressings automatically. Opt in under Settings, TheDiscDB Contributions
```

- [ ] **Step 3: Link the privacy document from the disclosure modal**

In `frontend/src/components/FingerprintDisclosureModal.tsx`, add a link in the body, above the accept and decline buttons. Match the surrounding `sv` token styling:

```tsx
                        <a
                            href="https://jsakkos.github.io/engram/development/fingerprint-privacy/"
                            target="_blank"
                            rel="noreferrer"
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 11,
                                letterSpacing: "0.1em",
                                color: sv.cyanHi,
                                textDecoration: "none",
                                textTransform: "uppercase",
                            }}
                        >
                            Read the full privacy disclosure
                        </a>
```

- [ ] **Step 4: Verify the frontend still builds and its tests pass**

```bash
cd frontend && npx tsc --noEmit && npm run test:unit -- FingerprintDisclosureModal
```

Expected: no type errors, existing tests pass.

- [ ] **Step 5: Commit**

```bash
git checkout frontend/package-lock.json 2>/dev/null; git add README.md docs/index.md frontend/src/components/FingerprintDisclosureModal.tsx && git commit -m "docs: separate episode matching from the acoustic fingerprint network"
```

---

### Task 9: The CHANGELOG entry

**Why:** `extract_changelog.py` feeds the GitHub release body, which `updater.py` now feeds to the what's-new modal. This section is the announcement copy every user reads on first launch after updating, not a routine changelog chore.

**Files:**
- Modify: `CHANGELOG.md` (the `## [Unreleased]` section)

- [ ] **Step 1: Add the entries**

In `CHANGELOG.md`, replace the bare `## [Unreleased]` line with:

```markdown
## [Unreleased]

### Added

- **Engram is part of two shared identification networks, and you can now see exactly what that means.** A new [Contributing Data](https://jsakkos.github.io/engram/guide/contributing-data/) guide explains both halves: **TheDiscDB**, which answers "which disc is this?" by content hash and which you can now contribute disc layouts back to (opt in under Settings → TheDiscDB Contributions, no account or API key needed), and the **Engram acoustic fingerprint network**, which answers "which episode is this track?" from chromaprint audio fingerprints, so episode identification no longer depends on finding reference subtitles for your specific show. No audio or video ever leaves your machine, contributions are pseudonymous, and everything you have sent can be deleted. Episode identification now reads from the fingerprint catalog by default and falls back to the existing TMDB, AI, and heuristic paths on a miss.
- **A "What's new" summary after you update.** Engram now shows the release notes for the version you are running, once per version, the first time you open the dashboard after updating. It works however you updated: self-update, a fresh download, or a new Docker image. You can reopen it any time by clicking the version number in the top bar.
```

- [ ] **Step 2: Verify no em or en dashes**

```bash
grep -nE "$(printf '\xe2\x80\x94|\xe2\x80\x93')" CHANGELOG.md | head
```

Expected: matches only in older, already-released sections; neither new bullet appears. See Task 7 Step 4 for why the bracket form of this command gives false positives. The `→` in "Settings → TheDiscDB Contributions" is an arrow, not a dash, and is intentional: it matches the existing 0.26.0 and 0.27.0 entries.

- [ ] **Step 3: Confirm the extractor is unaffected**

```bash
cd backend && uv run python scripts/extract_changelog.py --version 0.27.0
```

Expected: prints the 0.27.0 section. Extraction never targets `[Unreleased]`, so this only confirms the file is still parseable.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md && git commit -m "docs: changelog entries for community data and the whats-new modal"
```

---

## Final verification

- [ ] **Backend suite**

```bash
cd backend && uv run pytest tests/unit -q
```

Expected: all pass.

- [ ] **Frontend suite and build**

```bash
cd frontend && npm run test:unit && npm run lint && npm run build
```

Expected: all pass.

- [ ] **Docs build**

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Expected: succeeds with only the pre-existing warnings.

- [ ] **Lock file is unmodified**

```bash
git status --porcelain frontend/package-lock.json
```

Expected: no output. If there is any, run `git checkout frontend/package-lock.json`.

## Not in this plan

- E2E coverage for the modal. Every modal in this app leaves a stuck `opacity: 0` full-screen overlay under automation, a known pre-existing issue. An E2E test here would fight that for little value.
- Publishing the Discussions and Discord posts. Drafts live in the spec appendices; the maintainer posts them.
- Any change to feature flags, config defaults, or `backend/app/core/features.py`.

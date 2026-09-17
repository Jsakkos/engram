# Combined Episode Filenames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A track that holds several episodes (a cartoon DVD's three-segment block) can be assigned, stored, displayed, exported and filed as one combined episode such as `S01E01-E03`.

**Architecture:** Adopt the design from community PR #614 (@raiju) by cherry-picking its three episode-code commits onto this branch: one anchored parser (`app/core/episode_codes.py`) that every reader of `matched_episode` goes through, a range splice in the organizer, and a sweep of the other readers. Then add what #614 lacked: a confirmed multi-episode verdict pre-fills the combined code for review, the frontend collision checks test each episode of a combined pick, the fingerprint-contribution guard becomes one tested helper, and the dashboard range summary understands combined codes.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, pytest (backend, `uv`); React 18, TypeScript, vitest + Testing Library (frontend, `npm`).

**Spec:** `docs/superpowers/specs/2026-09-13-empty-srt-references-design.md`, section "Follow-up: PR 2 (combined filenames)".

---

## Ground rules (read before Task 0)

- **Worktree:** all work happens in `C:\Github\engram\.claude\worktrees\combined-episode-filenames` on branch `feat/combined-episode-filenames`. It is stacked on `fix/empty-srt-references` (PR #656, not merged yet). Never edit `C:\Github\engram\...` outside this worktree.
- **Never run `git stash`** in any form. Another session shares the stash stack.
- **Never run `npm install`** (it rewrites `package-lock.json`). Use `npm ci`.
- **No em dashes or en dashes** in code comments, commit messages, docs or the CHANGELOG. Check with: `git diff | grep '^+' | LC_ALL=C grep -c $'\xe2\x80\x94\|\xe2\x80\x93'` (expect `0`). Existing UI strings that already contain one are fine to leave alone; do not type new ones.
- **Never delete `backend/engram.db`.**
- **Commit trailer:** every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Commands** run from `backend/` (Python) or `frontend/` (Node) inside the worktree unless stated.

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `backend/app/core/episode_codes.py` (new, from #614) | Parse, format, normalize and split episode codes, including combined ones | 1, 2 |
| `backend/app/core/organizer.py` | Name a combined file with the range form; project every part | 1, 2 |
| `backend/app/api/routes.py` | Season roster counts a combined track in every slot | 1 |
| `backend/app/services/finalization_coordinator.py` | Canonicalize decisions and conflict keys | 1 |
| `backend/app/services/job_manager.py` | Validate and canonicalize a manual assignment | 1 |
| `backend/app/services/contribution_correction.py` | Never re-contribute a combined track | 1 |
| `backend/app/core/discdb_exporter.py`, `backend/app/services/disc_contribution_queue.py` | Publish `"17-18"` for combined titles | 2 |
| `backend/app/core/discord_notifier.py` | Summary counts episodes, not tracks | 2 |
| `backend/app/core/discdb_classifier.py` | Ingest TheDiscDB's `"17-18"` episode field | 3 |
| `backend/app/services/matching_coordinator.py` | Overlap check, DiscDB mapping codes, contribution guard helper, verdict pre-fill | 3, 4, 5 |
| `backend/app/scripts/bootstrap_library.py` | Documented deliberate non-parse of combined filenames | 3 |
| `frontend/src/components/ReviewQueue/coverage.ts` | Frontend parser, parts, collisions, `selectionCollides` | 1, 6 |
| `frontend/src/components/ReviewQueue/{Inspector,TitleList}.tsx`, `frontend/src/components/ReviewQueue.tsx` | Combined labels and per-episode collision checks | 1, 6 |
| `frontend/src/components/HistoryPage.tsx` | Amend picker offers every episode of a combined code | 3 |
| `frontend/src/types/adapters.ts` | Dashboard range summary counts combined tracks | 7 |
| `CHANGELOG.md` | User-facing entry crediting @raiju | 8 |

**Deferred (not in this plan):** #614's searchable episode picker, span control and its setting/migration, disc name in the review header, neighbouring-season assignment. The history amend path in `job_manager.py` stores the user's code as sent; it is not normalized here.

---

### Task 0: Prepare the worktree

**Files:** none changed.

- [ ] **Step 1: Confirm branch and base**

Run (from the worktree root):
```bash
git branch --show-current && git log --oneline -1 && git status --short
```
Expected: `feat/combined-episode-filenames`; HEAD is this plan's commit on top of `b99e217a fix(matching): report the most damaged season when every season refuses` (or a later `fix/empty-srt-references` commit); no changes.

- [ ] **Step 2: Confirm the #614 commits are reachable**

Run:
```bash
git cat-file -t eac0046b && git cat-file -t 059d9153 && git cat-file -t 198d46b3
```
Expected: `commit` three times. If not, run `git fetch origin pull/614/head:refs/remotes/pr/614` and retry.

- [ ] **Step 3: Install dependencies**

Run:
```bash
cd backend && uv sync
cd ../frontend && npm ci
```
Expected: both succeed. Then `git status --short` from the root shows nothing (in particular `frontend/package-lock.json` is unchanged).

- [ ] **Step 4: Create the worktree database schema**

Run from `backend/`:
```bash
uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"
```
Expected: Alembic log lines, no traceback. This creates the gitignored `backend/engram.db` for this worktree; tests that use `app.database.async_session` need it.

- [ ] **Step 5: Baseline tests**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_organizer.py tests/unit/test_naming_conventions.py -q -p no:cacheprovider
```
Run from `frontend/`:
```bash
npx vitest run src/components/ReviewQueue src/types
```
Expected: all pass. Record the counts; later tasks must not reduce them.

---

### Task 1: Cherry-pick #614's episode-code core (`eac0046b`)

Brings in `episode_codes.py`, the organizer range splice, the roster, `_normalize_episode_code`, manual-assignment validation, the contribution-correction guard, and the frontend parser, labels and per-episode Inspector collision check. It applies without conflicts on this branch.

**Files:**
- Create: `backend/app/core/episode_codes.py`, `backend/tests/unit/test_episode_codes.py`
- Modify: `CLAUDE.md`, `backend/app/api/routes.py`, `backend/app/core/organizer.py`, `backend/app/services/contribution_correction.py`, `backend/app/services/finalization_coordinator.py`, `backend/app/services/job_manager.py`, `backend/tests/unit/test_contribution_correction.py`, `backend/tests/unit/test_naming_conventions.py`, `frontend/src/components/ReviewQueue/Inspector.tsx`, `frontend/src/components/ReviewQueue/TitleList.tsx`, `frontend/src/components/ReviewQueue/coverage.ts`, `frontend/src/components/ReviewQueue/coverage.test.ts`, `frontend/src/components/ReviewQueue/types.ts`

- [ ] **Step 1: Apply without committing**

Run from the worktree root:
```bash
git cherry-pick -n eac0046b
```
Expected: no conflict output.

- [ ] **Step 2: Drop #614's CHANGELOG text**

This PR writes its own entry in Task 8.
```bash
git restore --source=HEAD --staged --worktree CHANGELOG.md
git status --short
```
Expected: the files listed above as `A`/`M`, and `CHANGELOG.md` absent from the list.

- [ ] **Step 3: Run the backend tests for the touched areas**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_episode_codes.py tests/unit/test_naming_conventions.py tests/unit/test_contribution_correction.py tests/unit/test_organizer.py tests/unit/test_finalization_coordinator.py tests/unit/test_api_routes.py tests/unit/test_job_manager.py -q -p no:cacheprovider
```
Expected: all pass.

- [ ] **Step 4: Run the frontend tests for review**

Run from `frontend/`:
```bash
npx vitest run src/components/ReviewQueue
```
Expected: all pass, including the new `combined episode codes` and `displayEpisodeCode` suites in `coverage.test.ts`.

- [ ] **Step 5: Lint**

Run from `backend/`: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` and no files to reformat.

- [ ] **Step 6: Commit, crediting the original author**

Run from the worktree root:
```bash
git commit --author="$(git log -1 --format='%an <%ae>' eac0046b)" \
  -m "feat(episodes): support one track holding several episodes" \
  -m "Cherry-picked from community PR #614 (commit eac0046b) by @raiju. Adds app/core/episode_codes.py as the one reader of episode codes, names a combined track with the Plex/Jellyfin range form, counts it in every roster slot, and keeps it out of fingerprint re-contribution." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Cherry-pick publish, count and name fixes (`059d9153`)

TheDiscDB export and the disc contribution queue publish `"17-18"`; the Discord summary counts episodes; the organizer keeps aired numbering when an ordering splits a combined track across seasons. Applies cleanly on top of Task 1.

**Files:**
- Modify: `backend/app/core/discdb_exporter.py`, `backend/app/core/discord_notifier.py`, `backend/app/core/episode_codes.py`, `backend/app/core/organizer.py`, `backend/app/services/disc_contribution_queue.py`
- Test: `backend/tests/pipeline/test_export_schema.py`, `backend/tests/unit/test_disc_contribution_queue.py`, `backend/tests/unit/test_discdb_exporter.py`, `backend/tests/unit/test_discord_notifier.py`, `backend/tests/unit/test_organizer.py`

- [ ] **Step 1: Apply without committing and drop the CHANGELOG text**

```bash
git cherry-pick -n 059d9153
git restore --source=HEAD --staged --worktree CHANGELOG.md
git status --short
```
Expected: no conflicts; the files above modified; `CHANGELOG.md` absent.

- [ ] **Step 2: Run the tests**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_episode_codes.py tests/unit/test_discdb_exporter.py tests/unit/test_disc_contribution_queue.py tests/unit/test_discord_notifier.py tests/unit/test_organizer.py tests/pipeline/test_export_schema.py -q -p no:cacheprovider
```
Expected: all pass.

- [ ] **Step 3: Lint**

`uv run ruff check . && uv run ruff format --check .` from `backend/`. Expected: clean.

- [ ] **Step 4: Commit**

```bash
git commit --author="$(git log -1 --format='%an <%ae>' 059d9153)" \
  -m "fix(episodes): publish, count and name combined tracks correctly" \
  -m "Cherry-picked from community PR #614 (commit 059d9153) by @raiju. TheDiscDB export and the disc contribution queue publish the range form TheDiscDB itself uses, the Discord summary counts episodes rather than tracks, and a projection that splits a combined track across seasons keeps the aired numbering." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Cherry-pick the remaining readers (`198d46b3`) and resolve its test conflict

`_same_episode_code` tests overlap, DiscDB mappings carry every episode, the match-time contribution parse uses the shared parser, the history amend picker offers every episode, and `bootstrap_library` documents its deliberate non-parse. Only `backend/tests/unit/test_matching_coordinator.py` conflicts.

**Files:**
- Modify: `backend/app/core/discdb_classifier.py`, `backend/app/scripts/bootstrap_library.py`, `backend/app/services/matching_coordinator.py`, `frontend/src/components/HistoryPage.tsx`
- Test: `backend/tests/unit/test_bootstrap_library.py`, `backend/tests/unit/test_discdb_classifier.py`, `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Apply without committing**

```bash
git cherry-pick -n 198d46b3
git status --short
```
Expected: `UU backend/tests/unit/test_matching_coordinator.py` (conflict) plus the other files modified.

- [ ] **Step 2: Drop the CHANGELOG text**

```bash
git restore --source=HEAD --staged --worktree CHANGELOG.md
```

- [ ] **Step 3: Resolve the test-file conflict**

Open `backend/tests/unit/test_matching_coordinator.py`. Remove every `<<<<<<<`, `=======` and `>>>>>>>` marker so that the file keeps ALL of this branch's content and ALSO gains #614's additions.

The import block from `app.services.matching_coordinator` must end up exactly:
```python
from app.services.matching_coordinator import (
    CONJOINED_SCAN_POINTS,
    MAX_CONJOINED_EPISODES,
    MULTI_EPISODE_ERROR_CODE,
    FileWaitResult,
    MatchingCoordinator,
    _apply_multi_episode_review,
    _conjoined_episode_count,
    _duration_matches_episode_runtime,
    _route_unconfirmable_title,
    _same_episode_code,
    _scan_points_for_hint,
    episode_curator,
)
```

The end of the file must keep this branch's last class (`TestConjoinedScanDepth`) and then #614's class, exactly:
```python
@pytest.mark.unit
class TestSameEpisodeCode:
    """Overlap, not equality: the caller asks "has another track taken this?".

    A prefix-anchored regex compared only the FIRST episode of each code, so a
    combined track hid every episode after its first from that question and a
    second track could claim one of them unchallenged.
    """

    def test_identical_codes_overlap(self):
        assert _same_episode_code("S01E01", "S01E01") is True

    def test_zero_padding_is_ignored(self):
        assert _same_episode_code("S1E9", "S01E09") is True

    def test_different_episodes_do_not_overlap(self):
        assert _same_episode_code("S01E01", "S01E02") is False

    def test_different_seasons_do_not_overlap(self):
        assert _same_episode_code("S01E01", "S02E01") is False

    def test_a_combined_track_overlaps_each_episode_it_claims(self):
        assert _same_episode_code("S01E01-E03", "S01E01") is True
        # The bug: E02 sits inside the range but is not its first episode.
        assert _same_episode_code("S01E01-E03", "S01E02") is True
        assert _same_episode_code("S01E01-E03", "S01E03") is True

    def test_a_combined_track_does_not_overlap_outside_its_range(self):
        assert _same_episode_code("S01E01-E03", "S01E04") is False

    def test_two_combined_tracks_overlap_on_a_shared_episode(self):
        assert _same_episode_code("S01E01-E03", "S01E03-E05") is True
        assert _same_episode_code("S01E01-E02", "S01E03-E04") is False

    def test_non_codes_fall_back_to_string_equality(self):
        assert _same_episode_code("extra", "extra") is True
        assert _same_episode_code("extra", "skip") is False
        assert _same_episode_code(None, "S01E01") is False
```
(The class docstring above replaces #614's dash with a colon; keep it that way.)

Then:
```bash
git add backend/tests/unit/test_matching_coordinator.py
grep -c "<<<<<<<\|>>>>>>>" backend/tests/unit/test_matching_coordinator.py
```
Expected: `0`.

- [ ] **Step 4: Verify the coordinator auto-merge landed where expected**

Run from the worktree root:
```bash
grep -n "format_episode_code(m.season\|format_episode_code(mapping.season\|parse_episode_code(title.matched_episode)\|elif is_multi_episode(title.matched_episode)\|return bool(set(pa\[1\]) & set(pb\[1\]))" backend/app/services/matching_coordinator.py
```
Expected: five matching lines: the two DiscDB mapping builders, the contribution parse, the `elif is_multi_episode(...)` guard, and the overlap return. (Task 4 removes the `elif`.)

- [ ] **Step 5: Run the tests**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_discdb_classifier.py tests/unit/test_bootstrap_library.py tests/unit/test_episode_codes.py -q -p no:cacheprovider
```
Expected: all pass.

- [ ] **Step 6: Lint both sides**

Run from `backend/`: `uv run ruff check . && uv run ruff format --check .`
Run from `frontend/`: `npx eslint src/components/HistoryPage.tsx --max-warnings 0`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git commit --author="$(git log -1 --format='%an <%ae>' 198d46b3)" \
  -m "fix(episodes): stop assuming one numeric episode on the remaining paths" \
  -m "Cherry-picked from community PR #614 (commit 198d46b3) by @raiju, with its test-file conflict resolved against this branch. The already-organized check tests overlap between episode sets, DiscDB mappings keep every episode TheDiscDB claims, the history amend picker offers every episode of a combined code, and bootstrap_library documents why it does not parse combined filenames." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: One tested fingerprint-contribution guard

After Task 3 the match-time enqueue has two guards for the same fact: the outer `if` (runtime hint, vote verdict) from #624 and the inner `elif is_multi_episode(...)` from #614. The inner one matters: `try_discdb_assignment` now writes a combined code for a combined DiscDB title with neither a hint nor a verdict. Merge them into one helper so all three signals are tested together.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (new helper after `_is_multi_episode_result`; the Phase 1 enqueue condition inside `_match_single_file_inner`)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Add `_may_contribute_fingerprint,` to the import block from Task 3, directly after `_duration_matches_episode_runtime,`.

Append to the end of `backend/tests/unit/test_matching_coordinator.py`:
```python
@pytest.mark.unit
class TestFingerprintContributionGuard:
    """A fingerprint row names one episode, so a track holding several must never be
    contributed, whichever signal says so: the runtime hint, the vote verdict, or a
    combined code already in matched_episode (a combined DiscDB title)."""

    def test_single_episode_track_may_contribute(self):
        assert _may_contribute_fingerprint("S01E05", None, {"score": 0.9}) is True

    def test_no_code_never_contributes(self):
        assert _may_contribute_fingerprint(None, None, None) is False

    def test_runtime_hint_blocks(self):
        assert _may_contribute_fingerprint("S01E05", 2, None) is False

    def test_confirmed_verdict_blocks(self):
        details = {"multi_episode": {"is_multi_episode": True, "codes": ["S01E01", "S01E02"]}}
        assert _may_contribute_fingerprint("S01E01", None, details) is False

    def test_combined_code_blocks(self):
        # try_discdb_assignment writes a combined code for a combined DiscDB title,
        # with neither a runtime hint nor a vote verdict to flag it.
        assert _may_contribute_fingerprint("S01E01-E02", None, {"source": "discdb"}) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py -k TestFingerprintContributionGuard -q -p no:cacheprovider
```
Expected: collection ERROR, `ImportError: cannot import name '_may_contribute_fingerprint'`.

- [ ] **Step 3: Add the helper**

In `backend/app/services/matching_coordinator.py`, directly after the `_is_multi_episode_result` function (it ends with `return bool(isinstance(verdict, dict) and verdict.get("is_multi_episode"))`), add:
```python


def _may_contribute_fingerprint(
    matched_episode: str | None, conjoined_hint: int | None, match_details: dict | None
) -> bool:
    """Whether a matched track may be published to the fingerprint network.

    A fingerprint row names exactly one episode. A track that holds several has no
    honest single-episode label, and publishing it under one code would teach every
    other user that this audio IS that episode. Three signals can say so: the
    runtime pre-filter hint, the chunk-vote verdict, and a combined code already in
    matched_episode (a combined DiscDB title carries neither of the other two).
    """
    return bool(
        matched_episode
        and not conjoined_hint
        and not _is_multi_episode_result(match_details)
        and not is_multi_episode(matched_episode)
    )
```

- [ ] **Step 4: Use it in the enqueue condition**

In `_match_single_file_inner`, replace:
```python
                    if (
                        title.chromaprint_blob
                        and title.matched_episode
                        and not conjoined_hint
                        and not _is_multi_episode_result(result.match_details)
                    ):
```
with:
```python
                    if title.chromaprint_blob and _may_contribute_fingerprint(
                        title.matched_episode, conjoined_hint, result.match_details
                    ):
```

- [ ] **Step 5: Remove the now-unreachable inner guard**

In the same block, delete this `elif` branch (added by Task 3) so the chain goes straight from the `tmdb_id_val == 0` branch to `else:`:
```python
                                elif is_multi_episode(title.matched_episode):
                                    # A combined track's audio spans several episodes
                                    # and a fingerprint row names exactly one. Under
                                    # the first episode's number, the network would
                                    # learn that this audio IS that episode, and every
                                    # later ripper of the disc would inherit the wrong
                                    # identity from us. There is no honest one-episode
                                    # label for it, so contribute nothing.
                                    logger.debug(
                                        f"Skipping contribution for title {title.id}: "
                                        f"{title.matched_episode} covers several "
                                        f"episodes, and a fingerprint "
                                        f"contribution names one."
                                    )
```
Then confirm: `grep -n "elif is_multi_episode" backend/app/services/matching_coordinator.py` prints nothing.

- [ ] **Step 6: Run the tests**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py -q -p no:cacheprovider
```
Expected: all pass, including the 5 new tests and the existing `TestConjoinedDiscDbBypass` tests.

- [ ] **Step 7: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && cd ..
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "fix(matching): keep every multi-episode signal in one contribution guard" \
  -m "The match-time enqueue checked the runtime hint and the vote verdict in one place and a combined matched_episode in another. A combined DiscDB title reaches the enqueue with only the last signal, so all three now live in one tested helper." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Pre-fill the combined code from a confirmed verdict

`_apply_multi_episode_review` still says "Engram cannot name a combined file yet". Now it can: a confirmed verdict whose codes are single episodes from one season pre-fills `matched_episode` with the combined code in playback order, and the title stays in REVIEW for a person to confirm.

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (import block; `MULTI_EPISODE_ERROR_CODE` comment; new `_combined_code` helper; `_apply_multi_episode_review`)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Append to the end of `backend/tests/unit/test_matching_coordinator.py`:
```python
@pytest.mark.unit
class TestCombinedAssignmentPrefill:
    """A confirmed multi-episode verdict pre-fills the combined code the organizer can
    now name. The title still lands in REVIEW so a person confirms it."""

    def _title(self, codes, matched="S01E01"):
        details = {
            "multi_episode": {
                "is_multi_episode": True,
                "reason": "contiguous_runs",
                "codes": codes,
            }
        }
        return SimpleNamespace(
            state=TitleState.MATCHED,
            matched_episode=matched,
            match_details=json.dumps(details),
            match_source="engram",
        )

    def test_contiguous_segments_prefill_a_range(self):
        title = self._title(["S01E01", "S01E02", "S01E03"])
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01-E03"
        message = json.loads(title.match_details)["message"]
        assert "appears to contain 3 episodes" in message
        assert "S01E01-E03" in message
        assert "cannot name a combined file" not in message

    def test_playback_order_is_kept(self):
        # A disc can pair segments TMDB does not number consecutively; the file's
        # own order is the one to name.
        title = self._title(["S01E03", "S01E01"])
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.matched_episode == "S01E03E01"

    def test_codes_from_different_seasons_are_not_prefilled(self):
        title = self._title(["S01E13", "S02E01"])
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01"
        assert "not all from one season" in json.loads(title.match_details)["message"]

    def test_unconfirmed_hint_is_not_prefilled(self):
        details = {
            "multi_episode": {
                "is_multi_episode": False,
                "reason": "insufficient_scan_depth",
                "codes": [],
            }
        }
        title = SimpleNamespace(
            state=TitleState.MATCHED,
            matched_episode="S01E01",
            match_details=json.dumps(details),
            match_source="engram",
        )
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01"
```

- [ ] **Step 2: Run them to verify they fail**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py -k TestCombinedAssignmentPrefill -q -p no:cacheprovider
```
Expected: `test_contiguous_segments_prefill_a_range`, `test_playback_order_is_kept` and `test_codes_from_different_seasons_are_not_prefilled` FAIL (matched_episode stays `S01E01`, or the message lacks the new text); `test_unconfirmed_hint_is_not_prefilled` passes.

- [ ] **Step 3: Import `format_episode_code`**

After Task 3, the import from `app.core.episode_codes` at the top of `matching_coordinator.py` reads:
```python
from app.core.episode_codes import (
    format_episode_code,
    is_multi_episode,
    parse_episode_code,
)
```
If it does, no change is needed. If `format_episode_code` is missing, add it in that position.

- [ ] **Step 4: Update the error-code comment**

Replace:
```python
# A track that is, or might be, several conjoined episodes. Parked for a human
# because Engram cannot yet NAME a multi-episode file (S01E01-E02 organizing is a
# separate change): auto-organizing it under one of its codes would silently lose
# the others.
MULTI_EPISODE_ERROR_CODE = "multi_episode_detected"
```
with:
```python
# A track that is, or might be, several conjoined episodes. Parked for a human:
# auto-organizing it under one of its codes would silently lose the others. A
# confirmed verdict pre-fills the combined code (S01E01-E03) for them to confirm.
MULTI_EPISODE_ERROR_CODE = "multi_episode_detected"
```

- [ ] **Step 5: Add the `_combined_code` helper**

Directly above `def _apply_multi_episode_review(`, add:
```python
def _combined_code(codes: list) -> str | None:
    """The combined code for a verdict's playback-ordered codes, or None.

    None when a code does not parse, a code is itself combined, fewer than two
    episodes remain, or the codes span seasons: a filename carries one season, so
    there is no honest combined name for a track that crosses one.
    """
    seasons: set[int] = set()
    episodes: list[int] = []
    for code in codes:
        parsed = parse_episode_code(code if isinstance(code, str) else None)
        if parsed is None or len(parsed[1]) != 1:
            return None
        seasons.add(parsed[0])
        episodes.append(parsed[1][0])
    if len(seasons) != 1 or len(episodes) < 2:
        return None
    return format_episode_code(seasons.pop(), episodes)


```

- [ ] **Step 6: Pre-fill and reword the confirmed message**

In `_apply_multi_episode_review`, replace:
```python
    if confirmed_multi:
        message = (
            f"This track appears to contain {len(codes)} episodes "
            f"({', '.join(codes)}). Engram cannot name a combined file yet. "
            "Assign one episode, or mark it as an Extra."
        )
```
with:
```python
    if confirmed_multi:
        combined = _combined_code(codes)
        if combined:
            # Pre-fill the assignment the review page opens with. The title stays
            # in REVIEW (set above), so nothing is filed until a person confirms.
            title.matched_episode = combined
            message = (
                f"This track appears to contain {len(codes)} episodes "
                f"({', '.join(codes)}). It is pre-filled as {combined}, which files it "
                "as one combined episode. Confirm it, or assign it differently."
            )
        else:
            message = (
                f"This track appears to contain {len(codes)} episodes "
                f"({', '.join(codes)}), but they are not all from one season, so it "
                "cannot be filed as one combined episode. Assign it by hand."
            )
```

- [ ] **Step 7: Run the tests**

Run from `backend/`:
```bash
uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_finalization_coordinator.py -q -p no:cacheprovider
```
Expected: all pass. The existing `test_confirmed_multi_episode_goes_to_review` and `test_prior_warning_is_carried_forward_not_clobbered` still pass because the message keeps the `S01E01, S01E02` list.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && cd ..
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "feat(review): pre-fill the combined episode code for a confirmed multi-episode track" \
  -m "A track the chunk votes confirm holds several episodes now opens in review already assigned its combined code, in playback order, instead of a message saying Engram cannot name the file. Codes that span seasons are left for a person to assign." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Per-episode collision checks in the title list and review page

After Task 1, `collidingCodes` returns single-episode codes, so `collisions.has(selection)` never matches a combined pick. `Inspector.tsx` already checks each part; `TitleList.tsx` and `ReviewQueue.tsx` do not.

**Files:**
- Modify: `frontend/src/components/ReviewQueue/coverage.ts`, `frontend/src/components/ReviewQueue/TitleList.tsx`, `frontend/src/components/ReviewQueue.tsx`
- Test: `frontend/src/components/ReviewQueue/coverage.test.ts`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/ReviewQueue/coverage.test.ts`, add `selectionCollides,` to the named import list from `'./coverage'` (next to `collidingCodes,`). Append to the end of the file:
```ts
describe('selectionCollides', () => {
    it('flags a combined pick when any of its episodes is contested', () => {
        const collisions = collidingCodes({ 10: 'S03E01-E03', 11: 'S03E02' });
        expect(selectionCollides('S03E01-E03', collisions)).toBe(true);
        expect(selectionCollides('S03E02', collisions)).toBe(true);
    });

    it('does not flag a combined pick with no contested episode', () => {
        const collisions = collidingCodes({ 10: 'S03E01-E02', 11: 'S03E03' });
        expect(selectionCollides('S03E01-E02', collisions)).toBe(false);
        expect(selectionCollides('S03E03', collisions)).toBe(false);
    });

    it('never flags an empty or pseudo selection', () => {
        expect(selectionCollides(undefined, new Set(['S03E01']))).toBe(false);
        expect(selectionCollides('extra', new Set(['extra']))).toBe(false);
    });
});
```

- [ ] **Step 2: Run to verify failure**

Run from `frontend/`:
```bash
npx vitest run src/components/ReviewQueue/coverage.test.ts
```
Expected: FAIL; `selectionCollides` is not exported (`is not a function` or an import error).

- [ ] **Step 3: Add the helper**

In `frontend/src/components/ReviewQueue/coverage.ts`, directly after the `collidingCodes` function, add:
```ts
/**
 * Whether a title's pick is in conflict. `collisions` holds single-episode codes,
 * so a combined pick ("S03E01-E03") is checked episode by episode: it conflicts
 * when ANY episode it claims is contested. Pseudo picks never conflict.
 */
export function selectionCollides(selection: string | undefined, collisions: Set<string>): boolean {
    if (!selection) return false;
    return episodeParts(selection).some((code) => collisions.has(code));
}
```

- [ ] **Step 4: Run to verify it passes**

Run from `frontend/`: `npx vitest run src/components/ReviewQueue/coverage.test.ts`
Expected: PASS.

- [ ] **Step 5: Use it in the title list**

In `frontend/src/components/ReviewQueue/TitleList.tsx`, change the import added in Task 1:
```tsx
import { displayEpisodeCode, episodeParts } from './coverage';
```
to:
```tsx
import { displayEpisodeCode, episodeParts, selectionCollides } from './coverage';
```
and replace:
```tsx
                const inConflict = !!selection && collisions.has(selection);
```
with:
```tsx
                const inConflict = selectionCollides(selection, collisions);
```

- [ ] **Step 6: Use it in the review page**

Find the coverage import in `frontend/src/components/ReviewQueue.tsx`:
```bash
grep -n "collidingCodes" frontend/src/components/ReviewQueue.tsx
```
The first match is inside a named import from `'./ReviewQueue/coverage'`. Add `selectionCollides` to that import's name list. Then replace:
```tsx
        const needsHelp = !currentSel || collisions.has(currentSel);
```
with:
```tsx
        const needsHelp = !currentSel || selectionCollides(currentSel, collisions);
```

- [ ] **Step 7: Type-check, lint and test**

Run from `frontend/`:
```bash
npx tsc --noEmit -p tsconfig.json
npx eslint src/components/ReviewQueue.tsx src/components/ReviewQueue --max-warnings 0
npx vitest run src/components/ReviewQueue
```
Expected: no type errors, no lint output, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ReviewQueue/coverage.ts frontend/src/components/ReviewQueue/coverage.test.ts frontend/src/components/ReviewQueue/TitleList.tsx frontend/src/components/ReviewQueue.tsx
git commit -m "fix(review): flag a combined pick that shares an episode with another track" \
  -m "The title list and the gap suggestion looked the whole combined code up in a set of single-episode codes, so a combined track never showed as conflicting. Both now check each episode it claims, as the inspector already did (open finding from the #614 review)." \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Dashboard episode-range summary understands combined codes

`computeEpisodeRangeSummary` in `frontend/src/types/adapters.ts` (added after #614 branched) matches `S(\d+)E(\d+)` and keeps only the first episode of a combined code, so a completed disc reads as having gaps it does not have.

**Files:**
- Modify: `frontend/src/types/adapters.ts`
- Test: `frontend/src/types/adapters.test.ts`

- [ ] **Step 1: Write the failing test**

In `frontend/src/types/adapters.test.ts`, directly after the test named `'shows episode range for a completed TV job'`, add:
```ts
  it('counts every episode of a combined track in the range', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'tv' }),
      [makeMatchedTitle('S02E01-E03', 1), makeMatchedTitle('S02E04', 2)],
    );
    // Read as its first episode only, the combined track left a false gap and the
    // summary added a "(2)" count. Every episode it claims fills the range.
    expect(disc.subtitle).toMatch(/^TV · S02 E01.E04$/);
  });
```

- [ ] **Step 2: Run to verify failure**

Run from `frontend/`: `npx vitest run src/types/adapters.test.ts`
Expected: FAIL; the received value ends in ` (2)`.

- [ ] **Step 3: Parse with the shared parser**

In `frontend/src/types/adapters.ts`, add after the existing `import { getRerippableStateFromTitle } from '../components/ReviewQueue/rerip';` line:
```ts
import { parseEpisodeCode } from '../components/ReviewQueue/coverage';
```
Then replace:
```ts
  const parsed = episodeCodes
    .map(ep => { const m = ep.match(/S(\d+)E(\d+)/i); return m ? { s: +m[1], e: +m[2] } : null; })
    .filter(Boolean) as Array<{ s: number; e: number }>;
```
with:
```ts
  // A combined track ("S02E01-E03") contributes every episode it claims.
  const parsed = episodeCodes.flatMap((ep) => {
    const code = parseEpisodeCode(ep);
    return code ? code.episodes.map((e) => ({ s: code.season, e })) : [];
  });
```

- [ ] **Step 4: Run to verify it passes**

Run from `frontend/`: `npx vitest run src/types/adapters.test.ts`
Expected: PASS, including the existing range tests.

- [ ] **Step 5: Type-check, lint and commit**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npx eslint src/types --max-warnings 0 && cd ..
git add frontend/src/types/adapters.ts frontend/src/types/adapters.test.ts
git commit -m "fix(dashboard): count every episode of a combined track in the disc summary" \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry under `[Unreleased]` / `### Added`**

In `CHANGELOG.md`, the `[Unreleased]` section's `### Added` list ends with the line:
```
  `docs/development/subtitle-cache-server.md`.
```
Insert after that line (keeping one blank line before the next heading):
```markdown

- **A track that holds several episodes can be filed as one combined episode.**
  Cartoon DVDs often put two or three short segments in a single track. When
  Engram confirms a track holds several episodes, review now opens with a
  combined assignment such as `S01E01-E03`, and confirming it names the file
  `Show - S01E01-E03.mkv`, the form Plex and Jellyfin read as every episode in
  the file. You can also type a combined code yourself. The season roster,
  conflict warnings, Discord summaries, the dashboard and TheDiscDB exports all
  count a combined track as each episode it holds, and it is never published to
  the fingerprint network, where a row names a single episode. (builds on #614,
  thanks @raiju!)
```

- [ ] **Step 2: Check for dashes and commit**

```bash
git diff CHANGELOG.md | grep '^+' | LC_ALL=C grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```
Expected: `0`.
```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note combined episode filenames" \
  -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Verification

**Files:** none changed unless a check fails.

- [ ] **Step 1: Backend lint**

Run from `backend/`: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 2: Full backend unit tier**

Run from `backend/` (about 3 minutes; run it in the main session, not inside a subagent):
```bash
uv run pytest tests/unit -q -p no:logging --no-header -p no:cacheprovider
```
Expected: 0 failed. About 17 errors reading `fixture 'caplog' not found` are caused by `-p no:logging`. Re-run each file named in those errors without the flag, for example:
```bash
uv run pytest tests/unit/test_ai_client.py tests/unit/test_create_job_from_staging_dedup.py tests/unit/test_drive_event_logging.py tests/unit/test_episode_identification.py tests/unit/test_errors.py tests/unit/test_guards_logging.py tests/unit/test_job_manager.py tests/unit/test_validation.py -q -p no:cacheprovider
```
Expected: all pass.

- [ ] **Step 3: Pipeline tests touched by this change**

Run from `backend/`:
```bash
uv run pytest tests/pipeline/test_export_schema.py tests/pipeline/test_organization_paths.py tests/pipeline/test_tv_episode_pipeline.py -q -p no:cacheprovider
```
Expected: all pass.

- [ ] **Step 4: Frontend unit tests, lint and build**

Run from `frontend/`:
```bash
npm run test:unit
npm run lint
npm run build
```
Expected: all pass. Then from the root, `git status --short` must not list `frontend/package-lock.json`.

- [ ] **Step 5: Branch hygiene**

Run from the root:
```bash
git log --oneline fix/empty-srt-references..HEAD
git log --format='%h %an | %(trailers:key=Co-Authored-By,valueonly,separator=;)' fix/empty-srt-references..HEAD
git diff fix/empty-srt-references..HEAD | grep '^+' | LC_ALL=C grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```
Expected: 9 commits (this plan plus Tasks 1 to 8); the three cherry-picks authored by the #614 author; every commit carries `Claude Opus 5`. The dash count may be non-zero only because of text cherry-picked verbatim from #614 (its comments use dashes); list those lines and leave them, since rewriting a credited contributor's prose is out of scope. Any dash in lines this plan wrote is a failure to fix.

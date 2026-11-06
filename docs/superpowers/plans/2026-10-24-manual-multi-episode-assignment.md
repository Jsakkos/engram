# Manual Multi-Episode Assignment Implementation Plan (#830)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the review page, let a user assign one track to two or more consecutive episodes (`S01E01-E02`) by hand, and make the server validate and canonicalize such hand-built codes.

**Architecture:** The backend already stores, organizes and covers combined codes (`app/core/episode_codes.py`); the frontend already parses them (`ReviewQueue/coverage.ts`). The gap is the Inspector's Manual `<select>`, which lists single episodes and blanks on a combined selection, and the review save path, which stores any string. We add an "Also covers" end-episode picker beside the Manual picker that builds the code through a shared `formatEpisodeCode`, and one shared server-side validator used by every assign path.

**Tech Stack:** FastAPI + SQLModel (backend, pytest), React 19 + TypeScript + vitest/RTL (frontend).

**Spec:** GitHub issue #830 (no separate design doc; the issue plus this plan's "Design calls" section are the spec).

## Global Constraints

- Episode codes are parsed and built only through `app/core/episode_codes.py` (backend) and `ReviewQueue/coverage.ts` (frontend); no local `S(\d+)E(\d+)` regex.
- `backend/tests/unit/test_matched_episode_consumers.py` must stay green (no new unclassified `matched_episode` parser).
- Codes are in the job's episode namespace (`DiscJob.episode_namespace`, tmdb or tvdb). Validation is namespace-agnostic: it checks shape only, never TMDB existence, so a TVDB-numbered job validates identically.
- No em dashes in new prose or copy.

## Design calls (made by the implementer, not the issue)

1. **Contiguous by construction.** The UI is an end-episode picker ("Also covers: through E03"), not a multi-select, so the UI can only ever emit a contiguous range in the Manual picker's season. It cannot express a gap or a second season.
2. **Gapped sets stay accepted by the server.** The matcher (`_combined_code`) and TheDiscDB (`format_episode_code(mapping.season, episodes)`) legitimately produce run-on gapped codes (`S01E01E03`), and the review page re-submits pre-filled matcher picks. Rejecting them would make a matcher result unsaveable. They are canonicalized, not rejected.
3. **Rejected by the server (422 / 400):** a non-code that is not `extra`/`skip`, which covers cross-season strings like `S01E12-S02E01` (they do not parse); and a backwards range (`S01E05-E02`), which `parse_episode_code` today silently turns into the gapped set E05+E02.
4. **Unchanged values are never rejected.** A decision whose `episode_code` equals the title's stored `matched_episode` passes through, so a legacy odd value already in the DB cannot make the whole review batch unsaveable.
5. **Changing the first episode resets the range** to a single episode. Predictable beats clever.
6. A combined selection shows its first episode in the Manual picker and its last in "Also covers". A gapped selection (matcher-only) shows a read-only "as matched" option in "Also covers".
7. "Also covers" options held by another title are labelled with that title's index, so the collision is visible before choosing; the existing conflict banner still fires after.

## Review Focus

- A gapped matcher pick re-saved untouched in a batch: must save (Task 1 test `test_gapped_set_is_canonicalized_not_rejected`).
- A lower-case or unpadded hand-built range (`s1e1-e2`): must store `S01E01-E02` (Task 1 test).
- A combined pre-filled selection opening the Inspector: Manual must show E01, not blank (Task 2 test).
- Extending a range into an episode another track holds: option is marked and the banner shows (Task 2 test).
- No roster (TMDB down): "Also covers" falls back to generated codes for the season (Task 2 test).

---

### Task 1: Server-side validation of hand-built codes

**Files:**
- Modify: `backend/app/core/episode_codes.py` (add `canonical_assignment_code`)
- Modify: `backend/app/services/finalization_coordinator.py` (`_apply_decision_fields`, `apply_review_batch`)
- Modify: `backend/app/services/job_manager.py` (`reassign_episode` uses the helper)
- Test: `backend/tests/unit/test_episode_codes.py` (or the existing episode-codes test file), `backend/tests/unit/` review-path test

**Interfaces:**
- Produces: `canonical_assignment_code(code: str) -> str` in `app/core/episode_codes.py`. Returns `"extra"`/`"skip"` unchanged, otherwise the canonical form; raises `ValueError` for a non-code or a backwards hyphen range.

- [ ] Failing tests: `canonical_assignment_code("s1e1-e2") == "S01E01-E02"`; `("S01E01E03") == "S01E01E03"`; `("S01E02-E02") == "S01E02"`; raises for `"S01E05-E02"`, `"S01E12-S02E01"`, `"garbage"`, `""`.
- [ ] Failing tests: review batch with `S01E01-E02` stores the range and both parts appear in roster coverage; review with `S01E05-E02` returns 422 and leaves the title untouched; a decision equal to the stored `matched_episode` passes even when unparseable.
- [ ] Implement; batch validates every decision before mutating any title.
- [ ] Run `uv run pytest` on the touched test files plus `tests/unit/test_matched_episode_consumers.py`; `uv run ruff check .`.
- [ ] Commit.

### Task 2: "Also covers" picker in the Inspector

**Files:**
- Modify: `frontend/src/components/ReviewQueue/coverage.ts` (add `formatEpisodeCode`; `normalizeEpisodeCode` uses it)
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx`
- Test: `frontend/src/components/ReviewQueue/coverage.test.ts`, `Inspector.test.tsx`

**Interfaces:**
- Produces: `formatEpisodeCode(season: number, episodes: number[]): string` in `coverage.ts`, mirroring the Python `format_episode_code`.

- [ ] Failing tests (coverage): `formatEpisodeCode(1, [1, 2]) === 'S01E01-E02'`, `(1, [1, 3]) === 'S01E01E03'`, `(1, [7]) === 'S01E07'`.
- [ ] Failing tests (Inspector): with selection `S01E01-E02` the Manual select has value `S01E01` and "Also covers" has value `2`; choosing "through E03" calls `onAssign('S01E01-E03')`; choosing "only this episode" calls `onAssign('S01E01')`; an option held by title 2 reads `#2`; with no selection the "Also covers" select is absent; with no roster, options fall back to generated codes.
- [ ] Implement.
- [ ] `npm run test:unit`, `npm run lint`, `npm run build`.
- [ ] Commit.

### Task 3: End-to-end check, changelog, PR

- [ ] Backend `DEBUG=true` on port 8130, Vite on 5303 (`VITE_BACKEND_PORT=8130`); simulate a TV disc; assign one track `S01E01` + also covers E02; save; confirm the organized filename uses the range form and the roster shows both episodes covered.
- [ ] Kill servers by port.
- [ ] `CHANGELOG.md` `[Unreleased]` entry; PR closing #830; comment `@claude please review this PR`.

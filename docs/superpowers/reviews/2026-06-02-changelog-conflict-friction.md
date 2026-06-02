# CHANGELOG `[Unreleased]` merge-conflict friction — assessment & recommendation

**Date:** 2026-06-02
**Status:** Recommendation made; minimal fix (Option 2) implemented in this branch.

## Problem

We run 3–4 parallel dev sessions, each producing a PR, against a repo with a
rapid release/merge cadence (multiple merges per day). The moment **one** PR
squash-merges to `main`, every other open PR develops a merge conflict — and in
nearly every case the **only** conflicting file is `CHANGELOG.md`, specifically
its `## [Unreleased]` block, because every PR adds an entry there. Each affected
PR must then be rebased onto the new `main` and have its `[Unreleased]` block
re-resolved by hand. A recent PR needed **four consecutive rebases** as other
PRs/releases merged ahead of it. The feature code itself almost always
auto-merges cleanly (different files/functions); the changelog `[Unreleased]`
block is the conflict magnet.

## Constraints this repo imposes on any fix

These are load-bearing — a fix that breaks any of them is a non-starter.

1. **`CHANGELOG.md` *is* the release-notes source.** `release.yml` runs
   `backend/scripts/extract_changelog.py --version X.Y.Z --changelog CHANGELOG.md`
   to produce the GitHub release body. The extractor (pure stdlib — the
   create-release job has no `uv`) finds a `## [X.Y.Z]` header and copies the
   body up to the next `##`. **It only ever reads finished version sections; it
   never reads `[Unreleased]`.**
2. **CI gate.** `ci.yml`'s `changelog-version-check` job runs the same script in
   `--check` mode against the version in `backend/pyproject.toml`. A
   `chore: release vX.Y.Z` PR fails fast if its `## [X.Y.Z]` section is missing.
   Normal PRs pass trivially (pyproject still points at the last released
   version, whose section already exists).
3. **Docs transclusion.** `docs/changelog.md` is just
   `--8<-- "CHANGELOG.md"` via `pymdownx.snippets` (`base_path: ["."]`,
   `check_paths: true`). The file must stay at the repo root and stay valid
   markdown, or the mkdocs `--strict` build breaks.
4. **Protected `main`.** Squash-merge only, force-push blocked, 9 named status
   checks + up-to-date branch required. Integration is via **`git rebase`**, not
   merge.
5. **Stack.** Python backend (`uv`), React/Vite frontend (`npm`), docs (mkdocs).

## The crux experiment

The whole question for the cheapest option (a git `union` merge driver) is:
**does `merge=union` actually fire during a `rebase`, with no per-clone config?**
The team rebases; it doesn't merge. I tested this in throwaway repos rather than
reasoning about it (Git 2.52 on Windows).

**Scenario A — current state (no driver).** Base `[Unreleased]` with two
existing bullets. `main` advances (a squash-merged PR B adds its bullet first);
a feature branch adds its own bullet first; `git rebase`:

```
Auto-merging CHANGELOG.md
CONFLICT (content): Merge conflict in CHANGELOG.md
RESULT: rebase CONFLICTED
  <<<<<<< HEAD
  - Feature from PR B. (#302)
  =======
  - Feature from PR A. (#301)
  >>>>>>> (PR A work)
```

Exactly the friction we hit.

**Scenario B — identical, plus one committed line `CHANGELOG.md merge=union` and
NO `git config` anywhere:**

```
RESULT: rebase SUCCEEDED
  ### Added
  - Feature from PR B. (#302)
  - Feature from PR A. (#301)
  - Existing entry one.
  - Existing entry two.
==> conflict markers present: 0
```

The rebase auto-combines. Two git facts make this work:

- **Rebase uses the merge backend.** Since Git 2.33 the default rebase backend
  is the merge/cherry-pick machinery (not the old `git am` patch backend), so it
  consults `.gitattributes` merge drivers. (Confirmed above on 2.52.)
- **`union` is a *built-in* driver** (like `text`/`binary`), so unlike a custom
  `merge=foo` driver it needs **zero** per-clone `git config`. Committing the
  `.gitattributes` line is sufficient for every clone and every CI checkout.
  `git check-attr merge CHANGELOG.md` → `union`; no `merge.union.driver` exists
  in config, and it does not — `union` is internal.

**Worst-case edge — duplicate headers.** I also tested a freshly-emptied
`[Unreleased]` (just the header, no subsections) where two PRs each *introduce*
`### Added`. Union did **not** duplicate the header: both sides share the
identical `### Added` line, so it merged to one header and unioned only the
differing bullet lines. Duplicate headers only occur when the headers themselves
differ — e.g. one PR adds `### Added`, another adds `### Changed` — and keeping
both is then the *correct* result.

## Option-by-option

### Option 1 — Changelog fragments (towncrier / scriv / changesets)

Each PR drops a separate file under `changelog.d/` (e.g.
`changelog.d/301.added.md`); a tool compiles them into `CHANGELOG.md` at release.

- **Conflict behaviour:** truly conflict-free — distinct filenames never clash.
  The strongest guarantee of any option.
- **Preserves author prose:** yes (each author writes their own fragment).
- **Fit / cost for this repo:** *moderate-to-high.* It inverts the current
  invariant: the `## [X.Y.Z]` section is **generated at release** instead of
  hand-written in the release PR. That means:
  - `changelog-version-check` can no longer just grep for an existing section;
    it must run the compiler (or assert fragments exist) — a rewrite of the gate
    and a new build step in the release flow.
  - `extract_changelog.py` keeps working **only if** the compiler writes a
    proper `## [X.Y.Z] - DATE` section into `CHANGELOG.md` first. towncrier's
    default template does *not* emit our `_Highlights: …_` italic lead — that
    line is editorial and would still be hand-written at release.
  - New dependency (`towncrier` fits the `uv` backend), new directory, new
    per-PR habit (author must add a categorised fragment, not edit the
    changelog), and CLAUDE.md rewrite.
  - mkdocs transclusion is unaffected (file stays at root, just generated).
- **Verdict:** the "correct" long-term answer if changelog volume/▸conflict
  pain ever outgrows union, but it touches the **release/CI gates**, so per the
  task brief it should be a spelled-out migration, not a drive-by change.

### Option 2 — `.gitattributes` union driver  ← **recommended**

One committed line: `CHANGELOG.md merge=union`.

- **Conflict behaviour:** concurrent `[Unreleased]` additions auto-combine on
  rebase/merge (proven above). Kills the *manual resolution* — the actual pain
  in "four consecutive rebases." Each rebase becomes a no-prompt `git rebase`.
- **Preserves author prose:** **yes** — each PR's hand-written bullet survives
  verbatim; union just keeps both. So it is strictly better than Option 3 and,
  for prose fidelity, on par with Option 1.
- **Config burden:** none. Built-in driver, honored from the committed file by
  every clone and CI checkout.
- **Blast radius:** `merge=union` affects *only* three-way merges of that one
  file. It does not touch `diff`, `blame`, `checkout`, or any other file.
- **Caveats (all cosmetic, all caught by the existing release-PR curation pass,
  which already moves `[Unreleased]` into the version section by hand):**
  - Bullet **order** is "upstream-then-yours," not chronological/grouped.
  - The rare *different-new-subsection* case keeps both headers (correct, but
    may want a tidy-up).
  - It does **not** clear GitHub's server-side "this branch has conflicts"
    banner with certainty in every case — but the team integrates by **local
    rebase + push**, and the local rebase is what we proved auto-resolves, so
    the banner clears on the next push regardless.
- **Interaction with release extraction:** none. The extractor and the CI gate
  read only finished `## [X.Y.Z]` sections, which are authored in a single
  release PR and never race. Union on `[Unreleased]` is invisible to them
  (verified: extractor preview + all 15 `test_extract_changelog.py` tests still
  pass — the change touches neither).

### Option 3 — Defer changelog out of the feature PR

Feature PRs don't touch `CHANGELOG.md`; the release PR (or a curator) writes
`[Unreleased]` from merged PR titles/labels at release time.

- **Conflict behaviour:** zero (feature PRs never touch the file).
- **Cost:** loses author-written prose *at the moment of most context*. This
  repo explicitly values curated, user-facing changelog prose ("keep it good");
  the current entries are detailed and clearly written at PR time. Reconstructing
  from PR titles at release loses that, or requires its own metadata→changelog
  automation project. It also dumps all changelog writing on the release author.
- **Verdict:** conflicts with a stated repo value. Not recommended as primary.

### Option 4 — Merge queue / auto-rebase bot

- **GitHub merge queue** serializes merges and re-tests each entry, but it
  **cannot resolve a content conflict** — a CHANGELOG `[Unreleased]` clash would
  still break the queue entry unless union (or fragments) resolves it first. So
  merge queue is *additive*, not a substitute, and it's a larger process change
  that interacts with squash-only + the 9 required checks.
- **Auto-rebase bot** (an Action that rebases green PRs) hits the same conflict —
  but *with the union driver in place* its `git rebase` would now succeed
  automatically. So a bot is a natural **Phase-3 complement to union**, removing
  even the "run the rebase command" step, once/if that residual friction matters.
- **Verdict:** revisit when volume grows; union is the prerequisite that makes
  any automated rebase actually succeed.

## Recommendation

**Adopt Option 2 (the `union` merge driver) now.** It is one committed line,
needs no per-clone setup, removes the manual-resolution friction that is the real
cost (proven by experiment), preserves author-written prose, has a blast radius
of exactly one file, and is invisible to the release/CI gates. Its only downsides
are cosmetic and are already absorbed by the release-PR curation step that exists
today.

Treat **Option 1 (towncrier fragments)** as the documented Phase-2 upgrade if
union's cosmetics ever become annoying or volume climbs; treat **Option 4
(auto-rebase bot atop union)** as Phase-3. Both are real improvements but cost
release/CI-gate changes that aren't justified by today's pain.

## What this branch changes (the minimal Option-2 fix)

1. **`.gitattributes`** — adds `CHANGELOG.md merge=union` with an explanatory
   comment (and the cosmetic caveats) matching the file's existing style.
2. **`CLAUDE.md`** — the "Release and Changelog" section notes that
   `[Unreleased]` auto-combines via the union driver, so a post-merge rebase
   resolves the changelog with no hand-editing.

Nothing else changes: `CHANGELOG.md` stays at the repo root with identical
structure, the extractor and CI gate are untouched, and mkdocs transclusion is
unaffected.

### Verification performed

- `git check-attr merge CHANGELOG.md` → `union` (README.md → `unspecified`,
  i.e. scoped to one file); no `merge.*.driver` config required or present.
- Reproduced the conflict without the driver and the clean auto-combine with it,
  via `git rebase` (the team's actual integration path), no local config set.
- `uv run python scripts/extract_changelog.py --version 0.14.1` still prints the
  correct release body; `--check` passes.
- `uv run pytest tests/unit/test_extract_changelog.py` → 15 passed.
- Changelog location/structure unchanged, so the mkdocs snippet transclusion is
  unaffected (no `--strict` regression risk introduced).

## Phase-2 migration sketch (towncrier) — for when/if we want it

Not implemented here; recorded so it's a known path.

1. `uv add --dev towncrier`; configure `[tool.towncrier]` in
   `backend/pyproject.toml` (or a root `towncrier.toml`) with `directory =
   "changelog.d"`, our category types (`added`/`changed`/`fixed`/`removed`),
   an `issue_format` that renders `(#NNN)`, and a template that matches Keep a
   Changelog.
2. Create `changelog.d/.gitkeep`. Per-PR habit becomes: add
   `changelog.d/<PR#>.<type>.md` with one prose line.
3. At release: `towncrier build --version X.Y.Z` writes the
   `## [X.Y.Z] - DATE` section into `CHANGELOG.md` and deletes the fragments.
   Hand-add the `_Highlights: …_` lead line (still editorial).
4. Rewrite `changelog-version-check` to either run `towncrier check` (asserts a
   fragment exists on a feature PR) or run `towncrier build --draft` and assert
   non-empty — keeping `extract_changelog.py` for the release body unchanged
   (it reads the section towncrier just wrote).
5. Keep the `CHANGELOG.md merge=union` line as a belt-and-suspenders backstop —
   it's harmless once fragments make conflicts rare.
6. Update CLAUDE.md "Release and Changelog" accordingly; verify mkdocs still
   builds (location unchanged).

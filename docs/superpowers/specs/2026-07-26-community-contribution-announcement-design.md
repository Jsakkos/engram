# Announcing Engram's community data features

**Date:** 2026-07-26
**Status:** Approved design, ready for planning
**Scope:** Documentation, a reusable in-app "What's new" surface, curated release notes, and two external post drafts.

## Problem

Engram now participates in two community data networks, and users have effectively
been told about neither.

| | TheDiscDB contributions | Engram fingerprint network |
|---|---|---|
| Shipped in | v0.27.0 (2026-07-24) | v0.10.0 (2026-05) |
| Master flag | `DISCDB_CONTRIBUTIONS_ENABLED = True` (`backend/app/core/features.py`) | none, always on |
| Per-user default | opt in, **off** (`discdb_contributions_enabled = False`) | opt out, **on** (`enable_fingerprint_contributions = True`) plus an explicit disclosure modal |
| Read path | lookup on since PR #430 | `enable_fingerprint_identification` now defaults **True** |
| User-facing docs | none | none; only `docs/development/fingerprint-privacy.md`, an engineering-grade wire-format reference |
| In-app surface | Contribute page and nav item | Settings toggle and `FingerprintDisclosureModal` |

Three consequences follow from that table, and they drive every decision below.

1. **The two features need opposite messaging.** TheDiscDB needs a call to action:
   nobody contributes unless they go and tick a box. The fingerprint network needs
   transparency and explanation: everyone is already contributing, and identification
   only just started reading from it. A single undifferentiated "help the community"
   message would under-sell the first and make the second sound like a surprise.

2. **The word "fingerprint" is already overloaded.** `README.md` and `docs/index.md`
   both describe the ASR-versus-subtitles matcher as "audio fingerprint matching".
   Chromaprint is the thing that actually is a fingerprint. Any document written
   without fixing this will be read as describing one system.

3. **The consent document is unreachable.** `docs/development/fingerprint-privacy.md`
   is referenced only by `mkdocs.yml` and one plan document. Nothing in the app links
   to it, including `FingerprintDisclosureModal`, the modal that asks users to accept
   those terms.

## Decisions

- **Framing:** one story, two clearly separated halves. TheDiscDB answers "which disc
  is this?"; the fingerprint network answers "which episode is this track?". They are
  adjacent halves of one identification problem, which makes the split honest rather
  than presentational.
- **In-app:** extend the existing update pipeline rather than building a parallel
  announcement system. A generic once-per-version "What's new" modal, fed by
  `CHANGELOG.md` through the existing release pipeline, announces this release and
  every future release at no extra cost.
- **External:** GitHub Discussions (Announcements category, currently unused) and
  Discord. Drafts are written here for the maintainer to post; nothing is published
  automatically.

## Workstreams

Five workstreams. A and D are writing, B and C are code, E depends on A because it
links to the new page.

| # | Workstream | Type |
|---|---|---|
| A | Docs: new user-guide page, nav entry, terminology disambiguation | writing |
| B | Backend: current-version release notes in `updater.py` | code |
| C | Frontend: once-per-version "What's new" auto-open | code |
| D | CHANGELOG: the 0.28.0 section, which is the announcement copy | writing |
| E | Post drafts for Discussions and Discord | writing |

---

### A. Documentation

**New page:** `docs/guide/contributing-data.md`, in `mkdocs.yml` nav under
*User Guide → Contributing Data*, placed after *Job History*.

Structure:

1. **Why there are two networks.** One paragraph plus a comparison table.
2. **TheDiscDB.** What a contribution contains (content hash, title to episode map,
   MakeMKV scan log, and at tier 3 the UPC/ASIN and cover art). The tier 1/2/3 model
   (`discdb_contribution_tier`, default 2). The Contribute page workflow: export,
   group multi-disc sets, enhance, submit. State plainly that lookup already benefits
   every user and has since PR #430, and that submission needs no account or API key.
   **The call to action lives here:** Settings → TheDiscDB Contributions.
3. **The Engram fingerprint network.** Opens by separating chromaprint from the
   ASR/subtitle matcher. Summarises what is sent (a short table, not the full field
   list), the per-install pseudonym, the rule that a fingerprint reaches canonical tier
   only after contributions from at least three distinct disc releases, the `/v1/forget`
   deletion path, and the fact that identification now defaults on. Links to
   `fingerprint-privacy.md` for the exhaustive wire format.
4. **FAQ.** Does Engram upload my media (no audio or video ever leaves the machine)?
   Can I turn it off? Can I delete what I have already sent? Do I need an account?

**Terminology fix,** targeted rather than a rewrite. In both `README.md` and
`docs/index.md`, the bullet "Audio fingerprint matching, identifies TV episodes via ASR
transcription matched against subtitles" becomes **Episode matching**, and a new bullet
introduces the fingerprint network as a distinct capability. Both files also gain a
short **Community data** bullet linking the new page.

**Reachability fix.** Link `fingerprint-privacy.md` from `FingerprintDisclosureModal`
and from the new guide page. The file stays at its current path so the existing nav
entry and any external links keep working.

### B. Backend: current-version release notes

`Updater._check()` returns at `if self._is_older_or_equal(tag, self._current_version)`
before ever assigning `self.release_notes`, so a user who is up to date has no notes
available. Two constraints shape the fix:

- `GITHUB_API_URL` points at `/releases/latest`, which is the **wrong release** for a
  user sitting on 0.27.0 while 0.28.0 is out. Current-version notes require a second
  call to `/releases/tags/v{current_version}`, fetched once and cached on the instance.
- The new values must **not** reuse `latest_version` / `release_notes`. `UpdateBanner`
  keys off `state === 'ready'` and the update flow keys off `latest_version`; writing
  the current version into those fields risks offering an update to the version already
  running. Use distinct fields: `current_release_notes` and `current_release_url`.

Both new fields must be added to **`get_status()` (REST) and
`broadcast_update_status()` (WS)**. These two serialisers have drifted before; a field
present in one and absent from the other produces a UI that works on reload and not on
live update.

Failure handling matches the existing check: network or rate-limit failures log at debug
and leave the fields `None`. There is no retry storm and no user-visible error.

### C. Frontend: once-per-version "What's new"

**Trigger.** Not `last_update_success_version`. That field is only set by a successful
self-update, so Docker users and anyone who downloads a release manually would never see
the modal. Instead compare `localStorage["engram:lastSeenVersion"]` against
`updateStatus.current_version`:

- key absent: seed it silently and show nothing (a brand new install should not get a
  what's-new modal during the setup wizard);
- key present and different: show the modal once, then write the new value;
- key present and equal: do nothing.

This mirrors the dedupe pattern already used by `useUpdateSuccessToast` and covers every
install path.

**Components.** A new `useWhatsNewModal` hook, plus a read-only mode for the existing
`UpdateModal`: same markdown body and chrome, with "Skip this version" and "Restart"
replaced by a single "Close". If `current_release_notes` is null (rate-limited GitHub,
air-gapped install) the modal does not open, and the localStorage key still advances so
it does not retry on every launch.

**Bonus.** With current-version notes always available, "What's new" becomes reachable
permanently from the version string in Settings, not only from an update banner that
renders while `state === 'ready'`.

### D. CHANGELOG

`extract_changelog.py` feeds the GitHub release body, which `updater.py` feeds to the
modal. The 0.28.0 `_Highlights:_` line and its `### Added` prose are therefore literally
what every user reads on first launch after updating. This is announcement copy, not a
changelog chore, and should be budgeted as writing time.

Two `### Added` entries: one for the community data story linking the new docs page, one
for the what's-new modal itself.

### E. External posts

Drafts are held as appendices to this document rather than as loose files: they stay
version controlled, they keep the repository root clean, and they are easy to copy from.
See Appendix A (Discussions) and Appendix B (Discord).

The maintainer posts them. Nothing here publishes anything.

---

## Testing

- **Backend:** unit tests for the tag-specific fetch, covering the success path, a 404
  for a tag with no GitHub release, and a network failure. `httpx` mocked, as in the
  existing updater tests.
- **Frontend:** vitest coverage in the existing `npm run test:unit` tier for the hook's
  three paths: seed on absent key, show once on changed version, no-op on unchanged
  version, plus the null-notes case.
- **No E2E for the modal.** Every modal in this app leaves a stuck `opacity: 0`
  full-screen overlay under automation, a known pre-existing issue unrelated to this
  work. An E2E test here would be fighting that for little value.
- **Docs:** `mkdocs build` from the repository root, without `--strict` (18 known
  warnings are expected).

## Out of scope

- Reddit and other external community posts.
- A bespoke one-time "spotlight" card. The generic modal carries this release; a
  purpose-built panel would be a throwaway asset.
- Any change to feature flags or defaults. Nothing is enabled or disabled by this work.
- Any nudge UI pushing users toward the TheDiscDB opt-in. That is a follow-up if the
  announcement under-performs.

---

## Appendix A: GitHub Discussions post (Announcements)

> **Title:** Engram is a shared network now: contributing disc data and audio fingerprints

Engram has quietly grown two community data features, and it is past time to explain
both properly. They solve adjacent halves of the same problem.

**Which disc is this?** That is TheDiscDB. A pressed disc has a content hash derived from
its file layout, so once anyone has mapped a given pressing's titles to episodes, every
other person who inserts that same disc gets the answer instantly. Engram has been
*reading* TheDiscDB for a while. As of v0.27.0 it can also *write* to it.

**Which episode is this track?** That is Engram's own acoustic fingerprint network. It
uses chromaprint, which condenses a short window of audio into a perceptual hash. Match
that hash against a catalog and you know which episode a track is without needing
subtitles for the show at all.

### TheDiscDB contributions: opt in

This is the half that needs you to do something. It is off by default and stays off
until you turn it on in **Settings → TheDiscDB Contributions**.

Once it is on, the **Contribute** page lets you export what Engram learned about a disc,
group multi-disc box sets, enhance the record with a UPC or cover art, and submit it to
[thediscdb.com](https://thediscdb.com). No account, no API key. There are three sharing
tiers, so "hash and title map only" and "everything including images" are both valid
choices.

What a contribution contains: the disc content hash, the title to episode mapping, the
MakeMKV scan log, and at the highest tier the UPC/ASIN and cover art you supplied. It
describes the factory pressing, not you.

### The fingerprint network: already running, here is what it does

This half is on by default, gated behind the disclosure dialog Engram shows before the
first upload, and it works without any further action from you. Two things are worth
stating plainly.

**No audio or video ever leaves your machine.** What is sent is a chromaprint hash
sequence: 30-second windows reduced to a stream of 32-bit integers, compressed. You
cannot reconstruct audio from it. Alongside it go the TMDB ID, season and episode
numbers, the local matcher's confidence, how the episode was identified, the Engram
version, and a random per-install UUID used as a pseudonym.

**You can withdraw.** The pseudonym is what makes deletion possible: `/v1/forget`
removes everything sent under it. The toggle is in Settings, and you can rotate the
pseudonym at any time.

A fingerprint only reaches canonical tier after contributions from at least three
distinct disc releases agree, which is what keeps one person's mislabelled rip from
poisoning the catalog.

Identification now reads from this catalog by default. On a miss it falls back to the
existing TMDB, AI, and heuristic paths, exactly as before.

### Why this matters

Episode identification has depended on finding reference subtitles for the specific show
you are ripping. For popular shows that works. For obscure ones it does not, and there
is nothing an individual user can do about it. Both of these networks attack the same
weakness: every disc someone identifies correctly is a disc nobody else has to identify
again.

Full details, including exactly what is transmitted field by field:
[Contributing Data](https://jsakkos.github.io/engram/guide/contributing-data/).

Questions and objections welcome in this thread.

---

## Appendix B: Discord announcement

> Engram now has two community data features, and neither has ever been properly
> explained. Short version:
>
> **TheDiscDB contributions (v0.27.0, opt in).** Engram has been reading TheDiscDB to
> identify discs by content hash. It can now write back to it too: export a disc, group
> box sets, add a UPC or cover art, submit. No account or API key needed. Off by default,
> switch it on in Settings → TheDiscDB Contributions.
>
> **The acoustic fingerprint network (on by default).** Chromaprint hashes of short audio
> windows, used to identify episodes without needing subtitles for the show. No audio or
> video ever leaves your machine, contributions are pseudonymous, and `/v1/forget` deletes
> everything you have sent. Identification now reads from the catalog by default and falls
> back to the old path on a miss.
>
> Both attack the same weakness: episode ID currently depends on finding reference
> subtitles for your specific show, which fails on obscure titles. Every disc identified
> correctly by one person is a disc nobody else has to identify again.
>
> Full write-up, including field-by-field detail on what gets transmitted:
> <https://jsakkos.github.io/engram/guide/contributing-data/>

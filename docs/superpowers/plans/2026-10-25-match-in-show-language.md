# Match in the Show's Own Language Implementation Plan (#241, phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-English show whose reference subtitles exist in its own language matches
automatically in that language. Everything that cannot match that way keeps phase 1's behaviour.

**Architecture:** The job's match language rides in a ContextVar beside the episode namespace and
is bound at the same sites. Every reference path already goes through `corpus_dir_name`, so
composing the language into `corpus_dir_suffix()` (`@de`, `@tvdb@de`) moves reads and writes to the
language's own folder and hides the English precomputed pack, with no parameter threaded through
the matcher. `EpisodeMatcher.audio_plan` already takes a language; it defaults to the bound one.

**Spec:** `docs/superpowers/specs/2026-10-20-non-english-content-design.md` (phase 2, maintainer
decisions of 2026-10-20). This plan details the phase 2 outline (Tasks 10 to 15) of
`docs/superpowers/plans/2026-10-20-non-english-content.md`.

## Pull requests and merge order

The maintainer asked for three PRs (issue comment, 2026-10-24). They merge in this order, so `main`
is safe at every merge point:

| Merge | Content (maintainer's numbering) | Plan tasks | After it merges |
|---|---|---|---|
| A | PR 1: language resolution and audio-track mapping | 10, 11, 12 | Inert: no references exist in another language, so every job decides English. |
| B | PR 3: Whisper language and TF-IDF analyzer | 13, 14, 15 | Inert for the same reason; the language reaches Whisper and the vectorizer. |
| C | PR 2: subtitles in the show's language | 16, 17, 18 | Live: references are downloaded, the decision can pick the language. "Fixes #241". |

Merging C before B would match German audio, transcribed with Whisper forced to English, against
German references.

## Global Constraints

- English is byte-identical to today: no new key, folder, filename or request for a job whose
  match language is English. Tests assert the exact English paths and keys.
- Normalized codes come from `app/core/languages.py`; nothing else maps codes.
- Unknown, undetermined or unmapped content language means English.
- New `DiscJob` columns are nullable (`None` = not decided, read as English). Alembic revision,
  nothing in `database.py`. Check `uv run alembic heads` before writing `down_revision`.
- Language folders are never published: one `is_local_only_corpus_dir(name)` predicate (`"@" in
  name`) replaces the three `endswith("@tvdb")` checks in `backend/scripts/`.
- Chromaprint keeps the English plan (`_plan_file_audio`): fpcalc takes ffmpeg's own pick, and
  skipping a contribution is the safe side. The no-subtitles LLM fallback stays English.
- No new Python dependency. No em dashes in prose.
- Backend from `backend/`: `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.

## Designer's calls (check these)

1. **The content language is read when the language is decided, not at identification.** A job's
   TMDB identity is set in many places (identification, the name prompt, re-identification,
   imports, the review-page correction). Reading `original_language` (cached TMDB details) next to
   `decide_job_namespace` covers all of them in one place and never goes stale after a correction.
2. **The per-title ladder is audio only.** Show language stream (`match`, `untagged`, `unprobed`),
   else an English stream, else the phase 1 refusal naming both languages. Whether English
   references exist is not part of the ladder: the matcher's existing "no reference files" path
   handles that exactly as today, and the prewarm (which has no show identity) reaches the same
   answer as the curator.
3. **`untagged` in the show's language means "try the show's language".** The same rule phase 1
   applies to English, and the stream is the one the job's language would most likely be.

## Review Focus

1. **English job:** no new folder, key or request. Tests in Tasks 11, 13, 16.
2. **German job, German audio, no German references yet:** decides English; the track is refused
   exactly as phase 1 refuses it. Test in Task 11.
3. **German job, a track with only an English dub:** matched in English, against English references.
   Test in Task 12.
4. **Prewarm and curator agree** on language and stream for every verdict. Test in Task 12.
5. **TVDB plus language:** `@tvdb@de`, never `@de@tvdb`, and the scripts skip it. Test in Task 10.

---

## Merge A: language resolution and audio-track mapping

### Task 10: The match-language context

**Files:**
- Create: `backend/app/core/match_language.py`
- Modify: `backend/app/core/episode_namespace.py` (`corpus_dir_suffix`)
- Modify: `backend/app/matcher/episode_identification.py` (`load_precomputed_manifest`)
- Modify: `backend/scripts/{pack_subtitle_cache,normalize_subtitle_cache,migrate_subtitle_cache_keys}.py`
- Test: `backend/tests/unit/test_match_language.py`

**Interfaces:**
- `ENGLISH = "en"`
- `current_match_language() -> str` (ContextVar, default `"en"`)
- `match_language_context(language: str | None)` (normalizes; `None` and unknown codes bind `"en"`)
- `language_dir_suffix() -> str`: `""` for English, else `"@<code>"`
- `is_local_only_corpus_dir(name: str) -> bool`: `"@" in name`
- `content_language(details: dict | None) -> str | None`: TMDB `original_language` through
  `normalize_language`; TMDB's own `cn` (Cantonese) reads as `zh`, `xx` (no language) as `None`
- `corpus_dir_suffix()` returns the namespace suffix then the language suffix

- [ ] **Step 1: Failing tests:** context default and binding; `corpus_dir_name(123, "X")` is `"123"`
  under English, `"123@de"` under German, `"123@tvdb@de"` under both; manifest is `None` under
  German; `content_language` for `de`, `cn`, `xx`, `""`, missing; the predicate on `123`,
  `123@tvdb`, `123@de`.
- [ ] **Step 2: Run, expect failures.** **Step 3: Implement.** **Step 4: Run, expect PASS.**

### Task 11: Decide and store the job's language

**Files:**
- Modify: `backend/app/models/disc_job.py` (`content_language`, `match_language`)
- Create: `backend/migrations/versions/<rev>_add_job_match_language.py`
- Create: `backend/app/services/match_language_service.py`
- Modify: `backend/app/services/matching_coordinator.py` (`download_subtitles`,
  `download_subtitles_all_seasons`, `_run_match_single_file`; `_job_match_language`)
- Modify: `backend/app/api/routes.py` (the coverage, manual-subtitle and LLM routes that bind the
  namespace)
- Test: `backend/tests/unit/test_match_language_service.py`

**Interfaces:**
- `async decide_job_match_language(job_id: int) -> str`. Never raises. TV job with `tmdb_id` and
  `detected_season`: store `content_language`; store `match_language` = the content language when it
  is not English and the season has at least `MIN_USABLE_REFERENCES` references in its folder, else
  `"en"`. Anything else leaves the job as it is and returns its stored value or `"en"`.
- `MatchingCoordinator._job_match_language(job_id) -> str` (fresh read, like `_job_namespace`).
- The sites that read references for a job bind `match_language_context` with the stored value
  beside `namespace_context`: `_run_match_single_file`, and the coverage, manual-subtitle and LLM
  routes. The download sites do not: until Task 17 the downloader fetches English, and binding the
  language there would write English subtitles into the language's folder.

- [ ] **Step 1: Failing tests:** English show stores `en`/`en`; German show without German
  references stores `de`/`en`; German show with two references in `<id>@de` stores `de`/`de`; TMDB
  failure leaves the job untouched; movie job untouched.
- [ ] **Step 2 to 4:** implement, run. `alembic upgrade head`, `downgrade base`, `upgrade head`.

### Task 12: One per-title ladder for curator and prewarm

**Files:**
- Modify: `backend/app/matcher/episode_identification.py` (`audio_plan` default, `plan_title_audio`,
  `match_details` provenance)
- Modify: `backend/app/matcher/subtitle_utils.py` (`language_refusal_details`)
- Modify: `backend/app/core/curator.py` (plan, refusal, bind the title's language around ASR)
- Modify: `backend/app/services/transcription_prewarm.py` (bind the job's contexts; same ladder)
- Test: `backend/tests/unit/test_plan_title_audio.py`, curator and prewarm tests

**Interfaces:**
- `EpisodeMatcher.audio_plan(video_file, language: str | None = None)`: `None` means the bound
  match language.
- `EpisodeMatcher.plan_title_audio(video_file) -> tuple[str, AudioPlan]`: the ladder of designer's
  call 2. English jobs return exactly `("en", audio_plan(file, "en"))`.
- `language_refusal_details(plan, match_language="en")`: a non-English job's message names the
  show's language and English ("...and Engram matches this show by German or English dialogue...").
- `match_details` gains `match_language` and `audio_stream` (audio-relative index or `None`).

- [ ] **Step 1: Failing tests:** ladder table (German job: `de` stream, untagged, `en` dub only,
  `ja` only, no audio, unprobed; English job unchanged); curator runs `identify_episode` with the
  ladder's language bound and stream mapped; refusal message names both languages; prewarm warms
  the same stream under the same transcript key as the curator would use.
- [ ] **Step 2 to 4:** implement, run.
- [ ] **Step 5:** CLAUDE.md Key Patterns: extend the #241 bullet with the match-language context.
  CHANGELOG: none (no user-visible change yet).
- [ ] **Step 6:** Full unit and pipeline suites, ruff. PR "Refs #241", body names the merge order.

---

## Merge B: Whisper language and TF-IDF analyzer

### Task 13: Whisper transcribes in the bound language

**Files:** `backend/app/matcher/asr_models.py`, `backend/app/matcher/asr_provider.py`,
`backend/app/matcher/episode_identification.py` (transcription keys)

- `transcribe(audio_path, language="en")`; the matcher passes the bound language mapped to
  Whisper's code (`nb` and `nn` to `no`), or `None` (auto-detect) when Whisper lacks it.
- The language joins the L1 and L2 transcript keys for non-English only; English keys unchanged.
- Tests: English call and keys identical; German call passes `language="de"`; keys differ.

### Task 14: TF-IDF analyzer per script

**Files:** `backend/app/matcher/episode_identification.py` (`TfidfMatcher.prepare`)

- `char_wb`, `ngram_range=(2, 4)` for `ja`, `zh`, `th`; the word analyzer otherwise. The analyzer
  joins the TF-IDF matcher's cache signature.
- Tests: a small Japanese fixture corpus ranks the right episode; English vectorizer unchanged.

### Task 15: Unspaced scripts always go to review

Maintainer decision 2: a track matched in `ja`, `zh` or `th` parks in review whatever its
confidence, with a note that says why, until real discs calibrate character n-grams.

---

## Merge C: subtitles in the show's language

### Task 16: Providers take the language

**Files:** `backend/app/matcher/testing_service.py`, `subtitle_provider.py`,
`addic7ed_client.py`, `tvsubtitles_client.py`, `provider_scheduler.py`

- OpenSubtitles `languages=` (region variants for `pt` and `zh`), Addic7ed by `language_name`,
  tvsubtitles only for the languages it lists. Downloads land in the bound folder.
- English requests and paths byte-identical (tests).

### Task 17: The decision downloads before it decides

`download_subtitles` for a non-English content language first downloads that season in the
language, with the language bound, then `decide_job_match_language` decides on what landed.
English references download as today, unbound, so the per-title English fallback has references.

### Task 18: Provenance in the review page, docs, PR

- The Inspector shows the match language beside the source chip when it is not English.
- CHANGELOG `### Added` (`thanks @wintheriscomming!`), troubleshooting page, CLAUDE.md.
- Replay: Babylon Berlin S01 disc 1 (the contributor's export) reproduces 0.72 to 0.82.
- PR "Fixes #241".

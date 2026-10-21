# Non-English Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop silently mis-matching TV tracks whose dialogue is not English: transcribe the right
audio stream, park unmatchable tracks in review with a reason that names the language (phase 1),
then match non-English shows in their own language (phase 2).

**Architecture:** One language-code module; scan-time SINFO capture stored on `DiscTitle` as
evidence; a per-file ffprobe "audio plan" owned by `EpisodeMatcher` that picks the stream
(`-map 0:a:<n>`) and returns a verdict; the curator refuses ASR on a `foreign` verdict after the
chromaprint prepass, and the coordinator/finalizer treat that refusal like the existing
`references_unreadable` one.

**Tech Stack:** Python 3.11, FastAPI, SQLModel + Alembic, ffmpeg/ffprobe, faster-whisper,
scikit-learn; React 19 + vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-non-english-content-design.md`

## Global Constraints

- Phase 1 PR says "Refs #241"; phase 2 says "Fixes #241".
- Any probe that cannot decide (ffprobe missing, error, untagged streams, unknown codes) yields
  today's behaviour. The only new no-ASR outcome is verdict `foreign` (and `no_audio`).
- Review error code string: `audio_language_unsupported`.
- Normalized language form: ISO 639-1 where the table maps it, else the lower-case three-letter code;
  `""`, `und`, `mis`, `mul`, `zxx`, `qaa`..`qtz` normalize to `None`.
- No new Python dependency (frozen-build hidden-import risk).
- New `DiscTitle` columns are nullable; Alembic revision plus nothing else (`_add_missing_columns()`
  covers frozen builds). Check `uv run alembic heads` for the current head before writing
  `down_revision`.
- Do not change `_build_markdown_summary`'s `report.md` format (the Discord bot parses it).
- Never touch MakeMKV `settings.conf` beyond what exists.
- Real `~/.engram/cache` stays read-only; tests use tmp dirs.
- CHANGELOG `[Unreleased]` entry credits the contributor: `(thanks @wintheriscomming!)`.
- No em dashes in prose (code comments, CHANGELOG, PR text).
- Backend from `backend/`: `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.
  Frontend from `frontend/`: `npm run test:unit`, `npm run lint`, `npm run build`.

## Review Focus

1. **A disc whose every episode-length title is `foreign`.** Expect review with the language reason,
   not `_detect_wrong_show` naming a same-name twin and not a cross-season re-run. Test in Task 7.
2. **An English disc with a foreign dub flagged default** (ffmpeg would have picked the dub). Expect
   the English stream to be mapped. Test in Task 5.
3. **Previously cached transcripts of English discs.** Expect stream 0 to keep today's L2 transcript
   key so existing caches still hit. Test in Task 5.
4. **ffprobe missing or failing.** Expect verdict `unprobed`, no `-map`, ASR proceeds exactly as
   today. Tests in Tasks 4 and 6.
5. **Auto-escalation (deep re-match) over a language-parked title.** Expect `match_details` kept and
   no re-dispatch. Test in Task 7.

---

## Phase 1: detect and route

### Task 1: Language-code module

**Files:**
- Create: `backend/app/core/languages.py`
- Test: `backend/tests/unit/test_languages.py`

**Interfaces:**
- Produces: `normalize_language(code: str | None) -> str | None`,
  `language_name(code: str | None) -> str`, `is_english(code: str | None) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("raw,expected", [
    ("eng", "en"), ("EN", "en"), ("jpn", "ja"), ("fre", "fr"), ("fra", "fr"),
    ("ger", "de"), ("deu", "de"), ("chi", "zh"), ("zho", "zh"), ("spa", "es"),
    ("osa", "osa"), ("", None), (None, None), ("und", None), ("zxx", None),
    ("mul", None), ("mis", None), ("qaa", None), ("qtz", None),
])
def test_normalize_language(raw, expected):
    assert normalize_language(raw) == expected

def test_language_name():
    assert language_name("ja") == "Japanese"
    assert language_name("jpn") == "Japanese"
    assert language_name("osa") == "OSA"
    assert language_name(None) == "an unknown language"

def test_is_english():
    assert is_english("eng") and is_english("en")
    assert not is_english("jpn") and not is_english(None)
```

- [ ] **Step 2: Run, expect ImportError.** `uv run pytest tests/unit/test_languages.py -v`
- [ ] **Step 3: Implement.** One dict keyed by 639-1 holding `(name, {639-2 B/T aliases})` for about
  40 languages (en, ja, zh, ko, th, vi, de, fr, es, pt, it, nl, sv, no, nb, da, fi, is, pl, cs, sk,
  hu, ro, bg, ru, uk, el, tr, ar, he, hi, ta, te, bn, id, ms, tl, fa, hr, sr); derive the reverse
  alias map from it.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(languages): normalize disc language codes (#241)`

### Task 2: Parse SINFO stream languages at scan time

**Files:**
- Modify: `backend/app/core/analyst.py` (`TitleInfo`, ~line 254)
- Modify: `backend/app/core/extractor.py` (`_parse_disc_info`, ~line 1951; replace the attr-28
  `pass` with a comment that attr 28 is MakeMKV's metadata language, not the audio language)
- Test: `backend/tests/unit/test_extractor.py`

**Interfaces:**
- Consumes: `normalize_language` (Task 1)
- Produces: `TitleInfo.audio_languages: list[str | None]`,
  `TitleInfo.subtitle_languages: list[str | None]` (default empty lists, stream order)

- [ ] **Step 1: Write the failing tests** using trimmed real lines (five-field SINFO):

```python
SCAN = '\n'.join([
    'TINFO:2,9,0,"0:33:10"', 'TINFO:2,28,0,"eng"',
    'SINFO:2,0,1,6201,"Video"',
    'SINFO:2,1,1,6202,"Audio"', 'SINFO:2,1,3,0,"osa"',
    'SINFO:2,2,1,6203,"Subtitles"', 'SINFO:2,2,3,0,"eng"',
    'TINFO:5,9,0,"0:06:36"',
    'SINFO:5,1,1,6202,"Audio"',                     # DVD: no attr 3 at all
    'SINFO:5,2,1,6202,"Audio"', 'SINFO:5,2,3,0,"fre"',
    'SINFO:5,3,1,6203,"Subtitles"', 'SINFO:5,3,3,0,"ger"',
])

def test_parse_disc_info_collects_stream_languages(extractor):
    titles, _ = extractor._parse_disc_info(SCAN)
    by_idx = {t.index: t for t in titles}
    assert by_idx[2].audio_languages == ["osa"]          # TINFO 28 "eng" ignored
    assert by_idx[2].subtitle_languages == ["en"]
    assert by_idx[5].audio_languages == [None, "fr"]     # untagged slot kept
    assert by_idx[5].subtitle_languages == ["de"]

def test_sinfo_attr3_before_type_line_is_still_classified(extractor):
    # attr order is not guaranteed; classify at end of parse, not per line
    titles, _ = extractor._parse_disc_info(
        'TINFO:0,9,0,"0:22:00"\nSINFO:0,1,3,0,"jpn"\nSINFO:0,1,1,6202,"Audio"')
    assert titles[0].audio_languages == ["ja"]
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: audio_languages`).
  `uv run pytest tests/unit/test_extractor.py -k stream_languages -v`
- [ ] **Step 3: Implement.** Regex `SINFO:(\d+),(\d+),(\d+),(\d+),"(.*)"`. Collect
  `{(title, stream): {"type": code, "lang": value}}` keyed on attr 1's numeric code (6202 audio,
  6203 subtitles), then fill the two lists in stream order after the loop. A SINFO line for a title
  with no TINFO creates the `TitleInfo` like TINFO does.
- [ ] **Step 4: Run, expect PASS;** run the whole `test_extractor.py`.
- [ ] **Step 5: Commit** `feat(extractor): capture per-stream audio/subtitle languages (#241)`

### Task 3: Persist the scan languages on `DiscTitle`

**Files:**
- Modify: `backend/app/models/disc_job.py` (`DiscTitle`)
- Create: `backend/migrations/versions/<rev>_add_disc_titles_stream_languages.py`
- Modify: `backend/app/services/identification_coordinator.py` (`DiscTitle(...)` creation ~line 648)
- Modify: `frontend/src/types/index.ts` (title type, near `discdb_flag_reason`)
- Test: `backend/tests/unit/test_identification_coordinator.py` (or the existing test that covers
  the scan-to-rows save), `backend/tests/unit/test_database_migration.py`

**Interfaces:**
- Consumes: `TitleInfo.audio_languages/subtitle_languages` (Task 2)
- Produces: `DiscTitle.audio_languages: str | None`, `DiscTitle.subtitle_languages: str | None`
  (JSON text of `list[str | None]`; `None` = unknown), plus helper
  `DiscTitle.audio_language_list() -> list[str | None] | None` (tolerates bad JSON → `None`)

- [ ] **Step 1: Write the failing tests:** saving a scanned title with
  `audio_languages=["ja", None]` stores `'["ja", null]'`; an empty list stores `None`; the
  reconciler test asserts both columns exist after `init_db()` on a pre-existing `disc_titles`
  table missing them (follow the file's existing column-add test).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the two `Field(default=None)` columns, the helper, the copy in the
  identification coordinator, the Alembic revision (two `batch_alter_table` add_column / drop_column),
  and `audio_languages?: string | null; subtitle_languages?: string | null;` in the TS type.
- [ ] **Step 4: Run, expect PASS;** `uv run pytest tests/unit/test_database_migration.py tests/unit/test_migration_idempotency.py`.
- [ ] **Step 5: Commit** `feat(models): store disc stream languages on titles (#241)`

### Task 4: Audio-stream probe and plan

**Files:**
- Create: `backend/app/matcher/audio_streams.py`
- Test: `backend/tests/unit/test_audio_streams.py`

**Interfaces:**
- Consumes: `normalize_language` (Task 1)
- Produces:
  - `@dataclass(frozen=True) AudioStream(audio_index: int, language: str | None, title: str, channels: int, default: bool, commentary: bool)`
    (`commentary` = disposition comment or visual_impaired, or title matches the pattern below)
  - `probe_audio_streams(path: Path) -> list[AudioStream] | None` (memoized on resolved path,
    size, mtime_ns; `None` on missing ffprobe, non-zero exit, timeout, bad JSON)
  - `@dataclass(frozen=True) AudioPlan(verdict: str, stream: AudioStream | None, languages: list[str | None])`
    with verdict in `"match" | "untagged" | "foreign" | "no_audio" | "unprobed"`
  - `plan_audio(streams: list[AudioStream] | None, language: str) -> AudioPlan`
  - `COMMENTARY_PATTERN = re.compile(r"comment|audio description|described|kommentar|commentaire|comentario|commento", re.I)`

- [ ] **Step 1: Write the failing tests**

```python
def s(i, lang, *, title="", default=False, commentary=False, ch=2):
    return AudioStream(i, lang, title, ch, default, commentary)

def test_match_prefers_default_among_language_streams():
    p = plan_audio([s(0, "ja", default=True), s(1, "en"), s(2, "en", default=True)], "en")
    assert (p.verdict, p.stream.audio_index) == ("match", 2)

def test_match_first_language_stream_when_none_default():
    assert plan_audio([s(0, "ja", default=True), s(1, "en")], "en").stream.audio_index == 1

def test_untagged_used_when_no_language_stream():
    p = plan_audio([s(0, None), s(1, "fr")], "en")
    assert (p.verdict, p.stream.audio_index) == ("untagged", 0)

def test_foreign_when_every_usable_stream_is_other_language():
    p = plan_audio([s(0, "ja"), s(1, "en", commentary=True)], "en")
    assert p.verdict == "foreign" and p.stream is None and p.languages == ["ja", "en"]

def test_no_audio_and_unprobed():
    assert plan_audio([], "en").verdict == "no_audio"
    assert plan_audio(None, "en").verdict == "unprobed"

def test_probe_parses_ffprobe_json(monkeypatch, tmp_path):
    # fake subprocess.run returning ffprobe -of json with tags.language "jpn",
    # tags.title "Commentary", disposition.comment 0, channels 6 -> commentary True by title
    ...

def test_probe_returns_none_when_ffprobe_missing(monkeypatch, tmp_path):
    # subprocess.run raises FileNotFoundError -> None, and nothing raises
    ...
```

- [ ] **Step 2: Run, expect ImportError.**
- [ ] **Step 3: Implement.** Command:
  `ffprobe -v error -select_streams a -show_entries stream=index,channels:stream_tags=language,title:stream_disposition=default,comment,visual_impaired -of json <path>`,
  `timeout=30`, bare `ffprobe` as `get_video_duration` uses. `audio_index` is the position within
  the returned audio list (what `-map 0:a:<n>` expects), not the global stream index.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(matcher): probe and plan a file's audio streams (#241)`

### Task 5: The matcher transcribes the planned stream

**Files:**
- Modify: `backend/app/matcher/episode_identification.py` (`extract_audio_chunk` ~1252,
  `_chunk_path` ~1150, `_transcription_key` ~1155, `transcribe_chunk_cached` ~1197,
  `transcribe_full` ~1574)
- Test: `backend/tests/unit/test_episode_identification.py`, `backend/tests/unit/test_transcription_prewarm.py`

**Interfaces:**
- Consumes: `probe_audio_streams`, `plan_audio`, `AudioPlan` (Task 4)
- Produces: `EpisodeMatcher.audio_plan(video_file, language: str = "en") -> AudioPlan` (memoized
  per resolved path and language). Phase 2 swaps the `"en"` default for the job's match language.

Design call: the plan lives on the matcher so `extract_audio_chunk`, the transcript caches and the
prewarm (which drives the same matcher class) can never disagree about the stream. Stream index 0
keeps today's cache keys (Review Focus 3); other indices append `#a<n>` to the chunk name, the L1
key and the L2 `file_key`.

- [ ] **Step 1: Write the failing tests**
  - `test_extract_audio_chunk_maps_planned_stream`: with `audio_plan` stubbed to stream 2, the
    captured ffmpeg argv contains `["-map", "0:a:2"]` before the output path.
  - `test_extract_audio_chunk_no_map_when_unprobed`: verdict `unprobed` → no `-map` in argv.
  - `test_cache_keys_differ_by_stream_but_stream0_keeps_legacy_key`: `_transcription_key` and
    `_chunk_path` for stream 0 equal today's values; for stream 1 they differ.
  - `test_transcribe_chunk_cached_l2_key_includes_stream`: `transcript_store.get` receives
    `file_key + "#a1"` for stream 1 and the bare `file_key` for stream 0.
  - `test_prewarm_uses_matcher_audio_plan` (prewarm test file): the prewarm's extraction for a file
    planned to stream 1 maps `0:a:1`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `audio_plan`, and thread the planned index through the five methods
  above. `match`/`untagged` give an index; `unprobed` gives none.
- [ ] **Step 4: Run both test files, expect PASS.**
- [ ] **Step 5: Commit** `fix(matcher): transcribe the English audio stream, not ffmpeg's pick (#241)`

Coordination: open PR #798 edits `extract_audio_chunk` and adds a post-failure ffprobe check. If it
has merged, rebase and make its check call `probe_audio_streams` (verdict `no_audio` ⇔ its
`NoAudioStreamError`). If not, note the overlap in this PR's description.

### Task 6: The curator refuses ASR on a foreign verdict

**Files:**
- Modify: `backend/app/matcher/subtitle_utils.py` (beside `REFERENCES_UNREADABLE_ERROR_CODE`)
- Modify: `backend/app/core/curator.py` (`match_single_file` ~333 after the chromaprint block, and
  `_match_across_seasons` before its season loop)
- Test: `backend/tests/unit/test_curator.py`

**Interfaces:**
- Consumes: `EpisodeMatcher.audio_plan` (Task 5), `language_name` (Task 1)
- Produces: `AUDIO_LANGUAGE_ERROR_CODE = "audio_language_unsupported"`;
  `language_refusal_details(plan: AudioPlan) -> dict` returning
  `{"error": AUDIO_LANGUAGE_ERROR_CODE, "audio_languages": plan.languages, "message": ...}`
  (in `subtitle_utils.py`, so the curator and coordinator share it)

Message copy (exact), joining distinct known names with " and ":
`"This track's only audio is {names}, and Engram matches episodes by English dialogue, so it could not be matched automatically. Assign the episode by hand."`

- [ ] **Step 1: Write the failing tests**
  - `test_foreign_audio_returns_language_refusal_without_asr`: matcher `audio_plan` returns
    `foreign` with languages `["ja"]`; `identify_episode` is a mock; result has
    `episode_code is None`, `needs_review is True`, `match_details["error"] == "audio_language_unsupported"`,
    message contains `"only audio is Japanese"`; `identify_episode.assert_not_called()`.
  - `test_chromaprint_hit_wins_over_foreign_audio`: canonical prepass hit at gate → accepted result,
    `audio_plan` not consulted.
  - `test_unprobed_audio_runs_asr_as_today`.
  - `test_across_seasons_refuses_once`: season `None`, foreign → one refusal, no per-season ASR.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Plan via `self._matcher.audio_plan(file_path)` inside
  `asyncio.to_thread` (ffprobe is blocking). `no_audio` is left to #798's handling if present;
  otherwise it returns the same shape with message
  `"This track has no audio, so it could not be matched by its dialogue. Assign the episode by hand."`
- [ ] **Step 4: Run, expect PASS;** run `test_curator*.py`.
- [ ] **Step 5: Commit** `feat(curator): park non-English tracks instead of guessing (#241)`

### Task 7: Coordinator and finalizer treat the refusal like unreadable references

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (`_NON_REMATCHABLE_REVIEW_ERRORS` ~229,
  `_route_unconfirmable_title` ~380, the subtitle-failed LLM fallback ~1206)
- Modify: `backend/app/services/finalization_coordinator.py` (`_refused_for_references` ~35, the
  `has_review` park ~1178)
- Test: `backend/tests/unit/test_matching_coordinator.py`, `backend/tests/unit/test_finalization_coordinator.py`

**Interfaces:**
- Consumes: `AUDIO_LANGUAGE_ERROR_CODE` (Task 6), `DiscTitle.audio_language_list()` (Task 3),
  `probe_audio_streams` (Task 4)
- Produces: `_language_review_summary(titles) -> str | None` in `finalization_coordinator.py`

- [ ] **Step 1: Write the failing tests**
  - `test_language_refusal_is_not_rematchable`: a REVIEW title with that code →
    `_is_rematchable_review` is False; auto-escalation leaves `match_details` intact.
  - `test_disc_had_english_hint_appended`: title `audio_languages='["ja","en"]'`, refusal languages
    `["ja"]` → stored message ends with
    `" The disc also has English audio, but MakeMKV's language settings left it out of the rip."`
  - `test_no_hint_when_disc_languages_unknown`: `audio_languages=None` → message unchanged.
  - `test_llm_fallback_skipped_for_foreign_audio`: subtitle status `failed` and the file plans
    `foreign` → `_llm_fallback_without_subtitles` not awaited, title gets the language refusal.
  - `test_all_foreign_disc_is_not_wrong_show` and `..._not_cross_season_rerun`: every episode
    candidate refused for language → `_detect_wrong_show` returns None and the cross-season
    predicate returns False (Review Focus 1).
  - `test_review_reason_names_language`: 3 REVIEW titles, 2 with the code (`["ja"]`) →
    reason `"3 title(s) need manual episode assignment. 2 have no English audio (Japanese)."`
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Rename `_refused_for_references` to `_refused_by_matcher` covering both
  codes (update its three callers). Add the code to the non-rematchable set. Append the hint where
  the coordinator stores `result.match_details` (before `json.dumps`). In the subtitle-failed branch,
  plan the file first; on `foreign` store the refusal and skip the LLM fallback.
- [ ] **Step 4: Run both files, expect PASS.**
- [ ] **Step 5: Commit** `feat(matching): explain language-parked tracks in review (#241)`

### Task 8: Review page notice

**Files:**
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx:21`
- Test: `frontend/src/components/ReviewQueue/Inspector.test.tsx`

- [ ] **Step 1: Write the failing test** mirroring the `references_unreadable` case at line ~191:
  a title with `match_details: JSON.stringify({ error: 'audio_language_unsupported', message })`
  renders `message`.
- [ ] **Step 2: Run, expect FAIL.** `npm run test:unit -- Inspector`
- [ ] **Step 3: Add the code to `REVIEW_NOTICE_CODES`.**
- [ ] **Step 4: Run, expect PASS;** `npm run lint`, `npm run build`.
- [ ] **Step 5: Commit** `feat(review): show the language notice on parked tracks (#241)`

### Task 9: Docs, changelog, full verification, PR

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Fixed` / `### Added`)
- Modify: `CLAUDE.md` (Key Patterns: one bullet: "the ripped file's audio plan, not the scan, decides
  ASR; TINFO 28 is metadata language")
- Modify: the user-facing review/matching docs page that lists review reasons, if one exists
  (`grep -rl "references_unreadable\|need manual episode" docs/`)

- [ ] **Step 1:** CHANGELOG bullet: tracks whose only audio is another language now go to review
  with a reason naming the language instead of being guessed; English audio is picked explicitly
  on discs with several audio tracks. `(#PR, thanks @wintheriscomming!)`
- [ ] **Step 2:** `uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 3:** `uv run pytest tests/unit -q -p no:cacheprovider` in the background (over 5 min);
  expect all pass (re-run any caplog files without `-p no:logging` if used).
- [ ] **Step 4:** `uv run pytest tests/pipeline -q` → pass (run `init_db()` first if the worktree DB
  is a 0-byte stub).
- [ ] **Step 5:** Push, `gh pr create` ("Refs #241"), then comment `@claude please review this PR`.

---

## Phase 2: match in the show's own language (outline; detailed after phase 1 merges and the open
questions are answered)

### Task 10: Content language on the job
`DiscJob.content_language` (nullable, normalized) from TMDB `original_language` during
identification (TV only); Alembic revision; diagnostics `job-detail.json` carries it.

### Task 11: Match-language decision and context
`DiscJob.match_language`; decided in `MatchingCoordinator.download_subtitles` beside
`decide_job_namespace`; bound in a ContextVar module `app/core/match_language.py` at the same four
binding sites as the episode namespace (`download_subtitles`, `download_subtitles_all_seasons`,
`_run_match_single_file` after the subtitle wait, and the roster/manual-subtitle/LLM routes).
`EpisodeMatcher.audio_plan` reads it as its default language.

### Task 12: References in the match language
Provider language plumbing (OpenSubtitles `languages=`, Addic7ed by name, tvsubtitles only where
`_LANGUAGE_NAMES` has it). `corpus_dir_suffix()` composes namespace and language (`@tvdb@de`).
One `is_local_only_corpus_dir(name)` predicate (`"@" in name`) replaces the three
`endswith("@tvdb")` checks in `scripts/`. Precomputed pack never consulted for non-English.

### Task 13: Whisper language and transcript keys
`transcribe(audio, language=...)` in `asr_models.py` (and the `asr_provider` wrapper); language
joins the L1/L2 transcript keys (English keeps today's keys).

### Task 14: TF-IDF analyzer per script
`char_wb`, `ngram_range=(2, 4)` for `ja`, `zh`, `th`; word analyzer otherwise. Matcher test on
small German and Japanese fixture corpora.

### Task 15: Fallback ladder, provenance, notice rewording
Per-title: match language stream → English stream with English references → refusal naming both
languages. `match_details` records `match_language` and `audio_stream`; Inspector shows the
language beside the source chip. CJK auto-file policy per open question 2. CHANGELOG, PR
"Fixes #241".

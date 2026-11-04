# Non-English content: detect, route, then match in the show's own language

- Issue: Jsakkos/engram#241
- Prior art: @wintheriscomming's patched 0.35.1 (issue comment), German *Babylon Berlin* S1 to S3
  matched at 0.72 to 0.82 confidence in German
- Related: PR #798 (#793, a track ripped with no audio), PR #693 (published subtitle cache stays
  English-only)
- Status: design, awaiting maintainer approval

## Why

Engram matches TV episodes by transcribing 30-second audio chunks with Whisper (forced to English)
and comparing the text with reference subtitles (English). When a track's dialogue is not English,
Whisper produces nonsense, the TF-IDF vote lands on an arbitrary episode, and the job either files a
wrong episode at low confidence or parks in review with a "best guess" that is noise. The user sees
no reason. The time spent transcribing is wasted.

The issue asks for detection plus routing to review. The contributor went further: match in the
show's own language, which turns a manual chore into an automatic match for the shows where
reference subtitles in that language exist. Both are worth building; the first is the floor the
second falls back to, so it ships first.

## What the evidence says (recon, 2026-10-20)

Measured against the 40+ real MakeMKV scan logs in `~/.engram/logs/makemkv/` (read-only):

1. **TINFO attr 28 is not the audio language.** It is MakeMKV's *metadata* language
   (`ap_iaMetadataLanguageCode`). *Killers of the Flower Moon* disc 2, title 2 reports
   `TINFO:2,28,0,"eng"` while its only audio stream is Osage (`SINFO:2,1,3,0,"osa"`). DVDs leave it
   empty. The issue's `primary_language` from attr 28 would be wrong exactly when it matters, so this
   design does not store it.
2. **The audio language is SINFO attr 3** (`ap_iaLangCode`) on streams whose attr 1 is
   `6202,"Audio"` (`6203,"Subtitles"` for subtitles). Real lines have five fields:
   `SINFO:<title>,<stream>,<attr>,<code>,"<value>"`, not the four the issue lists.
3. **Codes are mixed ISO 639-2/B and /T** on the same machine: `fre` and `fra`, `ger` and `deu`.
   DVD audio streams often have **no attr 3 at all** (not `und`, simply absent).
4. **Commentary flags are not set in practice.** SINFO attr 22 (stream flags; bits 1 and 2 are
   director's comments, bit 4 is visually impaired) was clear on every audio stream in every log.
   Commentary detection cannot rely on it; it needs the track name and the MKV disposition as well.
5. **The disc's streams are not the file's streams.** MakeMKV's own language preferences decide
   which audio tracks it writes. #793 is a 47-minute extra whose only audio was Japanese; MakeMKV
   wrote the title without audio. The ripped file, not the scan, is the authority on what can be
   transcribed. Imports (mkv/mp4/m4v/ts/m2ts) have no scan at all.
6. **Engram cannot match from embedded subtitle tracks.** Disc subtitles are PGS/VobSub bitmaps and
   nothing in the pipeline reads them. The issue's row "non-English audio, English subtitle tracks
   present: proceed" does not hold: an English subtitle *track* gives the ASR matcher nothing.
7. **ffmpeg picks the audio stream today** (`extract_audio_chunk` passes no `-map`). Its automatic
   choice favours the stream flagged default, then the one with most channels. MakeMKV flags the
   first track in the user's preferred language as default, which is why English discs work. A dub
   disc where the default flag lands on the original-language track would be transcribed in the
   wrong language even though English audio is present.
8. Whisper is hard-coded `language="en"` in `asr_models.py`; OpenSubtitles, Addic7ed and
   tvsubtitles are called with English; the TF-IDF vectorizer uses a word analyzer, which is fine
   for German but treats an unspaced Japanese sentence as one token.
9. Nothing reads TMDB `original_language` today.

## Goals

- A track whose dialogue cannot be matched is never silently mis-matched: it parks in review with a
  plain reason that names the language.
- A track that has a usable English audio stream is transcribed from that stream, whatever ffmpeg
  would have picked.
- (Phase 2) A non-English show whose reference subtitles exist in its own language matches
  automatically in that language.
- Every decision is visible after the fact: the review page, `match_details`, the job-tagged log
  and the diagnostics bundle say which audio stream was used, in which language, and why.

## Non-goals

- Movies. Movie identification does not transcribe dialogue; nothing changes there.
- OCR of PGS/VobSub subtitle tracks.
- Changing MakeMKV's track selection (`settings.conf`). Engram writes `app_Key` there and nothing
  else; rewriting the user's language preferences is out of bounds. Engram explains instead (see
  "Disc had it, file does not").
- Publishing non-English references to the shared subtitle cache. PR #693 keeps the published pack
  English-only; local non-English references never leave the machine.
- The contributor's two separable matcher changes (summed-cosine chunk-vote ranking; OpenSubtitles
  candidate ordering by trusted uploader and download count). Both are real improvements with their
  own blast radius on English matching and get their own issues.

## Anti-goals (would make this a failure even if it "works")

- Any English disc that matches today matching worse, or less often. Every new check that cannot
  decide (ffprobe missing or failing, untagged streams, unknown codes) falls back to today's
  behaviour.
- A foreign-language bonus feature stalling an English disc. The duration pre-filter already files
  non-episode-length titles as extras before matching; the language gate sits after it, so it only
  ever sees episode-length titles.
- A second, drifting language table. One module owns codes and names.

## Design

### Language codes: one module

`app/core/languages.py` (new):

- `normalize_language(code) -> str | None`. Lower-cases, maps ISO 639-2/B and /T and 639-1 to
  639-1 for the languages a table covers (about 40: the major European, CJK, Indic, Arabic, Hebrew,
  Turkish, Thai, Nordic and Slavic languages). An unmapped three-letter code is kept as-is (`osa`
  stays `osa`). Empty, `und`, `mis`, `mul`, `zxx` and the private-use range `qaa`..`qtz` return
  `None` ("unknown").
- `language_name(code) -> str` for messages ("Japanese"), falling back to the upper-cased code.
- `is_english(code)`.

No new dependency (`langcodes`/`pycountry` would add a frozen-build hidden-import risk for a small
table).

### Scan-time capture (phase 1)

`Extractor._parse_disc_info` parses SINFO lines alongside TINFO. Per title it collects, in stream
order, the normalized language of each audio stream and each subtitle stream (`None` for an
untagged stream, kept as a slot so counts stay honest). `analyst.TitleInfo` gains
`audio_languages: list[str | None]` and `subtitle_languages: list[str | None]`.

`DiscTitle` gains two nullable JSON-text columns, `audio_languages` and `subtitle_languages`.
`None` means "not known" (legacy rows, imports, a scan that had no SINFO). The identification
coordinator copies them when it creates rows. They need an Alembic revision **and** nothing more in
`database.py`, because `_add_missing_columns()` adds nullable model columns for frozen builds.
They reach the frontend through the existing title serialization; `frontend/src/types/index.ts`
gains the optional fields.

These columns describe the *disc*. They are evidence for the review message and the diagnostics,
not the routing authority (finding 5).

### Match-time audio plan (phase 1)

`app/matcher/audio_streams.py` (new):

- `probe_audio_streams(path) -> list[AudioStream] | None`: one `ffprobe -select_streams a` call
  returning per audio stream its audio-relative index, normalized language, track title, channel
  count, and the `default`, `comment`, `visual_impaired`, `hearing_impaired` dispositions. Memoized
  per resolved path, size and mtime. `None` when ffprobe is missing or fails.
- `plan_audio(streams, language) -> AudioPlan` with a `verdict` and an optional stream:
  - `match`: the first non-commentary stream tagged `language` (default flag breaks ties).
  - `untagged`: no stream tagged `language`, but at least one untagged non-commentary stream; use
    the first one (the DVD case, finding 3; matches today's behaviour).
  - `foreign`: every non-commentary stream carries a known language other than `language`.
  - `no_audio`: zero audio streams (the #793 file).
  - `unprobed`: `streams is None`; use no `-map` (exactly today's behaviour).
- Commentary and audio description: disposition `comment` or `visual_impaired`, or a title matching
  a small multilingual pattern (commentary, audio description, described, Kommentar, commentaire,
  comentario, commento). A file whose only `language` streams are commentary counts as `foreign`.

`extract_audio_chunk` takes an optional audio-relative stream index and adds `-map 0:a:<n>` when
given. The index joins the chunk-file name and the in-memory chunk cache key, and the transcript
cache key (otherwise a chunk of the wrong stream cached earlier would be reused). The transcription
prewarm uses the same plan so it never caches the wrong stream.

PR #798 also edits `extract_audio_chunk` and adds an ffprobe audio check after an ffmpeg failure.
Whichever lands second rebases; the `no_audio` verdict and #798's `NoAudioStreamError` should share
one probe (#798's check becomes a call to `probe_audio_streams`).

### Routing (phase 1)

In `EpisodeCurator.match_single_file` (and `_match_across_seasons`, which returns before it), after
the chromaprint prepass has had its chance (a fingerprint hit is language-independent) and before
ASR, the curator plans the file's audio for English:

- `match` / `untagged` / `unprobed`: ASR proceeds on the chosen stream.
- `foreign`: no ASR. Return a review result whose `match_details` is
  `{"error": "audio_language_unsupported", "audio_languages": [...], "message": ...}`. The message
  names the language: "This track's only audio is Japanese, and Engram matches episodes by English
  dialogue, so it could not be matched automatically. Assign the episode by hand."
- `no_audio`: no ASR; handled as #798 does (or, if #798 has not landed, the same review result with
  its own message).

The code joins `_NON_REMATCHABLE_REVIEW_ERRORS` (a deeper re-scan of the same audio cannot change
the language; memory note: a new review code that is missing there gets its `match_details` wiped by
auto-escalation) and the frontend `REVIEW_NOTICE_CODES` in `ReviewQueue/Inspector.tsx`. The
no-subtitles LLM fallback (which also transcribes in English) is skipped for these titles.

The job's `review_reason` gains one summary line when any title was routed for language, e.g.
"3 tracks have no English audio (Japanese)", built the way the unreadable-references summary is.

**Disc had it, file does not.** When the title's scan-time `audio_languages` list a language that
the probed file does not contain, the message adds: "The disc also has English audio, but MakeMKV's
language settings left it out of the rip." That one sentence turns a #793-style mystery into an
actionable fix (MakeMKV Preferences > Language). The curator has no database access, so the
matching coordinator appends this hint when it stores the result, reading the title's
`audio_languages` (designer's call).

ffprobe is invoked the way `get_video_duration` already invokes it (bare `ffprobe` on `PATH`,
beside the ffmpeg Engram already requires).

### Native-language matching (phase 2)

Built on phase 1's plan and codes; follows the contributor's design.

1. **Content language.** Identification records TMDB `original_language` on the job
   (`DiscJob.content_language`, nullable, normalized). Unknown means English.
2. **Match language per job.** If `content_language` is English or unknown, nothing changes. Else
   the job's match language is `content_language` when references in that language can be obtained
   (step 3); otherwise English. Decided once in `MatchingCoordinator.download_subtitles`, next to
   the episode-namespace decision, and stored on the job (`DiscJob.match_language`) so re-matches,
   imports and the review routes read the same value.
3. **References in that language.** Providers take the language: OpenSubtitles `languages=`,
   Addic7ed and tvsubtitles by name (tvsubtitles only supports a few; it is skipped otherwise). They
   download into `<tmdb_id>[@tvdb]@<lang>/`. The published precomputed pack is English and is not
   consulted for a non-English match language. The cache scripts' three `endswith("@tvdb")` skips
   become one shared "local-only dir" predicate (`"@" in name`), so these dirs are never migrated,
   normalized or published.
4. **Per-title audio.** The plan is made for the job's match language. A title with no stream in it
   but an English stream falls back to an English match of that title only if English references
   are also present; otherwise it parks with phase 1's notice, reworded ("...no German or English
   audio...").
5. **Whisper** takes the language (`transcribe(..., language=lang)`). The transcript cache key
   includes it.
6. **TF-IDF.** Word analyzer for spaced scripts. For Japanese, Chinese and Thai, a `char_wb`
   analyzer (2 to 4 grams) is fit on the fly, since no precomputed corpus exists for them anyway.
7. **Confidence.** `calibrate_confidence` was tuned on English word n-grams. German with the word
   analyzer behaved (contributor's 0.72 to 0.82). Character n-grams have a different cosine scale
   with no evidence yet (see open question 2).
8. **Provenance.** `match_details` records `match_language` and the audio stream used; the review
   Inspector shows the language beside the match source chip.

## Phasing

| Phase | Ships | Closes |
|---|---|---|
| 1 | languages module; SINFO capture and two `DiscTitle` columns; ffprobe audio plan with explicit `-map`; `audio_language_unsupported` review routing with job summary and the "MakeMKV left it out" hint; UI notice | Refs #241 |
| 2 | content/match language on the job; references in that language under `@<lang>` dirs; Whisper language; char n-grams for ja/zh/th; provenance | Fixes #241 |

Phase 1 is independently useful and safe: the only new non-ASR outcome is for a file whose every
usable audio stream is tagged with a non-English language.

## Testing

- Parser tests from trimmed real scan-log excerpts (Breaking Bad: English plus French subtitles;
  *Killers of the Flower Moon* title 2: Osage audio with `TINFO 28 = eng`; a DVD title with an
  untagged audio stream; `fre`/`fra`, `ger`/`deu`).
- `plan_audio` table tests for every verdict, commentary by disposition and by name, default
  tie-break.
- `extract_audio_chunk` builds `-map 0:a:<n>`; cache keys differ by stream.
- Curator: a `foreign` file returns the review result without calling ASR; the prepass still runs
  first; `unprobed` keeps today's call exactly.
- Coordinator: the code is non-rematchable and survives auto-escalation; the job summary line.
- Frontend unit test (vitest) for the Inspector notice.
- Phase 2 adds provider language plumbing, dir naming, and a matcher test on a small German and a
  small Japanese corpus.
- Not E2E: simulation bypasses identification and ffprobe, so it cannot represent this.

## Calls made by the designer (check these)

1. TINFO attr 28 is dropped as a signal (finding 1), departing from the issue text.
2. The issue's "English subtitle tracks present: proceed" row is dropped (finding 6).
3. The ripped file (ffprobe) is the routing authority; the scan columns are evidence only.
4. Untagged audio counts as "try it" (the issue's rule), and any probe failure means "today's
   behaviour".
5. No changes to MakeMKV `settings.conf`; explain instead.
6. Phase 2 prefers the original language over an English dub when references exist in both
   (translations match a dub's script poorly; original dialogue against original-language subtitles
   is the faithful pair). Open question 1 lets the maintainer overrule this.
7. Language suffix `@<lang>` (the contributor used `.<lang>`) so one predicate covers `@tvdb` and
   language dirs.

8. (Phase 2, 2026-10-25) The content language is read at the language decision, beside
   `decide_job_namespace`, not at identification: a job's show is set in many places, and every one
   of them passes the subtitle download. Phase 2 ships as three PRs in the order that keeps `main`
   safe at every merge (plan `docs/superpowers/plans/2026-10-25-match-in-show-language.md`).

## Maintainer decisions (2026-10-20)

1. Phase 2, a foreign show whose disc has both the original audio and an English dub: match in the
   **original language**.
2. Phase 2, Japanese/Chinese/Thai (character n-grams, uncalibrated): **always park matches for
   review** until real-disc evidence calibrates them.

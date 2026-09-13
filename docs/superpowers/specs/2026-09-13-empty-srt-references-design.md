# Empty subtitle references and 3-in-1 conjoined tracks

**Status:** approved design, not yet implemented
**Scope:** bug-fix PR 1 of 2. PR 2 (combined `S01E01-E03` filenames) is a separate spec that
builds on the approach in #614; see "Follow-up: PR 2" below.

## Problem

A user ripped a Dexter's Laboratory season 1 DVD (13 tracks, each ~22 min holding three
~7 min TMDB segments). Every track matched `S01E01` with confidence 0.86 to 1.0 and parked in
review with "runtime suggests about 2 episodes joined together, but the audio match could not
confirm it (single_episode)". A Looney Tunes set (also 3-in-1) behaved the same way.

Evidence from the diagnostics bundle (job 28, v0.35.1, Windows 10, frozen build):

- 37 reference SRTs were found for season 1, but the TF-IDF corpus log shows
  `S01E01 (6289 chars)` and **0 chars for all 36 others**.
- Every chunk on every track therefore voted for the only non-empty reference:
  `runs: [{code: S01E01, first_start: 90, last_start: 1215, votes: 10}]`.
- `select_chunk_vote` treats a lone scoring candidate as a clear win (runner-up score 0.0), so
  the confidence calibrated to 1.0 on a wrong answer.
- The subtitle step finished in 5 seconds: the broken files were reused from
  `~/.engram/cache/data/4229`, because `is_valid_srt_file` only checks for `-->` in the header.
- The runtime hint reported ~2 conjoined episodes because `_conjoined_episode_count` returns the
  smallest `n` that fits: 7+7=14 min, with the -5/+10 window, already admits a 22 min track.

## Root causes

### 1. The SRT parser silently drops valid-looking files

`SubtitleReader.extract_subtitle_chunk` (in both `app/matcher/episode_identification.py` and
`app/matcher/srt_utils.py`) splits on `"\n\n"` and requires the timestamp on `lines[1]` of each
block. Reproduced on Windows against the real reader:

| File shape | `is_valid_srt_file` | `get_full_text` chars |
|---|---|---|
| Clean LF | True | 44 |
| CRLF | True | 44 |
| Whitespace-only separator lines | True | 72 |
| `\r\r\n` line doubling | True | **0** |
| CRLF content written through a Windows text-mode write | True | **0** |
| No cue index lines (timestamp is `lines[0]`) | True | **0** |

`\r\r\n` reads back through universal newlines as a blank line after every line, so every block
fragments into sub-3-line pieces and is skipped. No exception is raised, so nothing is logged.

### 2. Manual import produces the doubled shape on Windows

`app/matcher/manual_subtitle_import.py` `commit_files` writes
`dest_path.write_text(f.content, encoding="utf-8")` without `newline=""`. Uploaded text that keeps
CRLF becomes `\r\r\n` on disk on Windows. Addic7ed and TVsubtitles clients use `write_bytes` and
are not affected by this writer bug. The bundle cannot show which route produced this user's files;
the parser fix covers every route regardless.

### 3. Validation and matching accept a degenerate corpus

- `is_valid_srt_file` / `is_valid_srt_content` sniff for `-->` only, so a file that yields zero
  cues is cached and reused forever.
- `TfidfMatcher.prepare` fits on empty strings without complaint, and no floor exists on the number
  of usable references before a match is accepted.

### 4. 3-in-1 tracks cannot be confirmed at default depth

`decompose_vote_runs` rejects with `insufficient_scan_depth` when
`total_scan_points <= MIN_SCAN_POINTS_PER_RUN * len(runs) + 1`. For three runs that is `10 <= 10`
at the default 10 scan points, so a 3-in-1 track is never confirmed even with a healthy corpus.
The module docstring already notes that lattice level 19 supports three runs. #622 shipped scoped
to 2-in-1 content.

## Design

### A. Line-stream SRT parser (one implementation, both readers)

Replace the `"\n\n"` split and the `lines[1]` requirement with a block parser that tolerates the
damaged layouts:

- Normalize `\r\n` and lone `\r` to `\n`, strip a leading BOM, and strip each line.
- Measure the file's intra-cue gap: for each valid timing line followed by text (not a bare
  number, not another timestamp-like `-->` line), count the blank lines between them. Take the
  most common count, ties to the larger (0 normal, 1 doubled, 2 tripled). A run of blank lines
  longer than that gap separates cues. The majority decides so one odd cue, header, or trailer
  cannot flip the whole file; ties go wide because a too-narrow gap empties every cue, while a
  too-wide gap still splits cues on their timing lines and only lets a timing-less stray line join
  the cue before it. (Two earlier rules failed code review: a global "no two adjacent non-blank
  lines" flag collapsed a doubled file that had one single-spaced header or tripled endings, and
  the minimum gap let one single-spaced cue empty a doubled file. A pure "ignore blank lines" parse
  was also rejected: it would merge a trailing timing-less junk block into the last real cue, and
  the watermark filter would then drop that whole cue.)
- Within a block, the first line matching the timing pattern
  (`H:M:S[,.]mmm --> H:M:S[,.]mmm` with 1 to 3 digits per field, because real cached files
  write timings such as `00:00:0,616`, `446:12:46,016`, and `00:100:02,227`; the pre-branch
  parser read their dialogue, and dropping the cue would make a readable file invalid; spaces
  around the arrow optional) opens a cue; lines
  before it (the index, or stray text) are ignored and lines after it are the cue's text. A block
  with no timing line is ignored, as today.
- A further timing line inside the same block (a missing blank separator) opens another cue, and a
  purely numeric line directly before it is dropped as that cue's index.
- A line that starts like a timestamp and contains `-->` but does not match the timing pattern
  drops that cue only. Dialogue that merely contains `-->` is kept as text.

Expose the parse as one function that yields `(start, end, text)` cues. Rebuild both
`extract_subtitle_chunk` implementations and `SubtitleReader.get_duration` on it, so the two
reader copies cannot drift again. The watermark filter (`_is_watermark_block`) keeps its current
call site and semantics.

Known trade-off: only in a block that is missing its blank separator, a dialogue line consisting
of just a number, directly before the next timing line, is taken as that cue's index. It loses one
line of text, never a cue.

Further limitations accepted in code review, each confined to a minority of cues in an unusual
layout:

- In a normally spaced file, a blank line inside a cue's text truncates that cue, and a blank line
  between a cue's timing line and its text empties that cue.
- In a doubled file, a trailer written with single line endings directly after the last cue joins
  that cue's text.
- When most cues in a file are single-spaced, a doubled minority of cues lose their text.
- A gap tie in a normally spaced file resolves wide, so a timing-less junk line between cues can
  join the cue before it.
- In a file without index lines whose gap resolves to 1 or more, a cue whose last line is a bare
  number loses that line.
- A cue with an absurd end time (for example `00:00:02,000 --> 446:12:46,016`, a tvsubtitles defect in
  Malcolm in the Middle S02) keeps that end time. Only per-window text extraction and the reported
  reference duration see it. Neither matters in production: the matcher compares against
  full-episode text, no score reads the reference duration (`MatchCoverage.episode_coverage` is
  unused), and the only per-window reader (`MultiSegmentMatcher`, reached from
  `app/matcher/core/engine.py` and `testing_service.match_episodes`) has no production caller.
  Capping cue spans was considered and rejected for that reason.
- A subtitle whose cues are only bracketed sound effects (Primal: `[fly buzzing]`, `[roars]`) stays
  valid even though the matcher's cleaning strips brackets and it contributes no text. It is a
  genuine subtitle of a dialogue-free show; rejecting it would delete and re-download the identical
  file on every pass, spending provider quota for nothing. The matcher drops it as an empty
  reference instead.

### B. Manual import writes bytes exactly

`commit_files` writes with `newline=""` (equivalently `write_bytes(f.content.encode("utf-8"))`), so
line endings round-trip unchanged on every OS.

### C. Validity means "has cues"

`is_valid_srt_file` and `is_valid_srt_content` keep the existing size, HTML, and `-->` checks, and
additionally require the parser from A to yield at least one cue with non-empty text. Effects:

- Cache reuse sites in `testing_service.py` (existing-file checks) reject a zero-cue file, so the
  next download pass replaces it instead of reusing it.
- The provider scheduler rejects a zero-cue download.
- Manual import rejects a zero-cue upload with "not a valid SRT".

Because the parser from A now reads `\r\r\n` and no-index files correctly, existing cached copies
of those shapes become valid and usable without a re-download. Only files with genuinely no
parseable cues are rejected.

Files that start with a UTF-16 byte-order mark are decoded leniently (`decode_utf16_bom` in
`srt_utils.py`) by the validator and by both `read_file_with_fallback` readers alike. tvsubtitles
appends a single-byte ASCII trailer to some UTF-16 subtitles, leaving an odd byte count; a strict
decode made the matcher fall back to latin-1 and read no dialogue while the validator, decoding
leniently, accepted the file. Code review found 16 real cached references in that state (Malcolm in
the Middle S02, I Dream of Jeannie, MacGyver, Criminal Minds, and others). One shared decoder keeps
the validator's verdict and the matcher's view of a file from diverging again.

A cue carrying only a watermark or ad does not count: validity requires a cue whose raw text is not
a watermark (`has_dialogue_cues`, sharing `is_watermark_block` with the matcher's reader). A scan of
the real cache found stubs the matcher could use none of, such as Malcolm in the Middle S02E08/23/25
(two `Downloaded From www.AllSubs.org` cues) and How I Met Your Mother S08E05 (348 cues repeating a
download URL). Rejecting them deletes the stub so the next download pass can fetch the real file.
The rule deliberately checks raw text, not text after the matcher's cleaning: see the dialogue-free
subtitle limitation below.

### D. Unusable-reference guard in matching

In `TfidfMatcher.prepare`, drop references whose full text is empty, and log one WARNING per
season summarizing `usable/total` with the dropped episode codes (not one line per file).

Add a floor, `MIN_USABLE_REFERENCES = 2`. When fewer usable references remain for the season,
`identify_episode` returns no match with a reason code, and the matching coordinator parks the
title in REVIEW with a message stating the reference subtitles for the season could not be read.
The new error code must be added to `_NON_REMATCHABLE_REVIEW_ERRORS` so review escalation does not
overwrite `match_details` (see the review-escalation wipe bug).

The precomputed-vector path (`load_precomputed`) is unaffected: it has no empty-text concept. The
`_augment_with_downloaded_srts` path must skip empty SRTs the same way.

### E. Confirmable 3-in-1 tracks

- **Scan depth.** When `_conjoined_episode_count` returns a hint (any `n >= 2`), run the ASR scan
  for that title at `snap_to_lattice_level(19)` instead of the default 10. 19 points admit a
  confident verdict for up to three runs (`19 > 3*3+1`) and nest with the 10-point lattice, so the
  first 10 transcripts are reused from the transcript cache.
- **Hint accuracy: dropped during planning.** Picking the closest-total `n` would break
  `test_smallest_n_wins_when_windows_overlap`, which pins a deliberate choice (overlapping windows
  return the smaller `n`; the vote runs correct it). Because the deeper scan above applies to any
  hint `n >= 2`, the hint count no longer affects detection. It only appears in the
  unconfirmed-review message, and a confirmed verdict reports its own run count. The
  smallest-`n` rule stays.

No change to `MIN_SCAN_POINTS_PER_RUN`, the territory rule, or `MAX_CONJOINED_EPISODES`.

## Invariants preserved

- `matched_episode` stays a single canonical aired code.
- Conjoined tracks still never enqueue fingerprint contributions.
- A multi-episode verdict still routes to REVIEW; this PR does not name combined files.

## Testing

Unit (`tests/unit/`):

- Parser: clean LF, CRLF, `\r\r\n`, doubled after a text-mode read, no index lines, whitespace
  separators, BOM, malformed timing line in one cue, missing blank separator, text before the first
  cue, timing-less trailing block. Assert identical cue lists for the clean and damaged variants of
  the same subtitle.
- Parity: `episode_identification.SubtitleReader` and `srt_utils.SubtitleReader` return identical
  results for every fixture without watermark blocks (only the matcher's reader filters them).
- `get_duration` on the damaged variants.
- `is_valid_srt_file` / `is_valid_srt_content`: zero-cue file rejected; `\r\r\n` and no-index files
  accepted.
- Manual import on Windows-style CRLF content: bytes on disk equal the input bytes.
- `TfidfMatcher.prepare` drops empty references and logs the summary.
- Guard: a season with one usable reference yields no match and the new review error code; the code
  is in `_NON_REMATCHABLE_REVIEW_ERRORS`.
- `_conjoined_episode_count`: unchanged; existing tests stay green.
- Scan depth: a hinted title requests 19 points; an unhinted title still requests 10.
- `decompose_vote_runs`: three contiguous balanced runs over 19 points confirm.

Regression fixture: a synthetic three-segment corpus with 36 of 37 references in `\r\r\n` shape,
asserting the pre-fix symptom (all votes to the one clean reference) is gone.

## Out of scope

- Combined filenames and storage of multiple codes per title (PR 2, building on #614).
- Segment-level fingerprint contributions for conjoined tracks.
- TheTVDB as a metadata source.
- Changing `select_chunk_vote`'s lone-candidate behaviour directly; guard D handles the degenerate
  case at the corpus level, where the cause lives.

## Follow-up: PR 2 (combined filenames)

Community PR #614 (@raiju, `split/multi-episode`) implements combined-track naming. It does not
touch any file this PR changes (no matcher or subtitle code), so PR 1 ships independently. PR 2
adopts #614's design rather than the sibling-column idea floated earlier:

- **Representation.** A combined track stores the widened canonical code in `matched_episode`
  (`S01E01-E03` for a contiguous run, `S01E01E03` for a gapped set). Every reader goes through
  one anchored parser, `app/core/episode_codes.py`. This supersedes this spec's
  "matched_episode stays a single canonical aired code" invariant for PR 2 only; the fingerprint
  key stays single-episode because combined tracks never contribute.
- **Taken from #614, credited `(thanks @raiju!)`:** `episode_codes.py` and its tests; the
  organizer range splice after the rendered episode token; the per-part DVD projection with the
  cross-season fallback to aired numbering; the reader sweep (`_same_episode_code` overlap,
  `_normalize_episode_code`, DiscDB ingest and export `"17-18"` form, Discord summary,
  contribution correction, disc contribution queue, history amend picker); the deliberate
  non-parse in `bootstrap_library`.
- **Added on top of #614:**
  - A confirmed `MultiEpisodeVerdict` (from #624) pre-fills the title's assignment with
    `format_episode_code(season, parts)` in playback order, still parked in REVIEW for
    confirmation, and the "cannot name a combined file yet" message is replaced.
  - Collision checks in `TitleList.tsx` and `ReviewQueue.tsx` test each part via
    `episodeParts(selection)`, matching `Inspector.tsx` (open finding from the #614 bot review).
  - One fingerprint-contribution guard for combined tracks instead of the two that a naive
    merge with #624 produces.
  - Rebased onto current main, without #611's already-merged commits.
- **Deferred to separate changes:** #614's searchable episode picker, span control and its
  setting/migration, disc name in the review header, and neighbouring-season assignment.

## User-facing notes

CHANGELOG `[Unreleased]` under Fixed: subtitle files with doubled line breaks or missing cue numbers
were read as empty, which could match every track on a disc to the same episode; cartoon discs with
three segments per track are now recognized as combined.

Workaround before release: deleting `~/.engram/cache/data/<tmdb_id>` forces a fresh download, which
helps only if the source serves well-formed files.

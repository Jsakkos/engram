# Empty subtitle references and 3-in-1 conjoined tracks

**Status:** approved design, not yet implemented
**Scope:** bug-fix PR 1 of 2. PR 2 (combined `S01E01-E02-E03` filenames) is a separate spec.

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

Replace block splitting with a line-oriented parse:

- Iterate lines; blank lines are ignored.
- A line matching the timestamp pattern (`HH:MM:SS[,.]mmm --> HH:MM:SS[,.]mmm`, spaces around the
  arrow optional) opens a new cue.
- A purely numeric line immediately preceding a timestamp line is a cue index and is dropped.
- Any other line appends to the current cue's text. Lines before the first timestamp are ignored.
- A malformed timestamp skips that cue only.

Expose the parse as one function that yields `(start, end, text)` cues. Rebuild both
`extract_subtitle_chunk` implementations and `SubtitleReader.get_duration` on it, so the two
reader copies cannot drift again. The watermark filter (`_is_watermark_block`) keeps its current
call site and semantics.

Known trade-off: a numeric dialogue line directly before a timestamp (e.g. a cue whose last line is
"42") is treated as the next cue's index. Acceptable: it loses one token of text, never a cue.

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
- **Hint accuracy.** `_conjoined_episode_count` picks, among admissible `n` in `2..MAX`, the `n`
  whose best consecutive-runtime total is closest to the track duration (ties go to the smaller
  `n`). For 7-min segments and a 22-min track this yields 3 (|21-22|=1) instead of 2 (|14-22|=8).
  It remains an admission hint; the authoritative count still comes from `decompose_vote_runs`.

No change to `MIN_SCAN_POINTS_PER_RUN`, the territory rule, or `MAX_CONJOINED_EPISODES`.

## Invariants preserved

- `matched_episode` stays a single canonical aired code.
- Conjoined tracks still never enqueue fingerprint contributions.
- A multi-episode verdict still routes to REVIEW; this PR does not name combined files.

## Testing

Unit (`tests/unit/`):

- Parser: clean LF, CRLF, `\r\r\n`, no index lines, whitespace separators, BOM, malformed
  timestamp in one cue, numeric dialogue line, trailing content after last cue. Assert identical
  cue lists for the clean and damaged variants of the same subtitle.
- Parity: `episode_identification.SubtitleReader` and `srt_utils.SubtitleReader` return identical
  results for every fixture.
- `get_duration` on the damaged variants.
- `is_valid_srt_file` / `is_valid_srt_content`: zero-cue file rejected; `\r\r\n` and no-index files
  accepted.
- Manual import on Windows-style CRLF content: bytes on disk equal the input bytes.
- `TfidfMatcher.prepare` drops empty references and logs the summary.
- Guard: a season with one usable reference yields no match and the new review error code; the code
  is in `_NON_REMATCHABLE_REVIEW_ERRORS`.
- `_conjoined_episode_count`: 7-min runtimes and 22-min track returns 3; existing 11-min two-segment
  cases still return 2.
- Scan depth: a hinted title requests 19 points; an unhinted title still requests 10.
- `decompose_vote_runs`: three contiguous balanced runs over 19 points confirm.

Regression fixture: a synthetic three-segment corpus with 36 of 37 references in `\r\r\n` shape,
asserting the pre-fix symptom (all votes to the one clean reference) is gone.

## Out of scope

- Combined filenames and storage of multiple codes per title (PR 2).
- Segment-level fingerprint contributions for conjoined tracks.
- TheTVDB as a metadata source.
- Changing `select_chunk_vote`'s lone-candidate behaviour directly; guard D handles the degenerate
  case at the corpus level, where the cause lives.

## User-facing notes

CHANGELOG `[Unreleased]` under Fixed: subtitle files with doubled line breaks or missing cue numbers
were read as empty, which could match every track on a disc to the same episode; cartoon discs with
three segments per track are now recognized as combined.

Workaround before release: deleting `~/.engram/cache/data/<tmdb_id>` forces a fresh download, which
helps only if the source serves well-formed files.

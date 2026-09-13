# Empty SRT References Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop damaged subtitle references (doubled line endings, missing cue numbers) from silently reading as empty and collapsing every track on a disc onto one episode, and let 3-in-1 conjoined tracks be confirmed.

**Architecture:** One block-based SRT cue parser in `app/matcher/srt_utils.py` replaces the two copies of `"\n\n"`-split parsing. Validity checks require at least one cue with text. `TfidfMatcher.prepare` drops empty references, `identify_episode` refuses to match against fewer than 2 usable references and returns a `references_unreadable` error code, and the matching coordinator routes that code to review ahead of the multi-episode routing. Hinted conjoined tracks scan 19 points instead of 10.

**Tech Stack:** Python 3.11+, pytest, scikit-learn TF-IDF, loguru, FastAPI service layer, uv.

**Spec:** `docs/superpowers/specs/2026-09-13-empty-srt-references-design.md`

---

## Conventions for every task

- Work from `backend/` inside the worktree, on branch `fix/empty-srt-references`.
- Run Python through uv: `uv run pytest ...`, `uv run ruff ...`.
- Commit after each task. End every commit message with the trailer
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` (pass it as a second `-m`).
- Do not run the whole backend unit tier inside a subagent (it takes longer than 5 minutes). Task 10
  lists the targeted files.
- Never use em or en dashes in comments, messages, or the changelog.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/matcher/srt_utils.py` | Modify | Owns SRT cue parsing: `parse_srt_timestamp`, `SrtCue`, `iter_srt_cues`, `has_srt_cues`. Its `SubtitleReader.extract_subtitle_chunk` is rebuilt on the parser. |
| `backend/app/matcher/episode_identification.py` | Modify | Matcher's `SubtitleReader` rebuilt on the parser; `TfidfMatcher.prepare` drops empty references; `MIN_USABLE_REFERENCES` guard in `identify_episode`. |
| `backend/app/matcher/subtitle_utils.py` | Modify | Validity requires cues with text; owns `REFERENCES_UNREADABLE_ERROR_CODE`. |
| `backend/app/matcher/manual_subtitle_import.py` | Modify | Write uploads with `newline=""`. |
| `backend/app/services/matching_coordinator.py` | Modify | `_title_details`, `_apply_unreadable_references_review`, `_route_unconfirmable_title`, `CONJOINED_SCAN_POINTS`, `_scan_points_for_hint`. |
| `backend/app/services/finalization_coordinator.py` | Modify | Add the new code to `_NON_REMATCHABLE_REVIEW_ERRORS`. |
| `backend/tests/unit/test_srt_utils.py` | Modify | Parser tests. |
| `backend/tests/unit/test_episode_identification.py` | Modify | Reader parity, damaged corpus regression, empty-reference and guard tests. |
| `backend/tests/unit/test_subtitle_utils.py` | Modify | Validity tests. |
| `backend/tests/unit/test_manual_subtitle_import.py` | Modify | CRLF byte round-trip test. |
| `backend/tests/unit/test_matching_coordinator.py` | Modify | Review routing and scan depth tests. |
| `CHANGELOG.md` | Modify | Two `[Unreleased]` Fixed entries. |

## Known limitation (not addressed here)

Subtitle content is cached in process: `read_file_with_fallback` (`lru_cache(maxsize=100)`),
`SubtitleCache.subtitles`, and the per-signature TF-IDF matcher cache. A reference repaired at the
same path during one process lifetime is only seen after a restart. This is unchanged behaviour.

---

### Task 1: Shared SRT cue parser

**Files:**
- Modify: `backend/app/matcher/srt_utils.py` (imports at top; new code between `read_file_with_fallback` and `class SubtitleReader`)
- Test: `backend/tests/unit/test_srt_utils.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/test_srt_utils.py`, replace the import block at the top:

```python
"""Unit tests for srt_utils text-cleaning helpers."""

import pytest

from app.matcher.srt_utils import clean_text
```

with:

```python
"""Unit tests for srt_utils text-cleaning helpers and the SRT cue parser."""

import re

import pytest

from app.matcher.srt_utils import clean_text, has_srt_cues, iter_srt_cues, parse_srt_timestamp
```

Append to the end of the file:

```python
_CLEAN_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nDexter, get out of my lab!\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nOmelette du fromage.\nOmelette du fromage.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nDee Dee!\n"
)
_EXPECTED_CUES = [
    (1.0, 3.0, ("Dexter, get out of my lab!",)),
    (4.0, 6.5, ("Omelette du fromage.", "Omelette du fromage.")),
    (7.0, 9.0, ("Dee Dee!",)),
]
_NO_INDEX_SRT = re.sub(r"(?m)^\d+\n(?=\d{2}:)", "", _CLEAN_SRT)
_TIMING_WITHOUT_TEXT = (
    "1\n00:00:01,000 --> 00:00:02,000\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n"
)


def _cue_tuples(content):
    return [(cue.start, cue.end, cue.lines) for cue in iter_srt_cues(content)]


@pytest.mark.unit
class TestIterSrtCues:
    @pytest.mark.parametrize(
        "variant",
        [
            pytest.param(_CLEAN_SRT, id="lf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r\n"), id="crlf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r\r\n"), id="cr-cr-lf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\n\n"), id="doubled-after-text-read"),
            pytest.param(_CLEAN_SRT.replace("\n\n", "\n \n"), id="whitespace-separators"),
            pytest.param("﻿" + _CLEAN_SRT, id="bom"),
            pytest.param(_NO_INDEX_SRT, id="no-index-lines"),
        ],
    )
    def test_damaged_layouts_yield_the_clean_cues(self, variant):
        assert _cue_tuples(variant) == _EXPECTED_CUES

    def test_malformed_timing_line_drops_only_that_cue(self):
        content = _CLEAN_SRT.replace("00:00:04,000 --> 00:00:06,500", "00:00:04 -> bad -->")
        assert _cue_tuples(content) == [_EXPECTED_CUES[0], _EXPECTED_CUES[2]]

    def test_missing_blank_separator_splits_on_the_next_timing_line(self):
        content = (
            "1\n00:00:01,000 --> 00:00:03,000\nFirst line\n"
            "2\n00:00:04,000 --> 00:00:05,000\nSecond line\n"
        )
        assert _cue_tuples(content) == [
            (1.0, 3.0, ("First line",)),
            (4.0, 5.0, ("Second line",)),
        ]

    def test_text_before_the_first_timing_line_is_ignored(self):
        assert _cue_tuples("Subtitles by someone\n\n" + _CLEAN_SRT) == _EXPECTED_CUES

    def test_trailing_block_without_timing_is_ignored(self):
        assert _cue_tuples(_CLEAN_SRT + "\nDownloaded from somewhere\n") == _EXPECTED_CUES

    def test_dot_millisecond_separator(self):
        assert _cue_tuples("00:00:01.250 --> 00:00:02.500\nHi\n") == [(1.25, 2.5, ("Hi",))]

    def test_empty_and_none_content(self):
        assert _cue_tuples("") == []
        assert _cue_tuples(None) == []


@pytest.mark.unit
class TestHasSrtCues:
    def test_true_for_dialogue(self):
        assert has_srt_cues(_CLEAN_SRT) is True

    def test_true_for_doubled_line_endings(self):
        assert has_srt_cues(_CLEAN_SRT.replace("\n", "\r\r\n")) is True

    def test_false_for_timing_without_text(self):
        assert has_srt_cues(_TIMING_WITHOUT_TEXT) is False

    def test_false_for_plain_text(self):
        assert has_srt_cues("just some notes with no timing") is False


@pytest.mark.unit
def test_parse_srt_timestamp_accepts_comma_and_dot():
    assert parse_srt_timestamp("01:02:03,500") == pytest.approx(3723.5)
    assert parse_srt_timestamp(" 00:00:07.25 ") == pytest.approx(7.25)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_srt_utils.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'has_srt_cues' from 'app.matcher.srt_utils'`.

- [ ] **Step 3: Implement the parser**

In `backend/app/matcher/srt_utils.py`, replace the import block:

```python
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
```

with:

```python
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
```

Then insert this block directly above `class SubtitleReader:`:

```python
_STAMP = r"\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?"
_TIMING_LINE_RE = re.compile(rf"^({_STAMP})\s*-->\s*({_STAMP})")


def parse_srt_timestamp(timestamp: str) -> float:
    """Parse ``HH:MM:SS,mmm`` (comma or dot before the milliseconds) into seconds."""
    hours, minutes, seconds = timestamp.strip().replace(",", ".").split(":")
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


@dataclass(frozen=True)
class SrtCue:
    """One subtitle cue: its timing and its non-blank text lines."""

    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Group stripped lines into cue blocks separated by blank lines.

    A real SRT always has at least two adjacent non-blank lines (a timing line sits
    next to its index or its text). A file with none has had its line endings
    doubled, for example CRLF text written through a Windows text-mode write, which
    reads back with a blank line after every line. In that layout one blank line is
    an ordinary line break and a run of two or more separates cues.
    """
    doubled = not any(a and b for a, b in zip(lines, lines[1:], strict=False))
    min_gap = 2 if doubled else 1
    blocks: list[list[str]] = []
    current: list[str] = []
    gap = 0
    for line in lines:
        if not line:
            gap += 1
            continue
        if current and gap >= min_gap:
            blocks.append(current)
            current = []
        gap = 0
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def iter_srt_cues(content: str | None) -> Iterator[SrtCue]:
    """Yield every cue in SRT text, tolerating the layouts real downloads arrive in.

    Handles LF, CRLF, lone CR, doubled line endings, a leading BOM, missing cue
    index lines, and whitespace-only separator lines. Within a block, lines before
    the first timing line are ignored (the index, or stray text) and lines after it
    are the cue's text. A block with no timing line is ignored. A further timing line
    inside the same block (a missing blank separator) starts another cue, and a
    purely numeric line directly before it is dropped as that cue's index. A line
    containing ``-->`` that is not a valid timing line drops that cue only.
    """
    if not content:
        return
    normalized = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]
    for block in _split_blocks(lines):
        timing: tuple[float, float] | None = None
        body: list[str] = []
        for line in block:
            match = _TIMING_LINE_RE.match(line)
            if match is None and "-->" not in line:
                if timing is not None:
                    body.append(line)
                continue
            if timing is not None:
                if body and body[-1].isdigit():
                    body.pop()  # the index line of the cue that starts here
                yield SrtCue(timing[0], timing[1], tuple(body))
            timing, body = None, []
            if match is not None:
                try:
                    timing = (
                        parse_srt_timestamp(match.group(1)),
                        parse_srt_timestamp(match.group(2)),
                    )
                except ValueError:
                    timing = None
        if timing is not None:
            yield SrtCue(timing[0], timing[1], tuple(body))


def has_srt_cues(content: str | None) -> bool:
    """True when at least one cue in ``content`` carries text."""
    return any(cue.lines for cue in iter_srt_cues(content))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_srt_utils.py -v`
Expected: all tests PASS (existing `TestCleanText` included).

- [ ] **Step 5: Lint**

Run: `uv run ruff check app/matcher/srt_utils.py tests/unit/test_srt_utils.py && uv run ruff format app/matcher/srt_utils.py tests/unit/test_srt_utils.py`
Expected: `All checks passed!` and files formatted.

- [ ] **Step 6: Commit**

```bash
git add app/matcher/srt_utils.py tests/unit/test_srt_utils.py
git commit -m "fix(subtitles): add a block SRT cue parser that survives doubled line endings" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Rebuild both SubtitleReaders on the parser

**Files:**
- Modify: `backend/app/matcher/srt_utils.py` (`SubtitleReader.extract_subtitle_chunk`)
- Modify: `backend/app/matcher/episode_identification.py` (imports; `SubtitleReader.extract_subtitle_chunk` and `SubtitleReader.get_duration`, near line 2283)
- Test: `backend/tests/unit/test_episode_identification.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/test_episode_identification.py`, add `import re` to the stdlib imports
(after `import json`), and add `from app.matcher import srt_utils` directly below
`from app.matcher import episode_identification as ei`.

Append to the end of the file:

```python
_DEXTER_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nDexter, get out of my lab!\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nOmelette du fromage.\nOmelette du fromage.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nDee Dee!\n"
)
_DEXTER_LINES = [
    "Dexter, get out of my lab!",
    "Omelette du fromage. Omelette du fromage.",
    "Dee Dee!",
]
_DAMAGED_VARIANTS = [
    pytest.param(_DEXTER_SRT, id="clean"),
    pytest.param(_DEXTER_SRT.replace("\n", "\n\n"), id="doubled"),
    pytest.param(re.sub(r"(?m)^\d+\n(?=\d{2}:)", "", _DEXTER_SRT), id="no-index"),
]


@pytest.mark.unit
class TestSubtitleReaderDamagedLayouts:
    @pytest.mark.parametrize("content", _DAMAGED_VARIANTS)
    def test_extract_reads_every_cue(self, content):
        assert SubtitleReader.extract_subtitle_chunk(content, 0, 999) == _DEXTER_LINES

    @pytest.mark.parametrize("content", _DAMAGED_VARIANTS)
    def test_get_duration_reads_the_last_cue(self, content):
        assert SubtitleReader.get_duration(content) == pytest.approx(9.0)

    @pytest.mark.parametrize("content", _DAMAGED_VARIANTS)
    def test_both_readers_agree(self, content):
        assert SubtitleReader.extract_subtitle_chunk(
            content, 0, 999
        ) == srt_utils.SubtitleReader.extract_subtitle_chunk(content, 0, 999)

    def test_full_text_of_doubled_file_is_not_empty(self):
        cache = SubtitleCache()
        cache.subtitles = {"ep.srt": _DEXTER_SRT.replace("\n", "\n\n")}
        assert cache.get_full_text("ep.srt").startswith("dexter get out of my lab")


@pytest.mark.unit
class TestDamagedReferenceCorpus:
    """Regression for the Dexter's Laboratory report: 36 of 37 references read as
    empty, so every chunk of every track voted for the one readable episode."""

    @staticmethod
    def _srt(token: str) -> str:
        body = (token + " ") * 20 + "the and a of to it"
        return "".join(
            f"{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n{body}\n\n"
            for i in range(1, 4)
        )

    def test_damaged_references_still_win_their_own_dialogue(self, tmp_path):
        clean = tmp_path / "Show - S01E01.srt"
        doubled = tmp_path / "Show - S01E02.srt"
        no_index = tmp_path / "Show - S01E03.srt"
        clean.write_bytes(self._srt("alpha").encode("utf-8"))
        doubled.write_bytes(self._srt("bravo").replace("\n", "\r\r\n").encode("utf-8"))
        no_index.write_bytes(
            re.sub(r"(?m)^\d+\n(?=\d{2}:)", "", self._srt("charlie")).encode("utf-8")
        )

        matcher = TfidfMatcher()
        matcher.prepare([clean, doubled, no_index], SubtitleCache())

        assert matcher.match("bravo " * 20)[0][0] == str(doubled)
        assert matcher.match("charlie " * 20)[0][0] == str(no_index)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_episode_identification.py -k "DamagedLayouts or DamagedReferenceCorpus" -v`
Expected: the `clean` cases PASS; the `doubled` and `no-index` cases FAIL (`[] == [...]` and
`0.0 == 9.0 ± ...`); `test_full_text_of_doubled_file_is_not_empty` FAILS; both
`TestDamagedReferenceCorpus` assertions FAIL because the top match is the clean `S01E01` file.

- [ ] **Step 3: Rebuild `srt_utils.SubtitleReader.extract_subtitle_chunk`**

In `backend/app/matcher/srt_utils.py`, replace the whole `extract_subtitle_chunk` method of
`SubtitleReader` with:

```python
    @staticmethod
    def extract_subtitle_chunk(content: str, start_time: float, end_time: float) -> list[str]:
        """Extract subtitle text for a specific time window."""
        return [
            cue.text
            for cue in iter_srt_cues(content)
            if cue.lines and cue.end >= start_time and cue.start <= end_time
        ]
```

- [ ] **Step 4: Rebuild the matcher's `SubtitleReader`**

In `backend/app/matcher/episode_identification.py`, add this import directly below
`from app.matcher.multi_episode import decompose_vote_runs`:

```python
from app.matcher.srt_utils import iter_srt_cues
```

Replace the whole `extract_subtitle_chunk` method of `SubtitleReader` (the one that loops over
`content.strip().split("\n\n")` and calls `_is_watermark_block`) with:

```python
    @staticmethod
    def extract_subtitle_chunk(content, start_time, end_time):
        """
        Extract subtitle text for a specific time window.

        Args:
            content (str): Full SRT file content
            start_time (float): Chunk start time in seconds
            end_time (float): Chunk end time in seconds

        Returns:
            list: List of subtitle texts within the time window
        """
        text_lines = []
        for cue in iter_srt_cues(content):
            if not cue.lines or cue.end < start_time or cue.start > end_time:
                continue
            text = cue.text
            # Skip watermark/ad blocks (URLs, credit lines, etc.)
            if _is_watermark_block(text, list(cue.lines), cue.start):
                logger.debug(f"Filtered watermark/ad block at {cue.start:.1f}s: {text[:80]}")
                continue
            text_lines.append(text)
        return text_lines
```

Replace the whole `get_duration` method of `SubtitleReader` with:

```python
    @staticmethod
    def get_duration(content):
        """
        Get the duration of the subtitle file (max end timestamp across all cues).

        Uses max() instead of the last cue because some subtitle files have
        watermark/ad blocks appended at the end with timestamps near 0:00,
        which would incorrectly report the duration as ~2 seconds.

        Args:
            content (str): Full SRT file content

        Returns:
            float: Duration in seconds, or 0 if parsing fails
        """
        try:
            return max((cue.end for cue in iter_srt_cues(content)), default=0.0)
        except Exception as e:
            logger.warning(f"Error getting duration from subtitle content: {e}")
            return 0.0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_episode_identification.py tests/unit/test_srt_utils.py tests/unit/test_matcher_concurrency.py tests/unit/test_precomputed_augmentation.py -v`
Expected: all PASS, including the existing `TestSubtitleReader` and `TestSubtitleCacheFullText`.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/matcher tests/unit/test_episode_identification.py
uv run ruff format app/matcher/srt_utils.py app/matcher/episode_identification.py tests/unit/test_episode_identification.py
git add app/matcher/srt_utils.py app/matcher/episode_identification.py tests/unit/test_episode_identification.py
git commit -m "fix(matching): read damaged reference subtitles instead of treating them as empty" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: A subtitle is valid only if a cue carries text

**Files:**
- Modify: `backend/app/matcher/subtitle_utils.py` (import line 7; `is_valid_srt_file` body; `is_valid_srt_content`)
- Test: `backend/tests/unit/test_subtitle_utils.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/test_subtitle_utils.py`, add these methods to `class TestIsValidSrtFile`:

```python
    def test_rejects_timing_lines_with_no_dialogue(self, tmp_path):
        p = tmp_path / "blank.srt"
        p.write_bytes(_TIMING_WITHOUT_TEXT.encode("utf-8"))
        assert is_valid_srt_file(p) is False

    def test_accepts_doubled_line_endings(self, tmp_path):
        p = tmp_path / "doubled.srt"
        clean = _SRT_TEXT.replace("\r\n", "\n")
        p.write_bytes(clean.replace("\n", "\r\r\n").encode("utf-8"))
        assert is_valid_srt_file(p) is True

    def test_accepts_cues_without_index_lines(self, tmp_path):
        p = tmp_path / "noindex.srt"
        p.write_bytes(
            b"00:00:01,000 --> 00:00:02,000\nHello there\n\n"
            b"00:00:03,000 --> 00:00:04,000\nGeneral Kenobi\n"
        )
        assert is_valid_srt_file(p) is True
```

Add this constant directly below `_SRT_TEXT = ...` near the top of the file:

```python
_TIMING_WITHOUT_TEXT = (
    "1\n00:00:01,000 --> 00:00:02,000\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\n"
)
```

Append to the end of the file:

```python
@pytest.mark.unit
class TestIsValidSrtContentCues:
    def test_rejects_timing_without_text(self):
        assert is_valid_srt_content(_TIMING_WITHOUT_TEXT) is False

    def test_accepts_doubled_line_endings(self):
        content = "1\n\n00:00:01,000 --> 00:00:02,000\n\nHello there, General Kenobi\n\n"
        assert is_valid_srt_content(content) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_subtitle_utils.py -v`
Expected: `test_rejects_timing_lines_with_no_dialogue` and `test_rejects_timing_without_text` FAIL
(`True is False`); the accept tests PASS already.

- [ ] **Step 3: Implement**

In `backend/app/matcher/subtitle_utils.py`, change line 7:

```python
from app.matcher.srt_utils import SubtitleReader, clean_text
```

to:

```python
from app.matcher.srt_utils import SubtitleReader, clean_text, has_srt_cues
```

In `is_valid_srt_file`, replace everything from the comment line `# Decode by BOM. TVsubtitles (and others) sometimes serve`
through the `return True` that ends the `try` block with:

```python
        # Decode by BOM. TVsubtitles (and others) sometimes serve UTF-16-encoded
        # SRTs; read as UTF-8 those keep a NUL between every character, so the
        # ASCII "-->" check below never matches and a perfectly valid subtitle
        # gets rejected. The whole file is decoded because the cue check needs it.
        raw = file_path.read_bytes()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16", errors="ignore")
        else:
            text = raw.decode("utf-8", errors="ignore")

        if not _looks_like_srt(text[:1000]):
            logger.warning(
                f"Rejecting {file_path.name}: not a valid SRT (HTML or no timestamp markers)"
            )
            return False

        # Timing arrows alone are not a subtitle. A file whose cues hold no text
        # (or whose layout nothing can parse) gives the matcher nothing, and
        # accepting it caches it for good: every later download pass reuses it.
        if not has_srt_cues(text):
            logger.warning(f"Rejecting {file_path.name}: no subtitle cue carries any text")
            return False

        return True
```

Replace the body of `is_valid_srt_content` (keep its docstring) so the function ends:

```python
    if len(content.encode("utf-8")) < 50:
        return False
    return _looks_like_srt(content[:1000]) and has_srt_cues(content)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_subtitle_utils.py tests/unit/test_manual_subtitle_import.py tests/unit/test_provider_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/matcher/subtitle_utils.py tests/unit/test_subtitle_utils.py
uv run ruff format app/matcher/subtitle_utils.py tests/unit/test_subtitle_utils.py
git add app/matcher/subtitle_utils.py tests/unit/test_subtitle_utils.py
git commit -m "fix(subtitles): reject cached subtitles with no readable cues so they re-download" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Manual import writes uploads byte for byte

**Files:**
- Modify: `backend/app/matcher/manual_subtitle_import.py:212`
- Test: `backend/tests/unit/test_manual_subtitle_import.py`

- [ ] **Step 1: Write the failing test**

Add to `class TestCommitFiles` in `backend/tests/unit/test_manual_subtitle_import.py`:

```python
    def test_crlf_upload_is_written_byte_for_byte(self, tmp_path):
        crlf = VALID_SRT.replace("\n", "\r\n")
        files = [CommitInputFile(filename="x.srt", season=1, episode=5, content=crlf)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "imported"
        dest = tmp_path / "data" / "123" / "Show Name - S01E05.srt"
        assert dest.read_bytes() == crlf.encode("utf-8")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_manual_subtitle_import.py::TestCommitFiles::test_crlf_upload_is_written_byte_for_byte -v`
Expected on Windows: FAIL, the bytes on disk contain `\r\r\n`. On Linux and macOS this test
passes before the fix (no newline translation there); the Windows CI unit job is what guards it.

- [ ] **Step 3: Implement**

In `backend/app/matcher/manual_subtitle_import.py`, replace:

```python
            dest_path.write_text(f.content, encoding="utf-8")
```

with:

```python
            # newline="" writes the text exactly as uploaded. Without it Windows
            # translates every "\n" to "\r\n", so an upload already using CRLF lands
            # on disk as "\r\r\n": a blank line after every line.
            dest_path.write_text(f.content, encoding="utf-8", newline="")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_manual_subtitle_import.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/matcher/manual_subtitle_import.py tests/unit/test_manual_subtitle_import.py
git commit -m "fix(subtitles): keep line endings intact when importing subtitles on Windows" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: TfidfMatcher drops references with no text

**Files:**
- Modify: `backend/app/matcher/episode_identification.py` (`TfidfMatcher.__init__`, `load_precomputed`, `prepare`, `match`)
- Test: `backend/tests/unit/test_episode_identification.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_episode_identification.py`:

```python
@pytest.mark.unit
class TestTfidfMatcherEmptyReferences:
    def test_prepare_skips_references_with_no_text(self):
        cache = SubtitleCache()
        cache._full_text_cache = {
            "ep1": "the quick brown fox jumps",
            "ep2": "",
            "ep3": "a slow green turtle swims",
        }
        matcher = TfidfMatcher()
        matcher.prepare(["ep1", "ep2", "ep3"], cache)

        assert matcher.ref_file_order == ["ep1", "ep3"]
        assert matcher.total_references == 3
        assert [ref for ref, _ in matcher.match("slow green turtle")] == ["ep3", "ep1"]

    def test_prepare_with_no_usable_text_does_not_raise(self):
        cache = SubtitleCache()
        cache._full_text_cache = {"ep1": "", "ep2": ""}
        matcher = TfidfMatcher()
        matcher.prepare(["ep1", "ep2"], cache)

        assert matcher.is_prepared is True
        assert matcher.ref_file_order == []
        assert matcher.total_references == 2
        assert matcher.match("anything") == []

    def test_load_precomputed_counts_every_reference(self):
        matcher = TfidfMatcher()
        matcher.load_precomputed(csr_matrix(np.eye(2)), ["S01E01", "S01E02"], np.ones(2))
        assert matcher.total_references == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_episode_identification.py::TestTfidfMatcherEmptyReferences -v`
Expected: FAIL. `ref_file_order` still includes `ep2`, `total_references` does not exist, and the
all-empty case raises `ValueError: empty vocabulary`.

- [ ] **Step 3: Implement**

In `TfidfMatcher.__init__`, add after `self._idf = None  # global IDF array, only set in precomputed mode`:

```python
        self.total_references = 0  # references offered, including any dropped as empty
```

In `load_precomputed`, add after `self.ref_file_order = list(ref_episode_codes)`:

```python
        self.total_references = len(self.ref_file_order)
```

Replace the whole `prepare` method with:

```python
    def prepare(self, reference_files, subtitle_cache: SubtitleCache):
        """
        Fit TF-IDF vectorizer on all reference episode full texts.

        References with no readable text are left out of the corpus: a TF-IDF row
        of zeros can never win a vote, and a corpus that is mostly zeros quietly
        hands every vote to the few references that do have text.
        ``total_references`` still counts them, so callers can tell how much of the
        season was unusable.

        Args:
            reference_files: List of paths to reference SRT files
            subtitle_cache: SubtitleCache instance for loading/caching SRT content
        """
        self.ref_file_order = []
        corpus = []
        dropped = []
        for rf in (str(r) for r in reference_files):
            full_text = subtitle_cache.get_full_text(rf)
            logger.debug(f"  TF-IDF ref: {Path(rf).stem} ({len(full_text)} chars)")
            if not full_text:
                dropped.append(Path(rf).stem)
                continue
            self.ref_file_order.append(rf)
            corpus.append(full_text)
        self.total_references = len(self.ref_file_order) + len(dropped)
        if dropped:
            logger.warning(
                f"TF-IDF: {len(self.ref_file_order)}/{self.total_references} reference "
                f"subtitles have readable text; skipped {len(dropped)} empty: "
                f"{', '.join(dropped)}"
            )

        self.vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            max_features=10000,
            sublinear_tf=True,
        )
        if corpus:
            self.ref_matrix = self.vectorizer.fit_transform(corpus)
            features = self.ref_matrix.shape[1]
        else:
            self.ref_matrix = None
            features = 0
        self._prepared = True
        logger.info(f"TF-IDF prepared: {len(self.ref_file_order)} references, {features} features")
```

In `match`, add directly after the `if not self._prepared: raise RuntimeError(...)` lines:

```python
        if not self.ref_file_order:
            return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_episode_identification.py tests/unit/test_matcher_concurrency.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/matcher/episode_identification.py tests/unit/test_episode_identification.py
uv run ruff format app/matcher/episode_identification.py tests/unit/test_episode_identification.py
git add app/matcher/episode_identification.py tests/unit/test_episode_identification.py
git commit -m "fix(matching): leave reference subtitles with no text out of the TF-IDF corpus" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Refuse to match against fewer than 2 usable references

**Files:**
- Modify: `backend/app/matcher/subtitle_utils.py` (new constant below `_HTML_MARKERS`)
- Modify: `backend/app/matcher/episode_identification.py` (import; `MIN_USABLE_REFERENCES` next to `CHUNK_VOTE_MARGIN_RATIO`; guard after `tfidf_matcher = self._get_tfidf_matcher(...)` in `identify_episode`)
- Test: `backend/tests/unit/test_episode_identification.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_episode_identification.py`:

```python
@pytest.mark.unit
class TestUnreadableReferenceGuard:
    _READABLE = "1\n00:00:01,000 --> 00:00:02,000\nDexter, get out of my lab!\n"
    _TEXTLESS = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\n"

    def test_one_usable_reference_is_not_matched_against(self, tmp_path, monkeypatch):
        from app.matcher.episode_identification import EpisodeMatcher
        from app.matcher.subtitle_utils import REFERENCES_UNREADABLE_ERROR_CODE

        data = tmp_path / "data" / "4229"
        data.mkdir(parents=True)
        (data / "Show - S01E01.srt").write_text(self._READABLE, encoding="utf-8")
        (data / "Show - S01E02.srt").write_text(self._TEXTLESS, encoding="utf-8")
        (data / "Show - S01E03.srt").write_text(self._TEXTLESS, encoding="utf-8")

        matcher = EpisodeMatcher(tmp_path, "Show", expected_tmdb_id=4229, model_name="small")
        monkeypatch.setattr(matcher, "_load_precomputed_season", lambda season: None)
        monkeypatch.setattr(
            "app.matcher.episode_identification.get_video_duration", lambda *a, **k: 1320.0
        )
        monkeypatch.setattr(
            matcher, "extract_audio_chunk", lambda video_file, start_time, duration=None: "chunk"
        )
        transcribe = MagicMock(return_value={"text": "dexter get out of my lab " * 5})
        monkeypatch.setattr(
            "app.matcher.episode_identification.get_cached_model",
            lambda cfg: MagicMock(transcribe=transcribe),
        )
        full_file = MagicMock(return_value=None)
        monkeypatch.setattr(matcher, "_match_full_file", full_file)

        result = matcher.identify_episode(tmp_path / "title_01.mkv", tmp_path, 1)

        assert result is not None
        assert result["episode"] is None
        assert result["match_details"] == {
            "error": REFERENCES_UNREADABLE_ERROR_CODE,
            "usable_references": 1,
            "total_references": 3,
        }
        transcribe.assert_not_called()
        full_file.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_episode_identification.py::TestUnreadableReferenceGuard -v`
Expected: FAIL with `ImportError: cannot import name 'REFERENCES_UNREADABLE_ERROR_CODE'`.

- [ ] **Step 3: Add the error code**

In `backend/app/matcher/subtitle_utils.py`, insert directly below the
`_HTML_MARKERS = (...)` line:

```python

# match_details["error"] when a season's scraped reference subtitles hold too little
# readable text to match against. Produced by EpisodeMatcher.identify_episode,
# routed to review by the matching coordinator, and never auto re-matched: a deeper
# scan against the same unreadable corpus cannot help.
REFERENCES_UNREADABLE_ERROR_CODE = "references_unreadable"
```

- [ ] **Step 4: Add the floor and the guard**

In `backend/app/matcher/episode_identification.py`, change:

```python
from app.matcher.subtitle_utils import corpus_dir_name, sanitize_filename
```

to:

```python
from app.matcher.subtitle_utils import (
    REFERENCES_UNREADABLE_ERROR_CODE,
    corpus_dir_name,
    sanitize_filename,
)
```

Insert directly below the line `CHUNK_VOTE_MARGIN_RATIO = 1.8  # top-1 must lead the runner-up by this ratio to vote`:

```python

# A scraped reference corpus needs at least this many subtitles with readable text
# before a match against it means anything. With one usable reference every chunk
# votes for it (a lone candidate always clears the margin rule in
# select_chunk_vote), so every track on a disc matches that one episode at full
# confidence. Two is the smallest corpus in which a vote can be lost.
MIN_USABLE_REFERENCES = 2
```

In `identify_episode`, find the call that ends:

```python
                reference_files=reference_files,
            )
```

(the closing of `tfidf_matcher = self._get_tfidf_matcher(`). Insert directly after it, before the
`span = f"{scan_points[0]}s-{scan_points[-1]}s" if scan_points else "empty"` line:

```python

            if not using_precomputed and len(tfidf_matcher.ref_file_order) < MIN_USABLE_REFERENCES:
                usable = len(tfidf_matcher.ref_file_order)
                logger.error(
                    f"Only {usable} of {len(reference_files)} reference subtitles for "
                    f"'{self.show_name}' season {season_number} contain readable text; "
                    f"not matching {Path(video_file).name} against them."
                )
                return {
                    "season": season_number,
                    "episode": None,
                    "confidence": 0.0,
                    "score": 0.0,
                    "match_details": {
                        "error": REFERENCES_UNREADABLE_ERROR_CODE,
                        "usable_references": usable,
                        "total_references": len(reference_files),
                    },
                    "runner_ups": [],
                }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_episode_identification.py tests/unit/test_matcher_concurrency.py tests/unit/test_subtitle_utils.py -v`
Expected: all PASS. `test_matcher_concurrency` writes two readable SRTs per season, which meets the floor.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/matcher tests/unit/test_episode_identification.py
uv run ruff format app/matcher/episode_identification.py app/matcher/subtitle_utils.py tests/unit/test_episode_identification.py
git add app/matcher/episode_identification.py app/matcher/subtitle_utils.py tests/unit/test_episode_identification.py
git commit -m "fix(matching): refuse to match a season with fewer than two readable references" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Route unreadable references to review ahead of multi-episode routing

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (import; helpers next to `_apply_multi_episode_review`; call site near line 1608)
- Modify: `backend/app/services/finalization_coordinator.py` (import; `_NON_REMATCHABLE_REVIEW_ERRORS`)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/test_matching_coordinator.py`, add `_route_unconfirmable_title` to the
existing `from app.services.matching_coordinator import (...)` block (keep alphabetical order
inside the block), and add this import below that block:

```python
from app.matcher.subtitle_utils import REFERENCES_UNREADABLE_ERROR_CODE
```

Append to the end of the file:

```python
@pytest.mark.unit
class TestUnreadableReferencesReviewRouting:
    """A title the matcher refused for lack of readable references goes to review
    with a message naming the subtitles, even when the runtime also hinted that the
    track is several conjoined episodes."""

    def _title(self, details: dict | None):
        return SimpleNamespace(
            state=TitleState.MATCHED,
            match_details=json.dumps(details) if details is not None else None,
            match_source="engram",
        )

    def _unreadable(self):
        return {
            "error": REFERENCES_UNREADABLE_ERROR_CODE,
            "usable_references": 1,
            "total_references": 37,
        }

    def test_unreadable_references_go_to_review_with_counts(self):
        title = self._title(self._unreadable())
        routed = _route_unconfirmable_title(title, conjoined_hint=None)
        assert routed == REFERENCES_UNREADABLE_ERROR_CODE
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == REFERENCES_UNREADABLE_ERROR_CODE
        assert "only 1 of 37 had any text" in parsed["message"]

    def test_unreadable_references_win_over_a_conjoined_hint(self):
        title = self._title(self._unreadable())
        routed = _route_unconfirmable_title(title, conjoined_hint=3)
        assert routed == REFERENCES_UNREADABLE_ERROR_CODE
        parsed = json.loads(title.match_details)
        assert parsed["error"] == REFERENCES_UNREADABLE_ERROR_CODE
        assert "joined together" not in parsed["message"]

    def test_hinted_track_without_the_error_still_routes_as_multi_episode(self):
        title = self._title(
            {"multi_episode": {"is_multi_episode": False, "reason": "single_episode"}}
        )
        assert _route_unconfirmable_title(title, conjoined_hint=3) == MULTI_EPISODE_ERROR_CODE

    def test_ordinary_title_is_untouched(self):
        title = self._title({"episode": "S1E1"})
        assert _route_unconfirmable_title(title, conjoined_hint=None) is None
        assert title.state == TitleState.MATCHED

    def test_unreadable_references_are_never_auto_rematched(self):
        from app.services.finalization_coordinator import _NON_REMATCHABLE_REVIEW_ERRORS

        assert REFERENCES_UNREADABLE_ERROR_CODE in _NON_REMATCHABLE_REVIEW_ERRORS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_matching_coordinator.py::TestUnreadableReferencesReviewRouting -v`
Expected: collection ERROR, `ImportError: cannot import name '_route_unconfirmable_title'`.

- [ ] **Step 3: Implement the helpers**

In `backend/app/services/matching_coordinator.py`, add this import with the other `app.matcher`
imports at the top of the module:

```python
from app.matcher.subtitle_utils import REFERENCES_UNREADABLE_ERROR_CODE
```

Insert directly above `def _apply_multi_episode_review(`:

```python
def _title_details(title: "DiscTitle") -> dict:
    """The title's persisted match_details as a dict ({} when absent or unparseable)."""
    if not title.match_details:
        return {}
    try:
        parsed = json.loads(title.match_details)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_unreadable_references_review(title: "DiscTitle") -> bool:
    """Park a title the matcher refused for lack of readable references. True if it did.

    The matcher returns this code when too few of the season's reference subtitles
    hold any text. There were no votes, so neither an episode suggestion nor a
    runtime-based multi-episode message describes the track; the reviewer needs to
    know the subtitles are the problem.
    """
    details = _title_details(title)
    if details.get("error") != REFERENCES_UNREADABLE_ERROR_CODE:
        return False
    title.state = TitleState.REVIEW
    usable = details.get("usable_references")
    total = details.get("total_references")
    counts = (
        f" (only {usable} of {total} had any text)"
        if isinstance(usable, int) and isinstance(total, int)
        else ""
    )
    details["message"] = (
        f"The reference subtitles for this season could not be read{counts}, so this "
        "track could not be matched by its dialogue. Assign the episode by hand."
    )
    title.match_details = json.dumps(details)
    return True


def _route_unconfirmable_title(title: "DiscTitle", conjoined_hint: int | None) -> str | None:
    """Apply the review routing that overrides a matcher result, most specific first.

    Unreadable references come first: with no usable corpus there is no vote
    evidence at all, so a runtime-based multi-episode message would bury the cause.
    Returns the error code the title was routed under, or None if it was not.
    """
    if _apply_unreadable_references_review(title):
        return REFERENCES_UNREADABLE_ERROR_CODE
    if _apply_multi_episode_review(title, conjoined_hint):
        return MULTI_EPISODE_ERROR_CODE
    return None
```

Inside `_apply_multi_episode_review`, replace:

```python
    details: dict = {}
    if title.match_details:
        try:
            parsed = json.loads(title.match_details)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            details = parsed
```

with:

```python
    details = _title_details(title)
```

- [ ] **Step 4: Use the router at the call site**

In `_match_single_file_inner`, replace:

```python
                # A conjoined track must not be auto-organized under a single code.
                if _apply_multi_episode_review(title, conjoined_hint):
                    # ids sanitized for the same reason as the pre-filter log above.
                    logger.info(
                        f"[MATCH] Title {sanitize_log_value(title_id)} "
                        f"(Job {sanitize_log_value(job_id)}): routed to review "
                        f"as multi-episode (hint={conjoined_hint})"
                    )
```

with:

```python
                # Neither a title the matcher refused (unreadable references) nor a
                # conjoined track may be auto-organized under a single code.
                routed = _route_unconfirmable_title(title, conjoined_hint)
                if routed:
                    # ids sanitized for the same reason as the pre-filter log above.
                    logger.info(
                        f"[MATCH] Title {sanitize_log_value(title_id)} "
                        f"(Job {sanitize_log_value(job_id)}): routed to review "
                        f"as {routed} (hint={conjoined_hint})"
                    )
```

- [ ] **Step 5: Never auto re-match it**

In `backend/app/services/finalization_coordinator.py`, add with the other `app.matcher` imports at
the top of the module (or directly above the `from app.services.matching_coordinator import`
line if there are none):

```python
from app.matcher.subtitle_utils import REFERENCES_UNREADABLE_ERROR_CODE
```

In `_NON_REMATCHABLE_REVIEW_ERRORS`, add after `MULTI_EPISODE_ERROR_CODE,`:

```python
    # The matcher refused the title: too few reference subtitles held any text. A
    # deeper scan against the same unreadable corpus cannot change that.
    REFERENCES_UNREADABLE_ERROR_CODE,
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_matching_coordinator.py -v`
Expected: all PASS, including the existing `TestMultiEpisodeReviewRouting`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check app/services tests/unit/test_matching_coordinator.py
uv run ruff format app/services/matching_coordinator.py app/services/finalization_coordinator.py tests/unit/test_matching_coordinator.py
git add app/services/matching_coordinator.py app/services/finalization_coordinator.py tests/unit/test_matching_coordinator.py
git commit -m "fix(matching): send tracks from a season with unreadable subtitles to review" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Scan hinted conjoined tracks deep enough to confirm three episodes

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (constant and helper below `_conjoined_episode_count`; call before `# 7. Run matching`)
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/unit/test_matching_coordinator.py`, add `CONJOINED_SCAN_POINTS`,
`MAX_CONJOINED_EPISODES`, and `_scan_points_for_hint` to the
`from app.services.matching_coordinator import (...)` block.

Append to the end of the file:

```python
@pytest.mark.unit
class TestConjoinedScanDepth:
    """Three-segment cartoon tracks (Dexter's Laboratory, Looney Tunes) could never
    be confirmed at the default 10 scan points: decompose_vote_runs needs more than
    3 points per run plus one."""

    def test_unhinted_track_keeps_the_requested_depth(self):
        assert _scan_points_for_hint(None, None) is None
        assert _scan_points_for_hint(37, None) == 37

    def test_hinted_track_scans_at_least_the_conjoined_depth(self):
        assert _scan_points_for_hint(None, 2) == CONJOINED_SCAN_POINTS
        assert _scan_points_for_hint(10, 3) == CONJOINED_SCAN_POINTS

    def test_deeper_requested_scan_is_not_reduced(self):
        assert _scan_points_for_hint(73, 3) == 73

    def test_conjoined_depth_is_a_lattice_level_that_confirms_the_cap(self):
        from app.matcher.episode_identification import snap_to_lattice_level
        from app.matcher.multi_episode import MIN_SCAN_POINTS_PER_RUN

        assert snap_to_lattice_level(CONJOINED_SCAN_POINTS) == CONJOINED_SCAN_POINTS
        assert CONJOINED_SCAN_POINTS > MIN_SCAN_POINTS_PER_RUN * MAX_CONJOINED_EPISODES + 1

    def test_three_segment_track_confirms_at_conjoined_depth_but_not_at_ten(self):
        from app.matcher.multi_episode import decompose_vote_runs

        deep = (
            [(s, "S01E01") for s in (0, 60, 120, 180, 240, 300)]
            + [(s, "S01E02") for s in (420, 480, 540, 600, 660)]
            + [(s, "S01E03") for s in (780, 840, 900, 960, 1020, 1080)]
        )
        verdict = decompose_vote_runs(deep, CONJOINED_SCAN_POINTS)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02", "S01E03")

        shallow = (
            [(s, "S01E01") for s in (0, 120, 240)]
            + [(s, "S01E02") for s in (480, 600)]
            + [(s, "S01E03") for s in (840, 960, 1080)]
        )
        assert decompose_vote_runs(shallow, 10).reason == "insufficient_scan_depth"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_matching_coordinator.py::TestConjoinedScanDepth -v`
Expected: collection ERROR, `ImportError: cannot import name 'CONJOINED_SCAN_POINTS'`.

- [ ] **Step 3: Implement**

In `backend/app/services/matching_coordinator.py`, insert directly after the end of
`_conjoined_episode_count` (after its final `return None`):

```python


# Scan depth for a track the runtime pre-filter admitted as conjoined. A confident
# multi-episode verdict needs more than MIN_SCAN_POINTS_PER_RUN * runs + 1 scan points
# (see app.matcher.multi_episode), so the default 10 confirms at most two runs and a
# three-segment cartoon track never confirms. 19 is the next scan-lattice level, so
# the first 10 transcripts are reused.
CONJOINED_SCAN_POINTS = 19


def _scan_points_for_hint(num_points: int | None, conjoined_hint: int | None) -> int | None:
    """Deepen the scan for a hinted conjoined track; never make a requested scan shallower."""
    if not conjoined_hint:
        return num_points
    if num_points is None or num_points < CONJOINED_SCAN_POINTS:
        return CONJOINED_SCAN_POINTS
    return num_points
```

In `_run_match_single_file`, replace:

```python
        # 7. Run matching
        try:
            await self._match_single_file_inner(
```

with:

```python
        # A hinted conjoined track needs a deeper scan before its vote runs can
        # confirm more than two episodes.
        num_points = _scan_points_for_hint(num_points, conjoined_hint)

        # 7. Run matching
        try:
            await self._match_single_file_inner(
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_matching_coordinator.py -v`
Expected: all PASS, including the unchanged `TestConjoinedEpisodeCount`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/services/matching_coordinator.py tests/unit/test_matching_coordinator.py
uv run ruff format app/services/matching_coordinator.py tests/unit/test_matching_coordinator.py
git add app/services/matching_coordinator.py tests/unit/test_matching_coordinator.py
git commit -m "fix(matching): scan conjoined tracks deeply enough to confirm three segments" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Changelog

**Files:**
- Modify: `CHANGELOG.md` (top of `## [Unreleased]` / `### Fixed`)

- [ ] **Step 1: Add the entries**

In `CHANGELOG.md`, insert directly below the first `### Fixed` heading under `## [Unreleased]`
(after its blank line, above the existing first bullet):

```markdown
- **Every track on a disc no longer matches the same episode when a season's
  subtitles are damaged.** Subtitle files whose line breaks had been doubled, or
  that leave out the cue numbers, were read as holding no dialogue at all while
  still passing validation, so they were cached and reused on every retry. On a
  Dexter's Laboratory set, 36 of the season's 37 reference subtitles read as empty,
  and every track matched the one episode that had text, at full confidence. Those
  files now read correctly, a subtitle with no readable dialogue is downloaded
  again instead of reused, subtitles imported by hand on Windows keep their line
  breaks, and a season with too few readable subtitles sends its tracks to review
  with a message saying so instead of guessing.

- **Cartoon discs with three segments per track are recognised as combined.**
  Shows like Dexter's Laboratory and Looney Tunes put three short segments in one
  track. Engram could confirm a combined track of two segments but never three,
  because it sampled too few points in the file; it now samples more densely when
  a track's length suggests several segments. Combined tracks still go to review
  for you to assign.

```

- [ ] **Step 2: Check for em and en dashes**

Run (from the repo root): `LC_ALL=C grep -nE $'\xe2\x80\x94|\xe2\x80\x93' CHANGELOG.md | head -5`
Expected: no new lines from the entries above (pre-existing matches elsewhere in the file are not
yours; compare against `git diff CHANGELOG.md`).

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): damaged subtitle references and three-segment tracks" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Verification

**Files:** none changed unless a failure needs fixing.

- [ ] **Step 1: Lint the backend**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` and no files would be reformatted.

- [ ] **Step 2: Run every affected unit test file**

Run:

```bash
uv run pytest tests/unit/test_srt_utils.py tests/unit/test_subtitle_utils.py tests/unit/test_manual_subtitle_import.py tests/unit/test_episode_identification.py tests/unit/test_matching_coordinator.py tests/unit/test_matcher_concurrency.py tests/unit/test_precomputed_augmentation.py tests/unit/test_provider_scheduler.py tests/unit/test_corpus_data_rekey.py -q
```

Expected: all PASS.

- [ ] **Step 3: Run the subtitle integration workflow**

Run: `uv run pytest tests/integration/test_subtitle_workflow.py -q`
Expected: all PASS.

- [ ] **Step 4: Run the full unit tier (main session only, not a subagent)**

Run: `uv run pytest tests/unit -q -p no:logging`
Expected: all PASS except caplog-based tests, which error under `-p no:logging`. Re-run any file
that reports caplog errors without the flag, for example
`uv run pytest tests/unit/test_<file>.py -q`, and confirm those pass.

- [ ] **Step 5: Confirm the branch history**

Run: `git log --oneline main..HEAD`
Expected: the two spec commits plus one commit per Task 1 to 9.

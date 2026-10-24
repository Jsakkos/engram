"""Fail when a MakeMKV release moves a robot-mode id that Engram parses.

``makemkvcon -r`` identifies TINFO/CINFO/SINFO attributes and SINFO stream
types by number, and the numbers are defined in the open-source half of
MakeMKV (``makemkvgui/inc/lgpl/apdefs.h``). The release notes are no guarantee
these stay put across a major version, and a moved id would not error: the
scan parser would just read the wrong field (a size as a duration, say). The
nightly "MakeMKV install check" runs this against each release it compiles.

Usage: python check_makemkv_protocol.py path/to/apdefs.h

Exit status: 0 when every id matches, 1 on drift, 2 on bad usage.
Stdlib only, so CI can run it on a bare python3.
"""

import re
import sys
from pathlib import Path

# Every id MakeMKVExtractor._parse_disc_info keys on (backend/app/core/extractor.py),
# with the value it assumes. Add an entry when the parser starts reading a new one.
EXPECTED: dict[str, int] = {
    "ap_iaType": 1,  # SINFO attr 1: stream type
    "ap_iaName": 2,  # TINFO title name, CINFO disc display name
    "ap_iaLangCode": 3,  # SINFO attr 3: audio/subtitle language
    "ap_iaChapterCount": 8,
    "ap_iaDuration": 9,
    "ap_iaDiskSize": 10,
    "ap_iaDiskSizeBytes": 11,
    "ap_iaSourceFileName": 16,
    "ap_iaVideoSize": 19,
    "ap_iaSegmentsCount": 25,
    "ap_iaSegmentsMap": 26,
    "ap_iaOutputFileName": 27,
    # Not parsed, but pinned: TINFO 28 is MakeMKV's metadata language, and the
    # audio language must keep coming from SINFO 3 (#241). If 28 ever moves,
    # the comments warning against reading it need revisiting too.
    "ap_iaMetadataLanguageCode": 28,
    # SINFO attr-1 values (_SINFO_AUDIO / _SINFO_SUBTITLES in the extractor).
    "APP_TTREE_AUDIO": 6202,
    "APP_TTREE_SUBPICTURE": 6203,
}

# Matches enum members (``ap_iaName=2,``) and constants
# (``static const unsigned long APP_TTREE_AUDIO=6202;``).
_DEFINITION_RE = re.compile(r"\b(ap_ia\w+|APP_TTREE_\w+)\s*=\s*(\d+)")


def parse_header(text: str) -> dict[str, int]:
    """Name to value for every attribute id and stream-type code in *text*."""
    return {name: int(value) for name, value in _DEFINITION_RE.findall(text)}


def find_drift(text: str) -> list[str]:
    """One message per expected id that is missing from *text* or has moved."""
    defined = parse_header(text)
    drift = []
    for name, expected in EXPECTED.items():
        actual = defined.get(name)
        if actual != expected:
            shown = "none" if actual is None else str(actual)
            drift.append(f"{name}: Engram expects {expected}, header defines {shown}")
    return drift


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_makemkv_protocol.py path/to/apdefs.h", file=sys.stderr)
        return 2
    drift = find_drift(Path(argv[0]).read_text(encoding="utf-8", errors="replace"))
    if drift:
        print("MakeMKV robot-mode ids Engram parses have changed:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print("Update backend/app/core/extractor.py, then EXPECTED here.", file=sys.stderr)
        return 1
    print(f"All {len(EXPECTED)} robot-mode ids Engram parses are unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

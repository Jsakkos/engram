"""Unit tests for cross-episode subtitle de-duplication.

Regression coverage for the harvester contamination found in the DS9 cache:
a provider returned S01E05's dialogue ("Babel") for an S02E05 request, so the
two SRTs carried identical dialogue with only different timestamps. The cache
builder then vectorized identical text into two episode slots, and matching for
those episodes degraded to "no clear vote". The guard must catch a saved SRT
whose cleaned dialogue is identical to a *different* episode already on disk.
"""

from app.matcher.subtitle_utils import find_duplicate_episode_srts
from app.matcher.testing_service import _reject_content_duplicates


def _srt(lines: list[tuple[str, str, str]]) -> str:
    """Render ``[(start, end, text)]`` cues into a minimal valid SRT string."""
    return "\n".join(
        f"{i}\n{start} --> {end}\n{text}\n" for i, (start, end, text) in enumerate(lines, 1)
    )


# Same dialogue, two different sync timings — the exact shape of the bug.
_DIALOGUE = [
    "Tarkalean tea again, Doctor?",
    "Just one of the perks of the job.",
    "Have you seen the ambassador?",
    "He left for the wormhole an hour ago.",
    "Something is wrong with the replicators.",
]
_TIMES_A = [
    ("00:00:15,049", "00:00:17,448"),
    ("00:00:18,000", "00:00:20,500"),
    ("00:00:22,100", "00:00:24,900"),
    ("00:00:26,000", "00:00:28,400"),
    ("00:00:30,000", "00:00:32,500"),
]
_TIMES_B = [
    ("00:00:15,849", "00:00:18,351"),
    ("00:00:18,800", "00:00:21,300"),
    ("00:00:22,900", "00:00:25,700"),
    ("00:00:27,100", "00:00:29,600"),
    ("00:00:31,200", "00:00:33,900"),
]


def test_flags_cross_season_content_duplicate(tmp_path):
    """An SRT whose dialogue equals a different episode's (only timestamps
    differ) is flagged; the lexicographically-first code is kept, and a
    genuinely distinct episode is left untouched."""
    # S01E05: the real "Babel" subtitle.
    (tmp_path / "Star Trek - S01E05.srt").write_text(
        _srt([(s, e, t) for (s, e), t in zip(_TIMES_A, _DIALOGUE, strict=True)]), encoding="utf-8"
    )
    # S02E05: the SAME dialogue, re-timed (the mislabeled contaminant).
    (tmp_path / "Star Trek - S02E05.srt").write_text(
        _srt([(s, e, t) for (s, e), t in zip(_TIMES_B, _DIALOGUE, strict=True)]), encoding="utf-8"
    )
    # A genuinely different episode — must NOT be flagged.
    (tmp_path / "Star Trek - S02E01.srt").write_text(
        _srt(
            [
                ("00:00:01,000", "00:00:03,000", "The station is under attack."),
                ("00:00:04,000", "00:00:06,500", "Raise shields and return fire."),
                ("00:00:07,000", "00:00:09,500", "The Cardassians are retreating."),
            ]
        ),
        encoding="utf-8",
    )

    dups = find_duplicate_episode_srts(tmp_path)

    assert set(dups) == {"S02E05"}
    assert dups["S02E05"].name == "Star Trek - S02E05.srt"


def test_distinct_episodes_are_not_flagged(tmp_path):
    """A directory of genuinely different episodes yields no duplicates."""
    (tmp_path / "Star Trek - S01E01.srt").write_text(
        _srt([("00:00:01,000", "00:00:03,000", "The wormhole has opened.")]), encoding="utf-8"
    )
    (tmp_path / "Star Trek - S01E02.srt").write_text(
        _srt([("00:00:01,000", "00:00:03,000", "A Bajoran terrorist is aboard.")]), encoding="utf-8"
    )

    assert find_duplicate_episode_srts(tmp_path) == {}


def test_reject_content_duplicates_marks_notfound_and_deletes(tmp_path):
    """The harvest guard deletes a mislabeled-duplicate SRT and rewrites its
    episode result to not_found, leaving distinct episodes untouched."""
    (tmp_path / "Star Trek - S01E05.srt").write_text(
        _srt([(s, e, t) for (s, e), t in zip(_TIMES_A, _DIALOGUE, strict=True)]), encoding="utf-8"
    )
    s02e05 = tmp_path / "Star Trek - S02E05.srt"
    s02e05.write_text(
        _srt([(s, e, t) for (s, e), t in zip(_TIMES_B, _DIALOGUE, strict=True)]), encoding="utf-8"
    )
    s02e01 = tmp_path / "Star Trek - S02E01.srt"
    s02e01.write_text(
        _srt([("00:00:01,000", "00:00:03,000", "The station is under attack.")]), encoding="utf-8"
    )

    episodes = [
        {
            "code": "S02E01",
            "status": "downloaded",
            "path": str(s02e01),
            "source": "opensubtitles_api",
        },
        {"code": "S02E05", "status": "downloaded", "path": str(s02e05), "source": "addic7ed"},
    ]

    out = _reject_content_duplicates(tmp_path, episodes)

    rejected = next(e for e in out if e["code"] == "S02E05")
    assert rejected["status"] == "not_found"
    assert rejected["path"] is None
    assert rejected["source"] is None
    assert not s02e05.exists()  # contaminant file removed

    kept = next(e for e in out if e["code"] == "S02E01")
    assert kept["status"] == "downloaded"
    assert s02e01.exists()

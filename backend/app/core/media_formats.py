"""Which video files a manual import accepts, and how they are named and served.

A disc Engram rips itself always comes out of MakeMKV as ``.mkv``, so the rip,
backup and staging paths keep their ``*.mkv`` globs. A *manual import* is
different: it files media the user already has, which is often an MP4 from
HandBrake or a transport stream from a DVR. Matching reads any container through
ffmpeg/ffprobe/fpcalc, so the container only matters in three places, all of
which go through this module:

- the import scanner and folder browser, which decide what is importable;
- the organizer, which must keep the source's extension (an MP4 renamed to
  ``.mkv`` is a mislabelled file, not a converted one);
- the review media endpoint, which reports a Content-Type to the player.

Engram never remuxes or transcodes an import. ffmpeg is auto-detected but not
bundled, so a conversion step could not be depended on.

``.vob`` is deliberately absent: a DVD title spans several VOBs and its
structure lives in the IFOs, so loose VOBs are not episodes. ``VIDEO_TS``
folder import (via MakeMKV) is the supported route for them.
"""

from __future__ import annotations

from pathlib import Path

IMPORTABLE_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mkv", ".mp4", ".m4v", ".ts", ".m2ts"})

# Everything Engram rips itself is Matroska, so an unknown or missing suffix
# falls back to the historical name rather than producing an extensionless file.
DEFAULT_SUFFIX = ".mkv"

_MEDIA_TYPES: dict[str, str] = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
}


def is_importable_video(name: str | Path) -> bool:
    """Whether a file name has an extension a manual import accepts (any case)."""
    return Path(name).suffix.lower() in IMPORTABLE_VIDEO_EXTENSIONS


def library_suffix(source: str | Path) -> str:
    """The extension an organized copy of ``source`` must carry.

    The source's own (lowercased) extension when it is a known video container,
    otherwise ``.mkv``. Organizing moves the file untouched, so the name has to
    describe the bytes that are actually in it.
    """
    suffix = Path(source).suffix.lower()
    return suffix if suffix in IMPORTABLE_VIDEO_EXTENSIONS else DEFAULT_SUFFIX


def media_type_for(path: str | Path) -> str:
    """The Content-Type to serve a video file with (Matroska when unknown)."""
    return _MEDIA_TYPES.get(Path(path).suffix.lower(), _MEDIA_TYPES[DEFAULT_SUFFIX])


def list_video_files(directory: Path) -> list[Path]:
    """Importable video files directly inside ``directory``, sorted by path."""
    try:
        return sorted(p for p in directory.iterdir() if p.is_file() and is_importable_video(p))
    except OSError:
        return []

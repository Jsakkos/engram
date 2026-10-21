"""Which audio stream of a ripped file to transcribe, and whether any can be (issue #241).

The matcher transcribes dialogue and compares it with reference subtitles in one
language. Two things decide whether that can work, and both come from the FILE,
not the disc scan: MakeMKV's language settings may leave tracks out of the rip,
and imports have no scan at all.

``probe_audio_streams`` asks ffprobe once per file. ``plan_audio`` turns the
answer into a verdict:

* ``match``     a non-commentary stream in the wanted language: transcribe it.
* ``untagged``  none in that language, but an untagged one (DVDs): try it, as today.
* ``foreign``   every usable stream is tagged with another language: do not guess.
* ``no_audio``  the file has no audio stream at all.
* ``unprobed``  ffprobe could not answer: behave exactly as before (no ``-map``).

Anything that cannot decide falls back to today's behaviour. Only ``foreign``
and ``no_audio`` stop a transcription.
"""

import json
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app.core.languages import normalize_language

# Track titles that mark commentary or audio description. MakeMKV never set the
# stream-flag bits for these on any disc we have logs for, so the name and the
# container disposition are the only signals.
COMMENTARY_PATTERN = re.compile(
    r"comment|audio description|described|kommentar|commentaire|comentario|commento", re.I
)


@dataclass(frozen=True)
class AudioStream:
    audio_index: int  # position among the file's audio streams: what -map 0:a:<n> takes
    language: str | None  # normalized; None = untagged
    title: str
    channels: int
    default: bool
    commentary: bool  # commentary or audio description: never transcribed


@dataclass(frozen=True)
class AudioPlan:
    verdict: str
    stream: AudioStream | None = None
    languages: list[str | None] = field(default_factory=list)  # every audio stream
    # Languages of the non-commentary streams: what the track's dialogue can be in.
    usable_languages: list[str | None] = field(default_factory=list)
    # The stream ffmpeg (and fpcalc) pick when not told: before #241 every
    # transcript and fingerprint came from it.
    auto_index: int | None = None


def follows_auto_pick(plan: AudioPlan) -> bool:
    """True when the planned stream is the one ffmpeg would pick by itself.

    Then a transcript cached before streams were chosen explicitly, and a
    fingerprint fpcalc takes from the whole file, describe the same audio the
    matcher now transcribes. An unprobed plan names no stream and follows it.
    """
    return plan.stream is None or plan.stream.audio_index == plan.auto_index


def _auto_pick(streams: list[AudioStream]) -> int:
    """ffmpeg's own choice: the default-flagged stream, else the most channels, first on ties.

    Checked against real ffmpeg and fpcalc: a default-flagged mono track beats an
    unflagged 5.1 one. ``test_auto_index_matches_real_ffmpeg_choice`` pins this
    against whatever ffmpeg the test machine has.
    """
    return max(streams, key=lambda s: (s.default, s.channels)).audio_index


_probe_memo: dict[tuple[str, int, int], list[AudioStream] | None] = {}
_probe_lock = threading.Lock()


def probe_audio_streams(path: Path) -> list[AudioStream] | None:
    """The file's audio streams, or None when ffprobe cannot tell us."""
    try:
        resolved = Path(path).resolve()
        st = resolved.stat()
    except OSError:
        return None
    key = (str(resolved), st.st_size, st.st_mtime_ns)
    with _probe_lock:
        if key in _probe_memo:
            return _probe_memo[key]

    streams = _run_probe(resolved)
    with _probe_lock:
        _probe_memo[key] = streams
    return streams


_PROBE_ENTRIES = (
    "stream=index,channels:stream_tags=language,title:stream_disposition=default,comment,"
    + "visual_impaired"
)


def _run_probe(path: Path) -> list[AudioStream] | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        _PROBE_ENTRIES,
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"[AUDIO] ffprobe could not list audio streams of {path.name}: {e}")
        return None
    if result.returncode != 0:
        logger.warning(
            f"[AUDIO] ffprobe exited {result.returncode} listing audio streams of {path.name}"
        )
        return None
    try:
        raw = json.loads(result.stdout or "")
    except (json.JSONDecodeError, TypeError):
        return None
    entries = raw.get("streams") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return None

    streams = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        tags = entry.get("tags") or {}
        disposition = entry.get("disposition") or {}
        title = str(tags.get("title") or "")
        streams.append(
            AudioStream(
                audio_index=position,
                language=normalize_language(tags.get("language")),
                title=title,
                channels=int(entry.get("channels") or 0),
                default=bool(disposition.get("default")),
                commentary=bool(disposition.get("comment"))
                or bool(disposition.get("visual_impaired"))
                or bool(COMMENTARY_PATTERN.search(title)),
            )
        )
    return streams


def plan_audio(streams: list[AudioStream] | None, language: str) -> AudioPlan:
    """Pick the stream to transcribe for ``language`` (normalized code)."""
    if streams is None:
        return AudioPlan("unprobed")
    languages = [s.language for s in streams]
    if not streams:
        return AudioPlan("no_audio", languages=languages)

    usable = [s for s in streams if not s.commentary]
    details = {
        "languages": languages,
        "usable_languages": [s.language for s in usable],
        "auto_index": _auto_pick(streams),
    }
    wanted = [s for s in usable if s.language == language]
    if wanted:
        chosen = next((s for s in wanted if s.default), wanted[0])
        return AudioPlan("match", chosen, **details)
    untagged = [s for s in usable if s.language is None]
    if untagged:
        return AudioPlan("untagged", untagged[0], **details)
    return AudioPlan("foreign", None, **details)

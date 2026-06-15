import hashlib
import re
from pathlib import Path

from loguru import logger

from app.matcher.srt_utils import SubtitleReader, clean_text


def is_valid_srt_file(file_path: Path) -> bool:
    """Validate that ``file_path`` is a real SRT subtitle file, not HTML
    or other garbage masquerading as one.

    Checks:
    1. File exists and is at least 50 bytes.
    2. Header doesn't contain HTML markers.
    3. Contains the SRT timestamp arrow ``-->`` somewhere in the header.

    Lives in ``subtitle_utils`` so every provider client and the
    scheduler can validate downloads without importing
    ``testing_service`` (which would create a circular dependency:
    ``testing_service`` imports the scheduler, which imports
    ``is_valid_srt_file``).
    """
    try:
        if not file_path.exists() or file_path.stat().st_size < 50:
            return False

        # Decode by BOM. TVsubtitles (and others) sometimes serve
        # UTF-16-encoded SRTs; read as UTF-8 those keep a NUL between every
        # character, so the ASCII ``-->`` check below never matches and a
        # perfectly valid subtitle gets rejected. Read a generous chunk of
        # raw bytes (UTF-16 is 2 bytes/char, so 1000 bytes ≈ 500 chars —
        # still well past the first timestamp).
        raw = file_path.read_bytes()[:1000]
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            header = raw.decode("utf-16", errors="ignore").lower()
        else:
            header = raw.decode("utf-8", errors="ignore").lower()

        if any(marker in header for marker in ["<!doctype", "<html", "<head", "<body", "<div"]):
            logger.warning(f"Rejecting {file_path.name}: appears to be HTML, not SRT")
            return False

        if "-->" not in header:
            logger.warning(f"Rejecting {file_path.name}: no SRT timestamp markers found")
            return False

        return True

    except Exception as e:
        logger.warning(f"Error validating {file_path}: {e}")
        return False


# Ordered season/episode patterns, tried in sequence. The first match wins.
_SEASON_EPISODE_PATTERNS = [
    r"[Ss](\d{1,2})[Ee](\d{1,2})",  # S01E01 / s1e2
    r"(\d{1,2})x(\d{1,2})",  # 1x01 / 01x01
    r"Season\s*(\d+).*?(\d+)",  # Season 1 - 01
]


def parse_season_episode_numbers(text: str) -> tuple[int, int] | None:
    """Parse season and episode numbers from a string.

    Tries multiple common formats (S01E01, 1x01, "Season 1 ... 01") and
    returns the first match as a (season, episode) tuple, or None if no
    pattern matches.
    """
    for pattern in _SEASON_EPISODE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def generate_subtitle_patterns(series_name: str, season: int, episode: int) -> list[str]:
    """
    Generate various common subtitle filename patterns.

    Args:
        series_name (str): Name of the series
        season (int): Season number
        episode (int): Episode number

    Returns:
        List[str]: List of possible subtitle filenames
    """
    patterns = [
        # Standard format: "Show Name - S01E02.srt"
        f"{series_name} - S{season:02d}E{episode:02d}.srt",
        # Season x Episode format: "Show Name - 1x02.srt"
        f"{series_name} - {season}x{episode:02d}.srt",
        # Separate season/episode: "Show Name - Season 1 Episode 02.srt"
        f"{series_name} - Season {season} Episode {episode:02d}.srt",
        # Compact format: "ShowName.S01E02.srt"
        f"{series_name.replace(' ', '')}.S{season:02d}E{episode:02d}.srt",
        # Numbered format: "Show Name 102.srt"
        f"{series_name} {season:01d}{episode:02d}.srt",
        # Dot format: "Show.Name.1x02.srt"
        f"{series_name.replace(' ', '.')}.{season}x{episode:02d}.srt",
        # Underscore format: "Show_Name_S01E02.srt"
        f"{series_name.replace(' ', '_')}_S{season:02d}E{episode:02d}.srt",
    ]

    return patterns


# "S01E01" (single) -- the only shape the harvester writes. A multi-episode file
# ("S01E01E02") is ambiguous to vectorize as one row, so it's skipped by callers
# that pack one vector per file. Shared with scripts/pack_subtitle_cache.py so the
# build harvester and the disk-only packer agree on what a cached episode looks like.
SINGLE_EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,4})\.srt$")
MULTI_EP_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,4}[Ee]\d{1,4}")


def discover_season_srts(series_cache_dir: str | Path, season: int) -> list[tuple[str, Path]]:
    """Return ``[(code, path)]`` for single-episode SRTs of ``season`` on disk.

    Globs ``series_cache_dir`` for ``*.srt`` files, keeps only single-episode
    files belonging to ``season``, and returns them sorted by episode number
    with ``code`` normalized to ``S%02dE%02d``. Multi-episode and unparseable
    filenames are ignored. Returns ``[]`` if the directory is missing — the
    caller treats that as "not actually on disk" and falls back to harvesting.
    """
    d = Path(series_cache_dir)
    if not d.is_dir():
        return []
    found: list[tuple[int, str, Path]] = []
    for srt in d.glob("*.srt"):
        if MULTI_EP_RE.search(srt.name):
            continue
        m = SINGLE_EP_RE.search(srt.name)
        if not m:
            continue
        s, e = int(m.group(1)), int(m.group(2))
        if s != season:
            continue
        found.append((e, f"S{s:02d}E{e:02d}", srt))
    found.sort(key=lambda x: x[0])
    return [(code, path) for _e, code, path in found]


def _cleaned_dialogue(srt_path: Path) -> str:
    """Return an SRT's dialogue text, normalized for content comparison —
    timestamps and cue indices excluded, lowercased and whitespace-collapsed.

    Sourced from ``srt_utils`` (a leaf module) rather than the matcher's
    ``get_full_text`` so this utility stays out of the ``episode_identification``
    import cycle. Any two SRTs with identical dialogue produce the same string,
    which is all the duplicate check needs.
    """
    try:
        content = SubtitleReader.read_srt_file(srt_path)
    except (OSError, ValueError):
        return ""
    lines = SubtitleReader.extract_subtitle_chunk(content, 0, 999999)
    return clean_text(" ".join(lines))


def find_duplicate_episode_srts(series_cache_dir: str | Path) -> dict[str, tuple[str, Path]]:
    """Return ``{contaminant_code: (canonical_code, path)}`` for SRTs whose cleaned
    dialogue is identical to a *different* episode's in the same directory.

    A provider occasionally returns one episode's subtitle for a different
    episode's request (e.g. S01E05's "Babel" dialogue saved as S02E05, re-timed),
    so the two files carry identical dialogue with only different timestamps.
    Because the cache builder vectorizes the cleaned dialogue text, two such files
    collapse to identical reference vectors and wreck matching for both episodes.

    Scans a show's subtitle dir, groups single-episode SRTs by the SHA-256 of
    their cleaned dialogue (timestamps and cue indices excluded), and for each
    colliding group keeps the lexicographically-smallest episode code as
    canonical, returning the rest as ``contaminant_code -> (canonical_code, path)``.
    Multi-episode, unparseable, and empty/unreadable SRTs are ignored.
    """
    d = Path(series_cache_dir)
    if not d.is_dir():
        return {}

    by_hash: dict[str, list[tuple[str, Path]]] = {}
    for srt in d.glob("*.srt"):
        if MULTI_EP_RE.search(srt.name):
            continue
        m = SINGLE_EP_RE.search(srt.name)
        if not m:
            continue
        code = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
        text = _cleaned_dialogue(srt)
        if not text:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        by_hash.setdefault(digest, []).append((code, srt))

    duplicates: dict[str, tuple[str, Path]] = {}
    for group in by_hash.values():
        if len(group) < 2:
            continue
        # Keep the lexicographically-smallest code (earliest season/episode) as
        # canonical. Assumption: seasons are harvested in ascending order, so the
        # smaller code holds the authentic dialogue and the larger code is the
        # late-arriving contaminant. This heuristic is wrong when contamination
        # flows the other way (a higher episode's dialogue returned for a lower
        # episode's request), but that case is indistinguishable from content
        # alone — both files contain identical dialogue.
        group.sort(key=lambda ce: ce[0])
        canonical_code = group[0][0]
        for code, path in group[1:]:
            duplicates[code] = (canonical_code, path)
    return duplicates


def find_existing_subtitle(
    series_cache_dir: str, series_name: str, season: int, episode: int
) -> Path | None:
    """
    Check for existing subtitle files in various naming formats.

    Args:
        series_cache_dir (str): Directory containing subtitle files
        series_name (str): Name of the series
        season (int): Season number
        episode (int): Episode number

    Returns:
        Optional[str]: Path to existing subtitle file if found, None otherwise
    """
    patterns = generate_subtitle_patterns(series_name, season, episode)

    for pattern in patterns:
        filepath = Path(series_cache_dir) / pattern
        if filepath.exists():
            return filepath

    return None


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename by removing/replacing invalid characters.

    Args:
        filename (str): Original filename

    Returns:
        str: Sanitized filename
    """
    # Replace problematic characters
    filename = filename.replace(":", " -")
    filename = filename.replace("/", "-")
    filename = filename.replace("\\", "-")

    # Remove any other invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)

    return filename.strip()


def corpus_dir_name(tmdb_id, show_name: str) -> str:
    """On-disk dir name / manifest key for a show's precomputed corpus + subtitle cache.

    Keyed by ``tmdb_id`` so two same-named shows (e.g. Frasier 1993 #3452 vs the
    2023 revival #195241) never collide into one directory. Falls back to the
    sanitized show name only when no tmdb_id is known (legacy caches, or a flat
    import that never resolved an id).
    """
    if tmdb_id is not None and str(tmdb_id).strip():
        return str(tmdb_id)
    return sanitize_filename(show_name)

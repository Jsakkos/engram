"""Organizer - File organization and library management.

Moves ripped files from staging to the library with proper naming conventions:
- Movies: Library/Movies/Movie Name (Year)/Movie Name (Year).mkv
- TV: Library/TV/Show Name/Season XX/Show Name - SXXEXX.mkv
"""

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Allowed placeholders for naming format strings
ALLOWED_TV_PLACEHOLDERS = {
    "show",
    "season",
    "episode",
}  # season folder (episode validation uses ALLOWED_EPISODE_PLACEHOLDERS once routes are wired)
# Show *folder* format — adds year/tmdb_id for same-name disambiguation
# (Plex "{tmdb-NNNN}" / Jellyfin "[tmdbid-NNNN]").
ALLOWED_TV_SHOW_PLACEHOLDERS = {"show", "year", "tmdb_id"}
# Episode *filename* format — widened so the year can opt into the filename too.
ALLOWED_EPISODE_PLACEHOLDERS = {"show", "season", "episode", "year", "tmdb_id"}
ALLOWED_MOVIE_PLACEHOLDERS = {"title", "year"}


def format_season_folder(fmt: str, season: int) -> str:
    """Format a season folder name from a config format string."""
    # format_map (not format(**...)) so a user-supplied format with an unknown
    # placeholder raises KeyError → caught below → safe fallback. Equivalent to
    # format(**mapping) but keeps CodeQL's missing-named-argument check from
    # flagging the intentionally-dynamic, user-controlled format string.
    try:
        result = fmt.format_map({"season": season})
    except (KeyError, ValueError, IndexError):
        result = f"Season {season:02d}"
    return sanitize_filename(result)


def format_episode_filename(
    fmt: str,
    show: str,
    season: int,
    episode: int,
    *,
    year: int | None = None,
    tmdb_id: str | int | None = None,
) -> str:
    """Format an episode filename from a config format string.

    ``year``/``tmdb_id`` are optional placeholders ({year}, {tmdb_id}). When the
    chosen format omits them they are ignored; when year is missing, an empty
    ``()`` left behind is stripped (mirrors ``format_movie_folder``). The default
    format ("{show} - SxxExx") is unaffected.
    """
    # format_map keeps an unknown placeholder raising KeyError → safe fallback,
    # while avoiding CodeQL's missing-named-argument false positive on the
    # user-controlled format string (see format_season_folder).
    try:
        result = fmt.format_map(
            {
                "show": show,
                "season": season,
                "episode": episode,
                "year": year if year is not None else "",
                "tmdb_id": tmdb_id if tmdb_id is not None else "",
            }
        )
    except (KeyError, ValueError, IndexError):
        result = f"{show} - S{season:02d}E{episode:02d}"
    result = _strip_empty_name_groups(result)
    return sanitize_filename(result)


def format_movie_folder(fmt: str, title: str, year: int | None) -> str:
    """Format a movie folder name from a config format string."""
    try:
        result = fmt.format_map({"title": title, "year": year or ""})
    except (KeyError, ValueError, IndexError):
        result = f"{title} ({year})" if year else title
    # Clean up trailing empty parens if year is None
    result = re.sub(r"\s*\(\s*\)\s*$", "", result).strip()
    return sanitize_filename(result)


def _strip_empty_name_groups(name: str) -> str:
    """Remove empty (), {..-}, [..-] groups left when year/tmdb_id are absent,
    consuming the whitespace that preceded each removed group so no double space
    is left behind. Does NOT collapse other internal whitespace — so a bare
    default format ("{show}") yields a byte-identical folder to pre-feature
    behavior (e.g. a sanitized "Tom  Jerry" double space is preserved).

    A populated tag like "{tmdb-3452}" is preserved (the char before '}' is a
    digit, not '-'); genuinely non-empty parens like "(US)" are preserved too.
    """
    name = re.sub(r"\s*\(\s*\)", "", name)  # empty parens (+ leading ws)
    name = re.sub(r"\s*\{[^{}]*-\s*\}", "", name)  # empty Plex tag, e.g. {tmdb-}
    name = re.sub(r"\s*\[[^\[\]]*-\s*\]", "", name)  # empty Jellyfin tag, e.g. [tmdbid-]
    return name.strip()


def format_tv_show_folder(fmt: str, show: str, year: int | None, tmdb_id: str | int | None) -> str:
    """Format the show *directory* name from a config format string.

    Mirrors ``format_movie_folder`` but adds a ``{tmdb_id}`` placeholder for
    media-server disambiguation (Plex ``{tmdb-NNNN}`` / Jellyfin ``[tmdbid-NNNN]``).
    Empty groups are stripped when year/id are missing, so the stable id tag never
    degrades to ``Frasier {tmdb-}``. A falsy/empty/whitespace-only ``fmt`` (e.g. an
    existing DB that backfilled '') falls back to the bare show name == current
    behavior — a whitespace-only format must NOT collapse the show-folder level.
    """
    fmt = (fmt or "").strip()
    if not fmt:
        return sanitize_filename(show)
    try:
        result = fmt.format_map(
            {
                "show": show,
                "year": year if year is not None else "",
                "tmdb_id": tmdb_id if tmdb_id is not None else "",
            }
        )
    except (KeyError, ValueError, IndexError):
        result = show
    return sanitize_filename(_strip_empty_name_groups(result))


def validate_naming_format(fmt: str, allowed: set[str]) -> str | None:
    """Validate a naming format string. Returns error message or None if valid."""
    import string

    try:
        formatter = string.Formatter()
        fields = [name for _, name, _, _ in formatter.parse(fmt) if name is not None]
    except (ValueError, IndexError) as e:
        return f"Invalid format string: {e}"

    unknown = set(fields) - allowed
    if unknown:
        return f"Unknown placeholders: {unknown}. Allowed: {allowed}"

    # Check for path traversal
    if ".." in fmt or fmt.startswith("/") or fmt.startswith("\\"):
        return "Format must not contain path traversal characters"

    return None


def clean_movie_name(raw_name: str) -> str:
    """Clean up a movie name from volume label or filename.

    Converts: "THE_SOCIAL_NETWORK" -> "The Social Network"
    """
    # Replace underscores and dashes with spaces
    name = raw_name.replace("_", " ").replace("-", " ")

    # Remove common disc identifiers
    patterns_to_remove = [
        r"\s*disc\s*\d+",  # "Disc 1", "Disc 2"
        r"\s*d\d+",  # "D1", "D2"
        r"\s*cd\s*\d+",  # "CD1", "CD2"
        r"\s*dvd\s*\d+",  # "DVD1"
        r"\s*bluray",  # "BLURAY"
        r"\s*blu-ray",  # "Blu-ray"
        r"\s*bd\s*\d*",  # "BD", "BD50"
        r"\s*uhd",  # "UHD"
        r"\s*4k",  # "4K"
        r"\s*hdr",  # "HDR"
        r"\s*dolby\s*vision",  # "Dolby Vision"
    ]

    for pattern in patterns_to_remove:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    # Clean up extra spaces
    name = re.sub(r"\s+", " ", name).strip()

    # Title case
    name = name.title()

    # Fix common title case issues (articles, conjunctions)
    small_words = [
        "a",
        "an",
        "the",
        "and",
        "but",
        "or",
        "for",
        "nor",
        "on",
        "at",
        "to",
        "by",
        "of",
        "in",
    ]
    words = name.split()
    for i, word in enumerate(words):
        if i > 0 and word.lower() in small_words:
            words[i] = word.lower()
    name = " ".join(words)

    return name


def resolve_conflict(dest_file: Path, conflict_resolution: str) -> tuple[Path | None, dict | None]:
    """Resolve a destination-file conflict according to the chosen strategy.

    Returns a (resolved_path, early_return) tuple:
    - ("overwrite") deletes the existing file and returns (dest_file, None)
    - ("rename") picks the next free "(vN)" path and returns (versioned, None)
    - ("skip") returns (None, {"skipped": True})
    - ("ask"/unknown) returns (None, {...}) with FILE_EXISTS conflict details

    When dest_file does not exist, returns (dest_file, None) unchanged.
    The early_return dict carries only conflict-specific keys; callers merge it
    with their own result shape.
    """
    if not dest_file.exists():
        return dest_file, None

    if conflict_resolution == "overwrite":
        logger.info(f"Overwriting existing file: {dest_file}")
        dest_file.unlink()
        return dest_file, None

    if conflict_resolution == "rename":
        # Find next available version
        counter = 2
        while True:
            versioned = dest_file.with_stem(f"{dest_file.stem} (v{counter})")
            if not versioned.exists():
                logger.info(f"Renaming to avoid conflict: {versioned}")
                return versioned, None
            counter += 1

    if conflict_resolution == "skip":
        logger.info(f"Skipping file due to conflict: {dest_file}")
        return None, {"success": True, "skipped": True}

    # "ask" or unknown — return conflict info for user review
    return None, {
        "success": False,
        "error": f"File already exists: {dest_file}",
        "error_code": "FILE_EXISTS",
        "existing_path": str(dest_file),
    }


def find_main_movie_file(staging_dir: Path) -> Path | None:
    """Find the main movie file (largest MKV) in a staging directory."""
    mkv_files = list(staging_dir.glob("*.mkv"))

    if not mkv_files:
        return None

    # Return the largest file (main movie)
    return max(mkv_files, key=lambda f: f.stat().st_size)


def find_extras(staging_dir: Path, main_file: Path) -> list[Path]:
    """Find extra/bonus content files (all MKVs except the main movie)."""
    mkv_files = list(staging_dir.glob("*.mkv"))
    return [f for f in mkv_files if f != main_file]


def organize_movie(
    staging_dir: Path,
    movie_name: str,
    year: int | None = None,
    library_path: Path | None = None,
    move_extras: bool = True,
    conflict_resolution: str = "ask",
) -> dict:
    """Organize a ripped movie into the library.

    Args:
        staging_dir: Path to the staging directory with MKV files
        movie_name: Clean movie name (will be further sanitized)
        year: Optional release year for folder naming
        library_path: Override for library path (defaults to settings)
        move_extras: Whether to move extra content as well
        conflict_resolution: How to handle file conflicts: "ask", "overwrite", "rename", "skip"

    Returns:
        dict with 'success', 'main_file', 'extras', 'extras_mapping', 'error' keys
    """
    # Imported function-locally to avoid a circular import with config_service.
    from app.services.config_service import get_config_sync

    if library_path is None:
        library_path = Path(get_config_sync().library_movies_path)

    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/movies"):
        return {
            "success": False,
            "main_file": None,
            "extras": [],
            "extras_mapping": {},
            "error": "Library path not configured. Please set Movies Library path in Settings.",
        }

    # Ensure library path exists
    try:
        library_path = Path(library_path)
        library_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured library path exists: {library_path}")
    except Exception as e:
        return {
            "success": False,
            "main_file": None,
            "extras": [],
            "extras_mapping": {},
            "error": f"Cannot create library directory {library_path}: {e}",
        }

    # Check if input is a file (manual selection) or directory (auto-detect)
    if staging_dir.is_file():
        main_file = staging_dir
        staging_dir = main_file.parent  # Update staging_dir for extras search
        logger.info(f"Using selected main movie file: {main_file.name}")
    else:
        # Find the main movie file in directory
        main_file = find_main_movie_file(staging_dir)
        if not main_file:
            return {
                "success": False,
                "main_file": None,
                "extras": [],
                "extras_mapping": {},
                "error": "No MKV files found in staging directory",
            }

    # Clean and sanitize the movie name
    clean_name = clean_movie_name(movie_name)

    # Load naming format from config
    cfg = get_config_sync()
    folder_name = format_movie_folder(cfg.naming_movie_format, clean_name, year)
    file_name = f"{folder_name}.mkv"

    # Create destination directory
    dest_dir = library_path / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / file_name

    logger.info(f"Moving main movie: {main_file.name} -> {dest_file}")

    # Check if destination exists and handle conflict
    dest_file, early = resolve_conflict(dest_file, conflict_resolution)
    if early:
        return {**early, "main_file": None, "extras": [], "extras_mapping": {}}

    try:
        # Move main movie
        shutil.move(str(main_file), str(dest_file))

        moved_extras = []
        extras_mapping: dict[str, Path] = {}

        # Move extras if requested
        if move_extras:
            extras = find_extras(staging_dir, main_file)
            if extras:
                extras_dir = dest_dir / "Extras"
                extras_dir.mkdir(exist_ok=True)

                for i, extra in enumerate(extras, 1):
                    extra_name = f"Extra {i}.mkv"
                    extra_dest = extras_dir / extra_name
                    logger.info(f"Moving extra: {extra.name} -> {extra_dest}")
                    shutil.move(str(extra), str(extra_dest))
                    moved_extras.append(extra_dest)
                    extras_mapping[extra.name] = extra_dest

        # Clean up empty staging directory
        try:
            remaining = list(staging_dir.iterdir())
            if not remaining:
                staging_dir.rmdir()
                logger.info(f"Cleaned up empty staging dir: {staging_dir}")
        except Exception as e:
            logger.warning(f"Could not clean staging dir: {e}")

        return {
            "success": True,
            "main_file": dest_file,
            "extras": moved_extras,
            "extras_mapping": extras_mapping,
            "error": None,
        }

    except Exception as e:
        logger.exception("Error organizing movie")
        return {
            "success": False,
            "main_file": None,
            "extras": [],
            "extras_mapping": {},
            "error": str(e),
        }


def sanitize_filename(name: str) -> str:
    """Remove invalid filename characters."""
    # Remove characters not allowed in Windows filenames
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "")

    # Also remove leading/trailing spaces and dots
    name = name.strip(". ")

    return name


# Convenience instance
class MovieOrganizer:
    """High-level interface for organizing movies."""

    def organize(
        self,
        staging_dir: Path,
        volume_label: str,
        detected_name: str | None = None,
        year: int | None = None,
    ) -> dict:
        """Organize a movie from staging to library.

        Uses detected_name if provided, otherwise falls back to volume_label.
        """
        movie_name = detected_name or volume_label
        return organize_movie(staging_dir, movie_name, year)


movie_organizer = MovieOrganizer()


def organize_tv_episode(
    source_file: Path,
    show_name: str,
    episode_code: str,
    library_path: Path | None = None,
    conflict_resolution: str = "ask",
    *,
    year: int | None = None,
    tmdb_id: str | None = None,
    ordering: str = "aired",
    episode_group_id: str | None = None,
) -> dict:
    """Organize a ripped TV episode into the library.

    Args:
        source_file: Path to the MKV file to move
        show_name: Name of the TV show (e.g., "The Office")
        episode_code: CANONICAL (TMDB aired-order) episode code (e.g., "S01E01").
            Always the canonical identity — never a projected number.
        library_path: Override for library path (defaults to settings)
        conflict_resolution: How to handle file conflicts: "ask", "overwrite", "rename", "skip"
        tmdb_id: Show's TMDB id; required to project a non-aired ordering.
        ordering: Output ordering for the FILENAME only ("aired" = identity).
            The canonical episode_code is unchanged; only the on-disk numbers
            are projected, so matched_episode and the fingerprint key stay
            canonical (#200).
        episode_group_id: Resolved TMDB group id for ``ordering`` (unused here;
            accepted so callers can pass the resolver's full result for audit).
        year: First-air year used for same-name show disambiguation in the folder
            name (e.g. Frasier 1993 vs 2023). Only affects the show folder when
            the configured ``naming_tv_show_format`` includes ``{year}``.

    Returns:
        dict with 'success', 'final_path', 'error' keys
    """
    import re

    # Imported function-locally to avoid a circular import with config_service.
    from app.services.config_service import get_config_sync

    if library_path is None:
        library_path = Path(get_config_sync().library_tv_path)

    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/tv"):
        return {
            "success": False,
            "final_path": None,
            "error": "Library path not configured. Please set TV Library path in Settings.",
        }

    # Parse episode code to extract season and episode numbers
    ep_match = re.match(r"S(\d+)E(\d+)", episode_code, re.IGNORECASE)
    if not ep_match:
        return {
            "success": False,
            "final_path": None,
            "error": f"Invalid episode code format: {episode_code}",
        }

    season_num = int(ep_match.group(1))
    episode_num = int(ep_match.group(2))

    # Load naming format from config
    cfg = get_config_sync()

    # Project the CANONICAL (aired) number to the chosen output ordering for the
    # filename only (#200). This is the one and only projection seam: matched_episode
    # in the DB and the fingerprint key stay canonical. Aired/no-tmdb_id is a no-op.
    out_season, out_episode = season_num, episode_num
    if ordering != "aired" and tmdb_id:
        from app.core.episode_ordering import project_episode

        out_season, out_episode = project_episode(
            tmdb_id, ordering, season_num, episode_num, cfg.tmdb_api_key
        )

    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    show_folder = format_tv_show_folder(cfg.naming_tv_show_format, clean_show, year, tmdb_id)
    season_folder = format_season_folder(cfg.naming_season_format, out_season)
    ep_stem = format_episode_filename(
        cfg.naming_episode_format,
        clean_show,
        out_season,
        out_episode,
        year=year,
        tmdb_id=tmdb_id,
    )
    filename = f"{ep_stem}.mkv"

    # Build destination path. The show folder may carry year/tmdb-id so same-name
    # shows (Frasier 1993 vs 2023) coexist; default "{show}" == bare clean_show.
    library_path = Path(library_path)
    dest_dir = library_path / show_folder / season_folder
    dest_file = dest_dir / filename

    logger.info(f"Organizing TV episode: {source_file.name} -> {dest_file}")

    # Check if destination exists and handle conflict
    dest_file, early = resolve_conflict(dest_file, conflict_resolution)
    if early:
        return {**early, "final_path": None}

    try:
        # Ensure directory exists
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Move the file
        shutil.move(str(source_file), str(dest_file))

        logger.info(f"Successfully organized: {dest_file}")

        return {"success": True, "final_path": dest_file, "error": None}

    except Exception as e:
        logger.exception(f"Error organizing TV episode {source_file}")
        return {"success": False, "final_path": None, "error": str(e)}


def organize_tv_extras(
    source_file: Path,
    show_name: str,
    season: int,
    library_path: Path | None = None,
    disc_number: int = 1,
    extra_index: int = 1,
    title_index: int | None = None,
    *,
    year: int | None = None,
    tmdb_id: str | None = None,
) -> dict:
    """Organize a ripped TV extra/bonus content into the library Extras folder.

    Args:
        source_file: Path to the MKV file to move
        show_name: Name of the TV show (e.g., "The Office")
        season: Season number
        library_path: Override for library path (defaults to settings)
        disc_number: Disc number for multi-disc sets (default: 1)
        extra_index: Index of this extra on the disc (default: 1)
        title_index: MakeMKV title index for unique naming (e.g., t03)
        year: First-air year for show-folder disambiguation (e.g. Frasier 1993 vs 2023).
            Only affects the folder when ``naming_tv_show_format`` includes ``{year}``.
        tmdb_id: Show's TMDB id for show-folder disambiguation. Only affects the folder
            when ``naming_tv_show_format`` includes ``{tmdb_id}``.

    Returns:
        dict with 'success', 'final_path', 'error' keys
    """
    # Imported function-locally to avoid a circular import with config_service.
    from app.services.config_service import get_config_sync

    if library_path is None:
        library_path = Path(get_config_sync().library_tv_path)

    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/tv"):
        return {
            "success": False,
            "final_path": None,
            "error": "Library path not configured. Please set TV Library path in Settings.",
        }

    # Load naming format from config
    cfg = get_config_sync()

    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    # Use the SAME disambiguated show folder as organize_tv_episode so an extra
    # and its episodes land under one show folder (TV-organize-paths-sync hazard).
    show_folder = format_tv_show_folder(cfg.naming_tv_show_format, clean_show, year, tmdb_id)
    season_folder = format_season_folder(cfg.naming_season_format, season)

    if title_index is not None:
        extra_name = f"{clean_show} Disc {disc_number} Extra t{title_index:02d}.mkv"
    else:
        extra_name = f"{clean_show} Disc {disc_number} Extra {extra_index}.mkv"

    # Build destination path
    library_path = Path(library_path)
    dest_dir = library_path / show_folder / season_folder / "Extras"
    dest_file = dest_dir / extra_name

    logger.info(f"Organizing TV extra: {source_file.name} -> {dest_file}")

    if dest_file.exists():
        return {
            "success": False,
            "final_path": None,
            "error": f"Destination file already exists: {dest_file}",
            "error_code": "FILE_EXISTS",
        }

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_file), str(dest_file))
        logger.info(f"Successfully organized extra: {dest_file}")
        return {"success": True, "final_path": dest_file, "error": None}
    except Exception as e:
        logger.exception(f"Error organizing TV extra {source_file}")
        return {"success": False, "final_path": None, "error": str(e)}


class TVOrganizer:
    """High-level interface for organizing TV episodes."""

    def organize(
        self,
        source_file: Path,
        show_name: str,
        episode_code: str,
        *,
        tmdb_id: str | None = None,
        ordering: str = "aired",
        episode_group_id: str | None = None,
        year: int | None = None,
    ) -> dict:
        """Organize a TV episode from staging to library.

        Forwards the output-ordering controls AND show disambiguation (year/tmdb_id)
        to organize_tv_episode so the library-mode path (no explicit library_path)
        also honors them. episode_code stays canonical; only the filename is projected.
        """
        return organize_tv_episode(
            source_file,
            show_name,
            episode_code,
            tmdb_id=tmdb_id,
            ordering=ordering,
            episode_group_id=episode_group_id,
            year=year,
        )

    def organize_batch(
        self,
        files: list[tuple[Path, str]],
        show_name: str,
        *,
        year: int | None = None,
        tmdb_id: str | None = None,
    ) -> list[dict]:
        """Organize multiple TV episodes.

        Args:
            files: List of (file_path, episode_code) tuples
            show_name: Name of the TV show
            year: First-air year for show-folder disambiguation (threaded to organize).
            tmdb_id: Show's TMDB id for show-folder disambiguation.

        Returns:
            List of result dicts for each file
        """
        results = []
        for source_file, episode_code in files:
            result = self.organize(source_file, show_name, episode_code, year=year, tmdb_id=tmdb_id)
            results.append(result)
        return results


tv_organizer = TVOrganizer()

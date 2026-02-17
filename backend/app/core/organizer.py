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


def clean_movie_name(raw_name: str) -> str:
    """Clean up a movie name from volume label or filename.
    
    Converts: "THE_SOCIAL_NETWORK" -> "The Social Network"
    """
    # Replace underscores and dashes with spaces
    name = raw_name.replace("_", " ").replace("-", " ")
    
    # Remove common disc identifiers
    patterns_to_remove = [
        r"\s*disc\s*\d+",  # "Disc 1", "Disc 2"
        r"\s*d\d+",        # "D1", "D2"
        r"\s*cd\s*\d+",    # "CD1", "CD2"
        r"\s*dvd\s*\d+",   # "DVD1"
        r"\s*bluray",      # "BLURAY"
        r"\s*blu-ray",     # "Blu-ray"
        r"\s*bd\s*\d*",    # "BD", "BD50"
        r"\s*uhd",         # "UHD"
        r"\s*4k",          # "4K"
        r"\s*hdr",         # "HDR"
        r"\s*dolby\s*vision",  # "Dolby Vision"
    ]
    
    for pattern in patterns_to_remove:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)
    
    # Clean up extra spaces
    name = re.sub(r"\s+", " ", name).strip()
    
    # Title case
    name = name.title()
    
    # Fix common title case issues (articles, conjunctions)
    small_words = ["a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "by", "of", "in"]
    words = name.split()
    for i, word in enumerate(words):
        if i > 0 and word.lower() in small_words:
            words[i] = word.lower()
    name = " ".join(words)
    
    return name


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
        dict with 'success', 'main_file', 'extras', 'error' keys
    """
    if library_path is None:
        from app.services.config_service import get_config_sync

        library_path = Path(get_config_sync().library_movies_path)
    
    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/movies"):
        return {
            "success": False,
            "main_file": None,
            "extras": [],
            "error": "Library path not configured. Please set Movies Library path in Settings."
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
            "error": f"Cannot create library directory {library_path}: {e}"
        }
    
    # Check if input is a file (manual selection) or directory (auto-detect)
    if staging_dir.is_file():
        main_file = staging_dir
        staging_dir = main_file.parent # Update staging_dir for extras search
        logger.info(f"Using selected main movie file: {main_file.name}")
    else:
        # Find the main movie file in directory
        main_file = find_main_movie_file(staging_dir)
        if not main_file:
            return {
                "success": False,
                "main_file": None,
                "extras": [],
                "error": "No MKV files found in staging directory"
            }
    
    # Clean and sanitize the movie name
    clean_name = clean_movie_name(movie_name)
    
    # Create folder name (with year if available)
    if year:
        folder_name = f"{clean_name} ({year})"
    else:
        folder_name = clean_name
    
    # Sanitize for filesystem
    folder_name = sanitize_filename(folder_name)
    file_name = f"{folder_name}.mkv"
    
    # Create destination directory
    dest_dir = library_path / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    dest_file = dest_dir / file_name
    
    logger.info(f"Moving main movie: {main_file.name} -> {dest_file}")

    # Check if destination exists and handle conflict
    if dest_file.exists():
        if conflict_resolution == "overwrite":
            logger.info(f"Overwriting existing file: {dest_file}")
            dest_file.unlink()
        elif conflict_resolution == "rename":
            # Find next available version
            counter = 2
            while True:
                versioned = dest_file.with_stem(f"{dest_file.stem} (v{counter})")
                if not versioned.exists():
                    dest_file = versioned
                    logger.info(f"Renaming to avoid conflict: {dest_file}")
                    break
                counter += 1
        elif conflict_resolution == "skip":
            logger.info(f"Skipping file due to conflict: {dest_file}")
            return {"success": True, "skipped": True, "main_file": None, "extras": []}
        else:  # "ask" or unknown
            # Return conflict info for user review
            return {
                "success": False,
                "main_file": None,
                "extras": [],
                "error": f"File already exists: {dest_file}",
                "error_code": "FILE_EXISTS",
                "existing_path": str(dest_file)
            }


    try:
        # Move main movie
        shutil.move(str(main_file), str(dest_file))
        
        moved_extras = []
        
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
            "error": None
        }
        
    except Exception as e:
        logger.exception("Error organizing movie")
        return {
            "success": False,
            "main_file": None,
            "extras": [],
            "error": str(e)
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
) -> dict:
    """Organize a ripped TV episode into the library.

    Args:
        source_file: Path to the MKV file to move
        show_name: Name of the TV show (e.g., "The Office")
        episode_code: Episode code (e.g., "S01E01")
        library_path: Override for library path (defaults to settings)
        conflict_resolution: How to handle file conflicts: "ask", "overwrite", "rename", "skip"

    Returns:
        dict with 'success', 'final_path', 'error' keys
    """
    import re
    
    if library_path is None:
        from app.services.config_service import get_config_sync

        library_path = Path(get_config_sync().library_tv_path)
    
    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/tv"):
        return {
            "success": False,
            "final_path": None,
            "error": "Library path not configured. Please set TV Library path in Settings."
        }
    
    # Parse episode code to extract season
    match = re.match(r"S(\d+)E\d+", episode_code, re.IGNORECASE)
    if not match:
        return {
            "success": False,
            "final_path": None,
            "error": f"Invalid episode code format: {episode_code}"
        }
    
    season_num = int(match.group(1))
    
    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    season_folder = f"Season {season_num:02d}"
    filename = f"{clean_show} - {episode_code.upper()}.mkv"
    
    # Build destination path: Library/TV/Show Name/Season XX/Show Name - SXXEXX.mkv
    library_path = Path(library_path)
    dest_dir = library_path / clean_show / season_folder
    dest_file = dest_dir / filename
    
    logger.info(f"Organizing TV episode: {source_file.name} -> {dest_file}")

    # Check if destination exists and handle conflict
    if dest_file.exists():
        if conflict_resolution == "overwrite":
            logger.info(f"Overwriting existing file: {dest_file}")
            dest_file.unlink()
        elif conflict_resolution == "rename":
            # Find next available version
            counter = 2
            while True:
                # Insert version before extension
                versioned = dest_file.with_stem(f"{dest_file.stem} (v{counter})")
                if not versioned.exists():
                    dest_file = versioned
                    logger.info(f"Renaming to avoid conflict: {dest_file}")
                    break
                counter += 1
        elif conflict_resolution == "skip":
            logger.info(f"Skipping file due to conflict: {dest_file}")
            return {"success": True, "skipped": True, "final_path": None}
        else:  # "ask" or unknown
            # Return conflict info for user review
            return {
                "success": False,
                "final_path": None,
                "error": f"File already exists: {dest_file}",
                "error_code": "FILE_EXISTS",
                "existing_path": str(dest_file)
        }

    
    try:
        # Ensure directory exists
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Move the file
        shutil.move(str(source_file), str(dest_file))
        
        logger.info(f"Successfully organized: {dest_file}")
        
        return {
            "success": True,
            "final_path": dest_file,
            "error": None
        }
        
    except Exception as e:
        logger.exception(f"Error organizing TV episode {source_file}")
        return {
            "success": False,
            "final_path": None,
            "error": str(e)
        }


def organize_tv_extras(
    source_file: Path,
    show_name: str,
    season: int,
    library_path: Path | None = None,
    disc_number: int = 1,
    extra_index: int = 1,
) -> dict:
    """Organize a ripped TV extra/bonus content into the library Extras folder.

    Args:
        source_file: Path to the MKV file to move
        show_name: Name of the TV show (e.g., "The Office")
        season: Season number
        library_path: Override for library path (defaults to settings)
        disc_number: Disc number for multi-disc sets (default: 1)
        extra_index: Index of this extra on the disc (default: 1)

    Returns:
        dict with 'success', 'final_path', 'error' keys
    """
    if library_path is None:
        from app.services.config_service import get_config_sync

        library_path = Path(get_config_sync().library_tv_path)

    # Validate library path
    if not library_path or str(library_path) in ("", ".", "./library/tv"):
        return {
            "success": False,
            "final_path": None,
            "error": "Library path not configured. Please set TV Library path in Settings.",
        }

    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    season_folder = f"Season {season:02d}"

    # New naming: "Show Name Disc X Extras Y.mkv"
    extra_name = f"{clean_show} Disc {disc_number} Extras {extra_index}.mkv"

    # Build destination path: Library/TV/Show Name/Season XX/Extras/Show Name Disc X Extras Y.mkv
    library_path = Path(library_path)
    dest_dir = library_path / clean_show / season_folder / "Extras"
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
    ) -> dict:
        """Organize a TV episode from staging to library."""
        return organize_tv_episode(source_file, show_name, episode_code)
    
    def organize_batch(
        self,
        files: list[tuple[Path, str]],
        show_name: str,
    ) -> list[dict]:
        """Organize multiple TV episodes.
        
        Args:
            files: List of (file_path, episode_code) tuples
            show_name: Name of the TV show
            
        Returns:
            List of result dicts for each file
        """
        results = []
        for source_file, episode_code in files:
            result = self.organize(source_file, show_name, episode_code)
            results.append(result)
        return results


tv_organizer = TVOrganizer()

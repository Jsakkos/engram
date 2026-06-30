"""Recursive scanner for manual media import.

Given a folder (or a single .mkv file) chosen by the user, walk the tree, find
every .mkv at any depth, and group the files into import units keyed by
(show, season). Intermediate folders that are not "Season NN" (for example
"Disc 1") are transparent: we recurse through them and roll their files up into
the inferred season.

This is the single source of truth for which files belong to which import job,
so the preview and the actual import are always consistent. Pure and
synchronous; callers run it via asyncio.to_thread. No network calls.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Matches "Season 1", "season 01", "Season 12", etc. (mirrors the old watcher).
_SEASON_RE = re.compile(r"^[Ss]eason\s*0*(\d+)$")

# Matches "Disc 1", "disc 02", etc. Disc folders are transparent grouping levels,
# not shows, so a "Show / Disc N / *.mkv" layout resolves to one show, not many.
_DISC_RE = re.compile(r"^[Dd]isc\s*0*\d+$")

# Bound the walk so a user pointing at a huge tree (or a symlink loop) can't
# hang the request. Surfaced as ImportScan.truncated when hit.
_MAX_FILES = 5000
_MAX_DEPTH = 12


@dataclass
class ImportUnit:
    show_name: str | None
    season: int | None
    files: list[Path]
    total_bytes: int


@dataclass
class ImportScan:
    root: Path
    units: list[ImportUnit]
    loose_files: list[Path]
    total_files: int
    total_bytes: int
    truncated: bool = False
    # True when the picked folder is a single title (holds media / Season / Disc
    # directly) rather than a parent-of-shows/library folder. Drives in-place
    # organize layout: a single-title pick organizes next to itself, not under a
    # spurious TV/ or Movies/ subdir created inside the title folder.
    picked_is_show: bool = False
    # True when the picked folder is itself a "Season NN" folder. The show then
    # comes from its parent and the season from its own name. Drives in-place
    # organize: the canonical "Show (Year)/Season XX" is recreated under the
    # season folder's grandparent (the show's parent), not inside the season folder.
    picked_is_season: bool = False


def _clean_show(name: str) -> str:
    """Light cleanup of a folder name for use as a show title (keeps any year)."""
    cleaned = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    return cleaned or name


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _safe_dirs(p: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_dir(follow_symlinks=False):
                    out.append(Path(entry.path))
            except OSError:
                continue
    except OSError:
        return []
    return out


def _season_from_path(file: Path, root: Path) -> int | None:
    """Season from the nearest 'Season NN' ancestor of file under root, else None."""
    try:
        rel_parts = file.relative_to(root).parts
    except ValueError:
        rel_parts = file.parts
    for part in reversed(rel_parts[:-1]):  # exclude the filename itself
        m = _SEASON_RE.match(part)
        if m:
            return int(m.group(1))
    return None


def _iter_mkvs(root: Path) -> tuple[list[Path], bool]:
    """Recursively collect .mkv files under root, bounded by count and depth.

    Skips symlinked directories and any file whose resolved path escapes root,
    so a crafted symlink cannot surface outside files or cause a loop.
    """
    found: list[Path] = []
    truncated = False
    root_resolved = root.resolve()

    def walk(d: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if depth > _MAX_DEPTH:
            truncated = True
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for entry in entries:
            if len(found) >= _MAX_FILES:
                truncated = True
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".mkv"):
                    p = Path(entry.path)
                    try:
                        if not p.resolve().is_relative_to(root_resolved):
                            continue
                    except (OSError, ValueError):
                        continue
                    found.append(p)
            except OSError:
                continue

    walk(root, 0)
    return found, truncated


def scan(path: Path) -> ImportScan:
    """Scan a folder or single .mkv file into import units."""
    path = Path(path).expanduser()

    # Single-file target: one flat unit; show derived from the parent folder.
    if path.is_file():
        if path.suffix.lower() != ".mkv":
            return ImportScan(path.parent, [], [], 0, 0, False)
        size = _safe_size(path)
        unit = ImportUnit(_clean_show(path.parent.name), None, [path], size)
        return ImportScan(path.parent, [unit], [], 1, size, False, picked_is_show=True)

    root = path

    # Third identity case: the user navigated INTO a "Season NN" folder and picked
    # it directly. Neither the "picked folder is a show" nor the "picked folder is
    # a parent of shows" branch fits: the show is the folder's parent, and the
    # season is the folder's own name. Without this, show_for() returns the season
    # folder name ("Season 4") and _season_from_path() returns None (the season
    # segment is the root, which it excludes).
    picked_season_match = _SEASON_RE.match(root.name)
    picked_season = int(picked_season_match.group(1)) if picked_season_match else None

    files, truncated = _iter_mkvs(root)

    immediate_dirs = _safe_dirs(root)
    has_loose_top = any(f.parent == root for f in files)
    has_season_top = any(_SEASON_RE.match(d.name) for d in immediate_dirs)
    has_disc_top = any(_DISC_RE.match(d.name) for d in immediate_dirs)

    # The picked folder IS a single show when it directly holds media, season
    # folders, or disc folders. Only when its immediate children are none of
    # those do we treat each child as a separate show (a parent-of-shows folder).
    picked_is_show = has_loose_top or has_season_top or has_disc_top

    # Loose top-level files beside structured season folders are ambiguous; report
    # them rather than silently merging (preserves the old data-loss safeguard).
    loose_files: list[Path] = []
    structured = files
    if has_season_top and has_loose_top:
        loose_files = sorted(f for f in files if f.parent == root)
        structured = [f for f in files if f.parent != root]

    def show_for(file: Path) -> str | None:
        if picked_season is not None:
            return _clean_show(root.parent.name)
        if picked_is_show:
            return _clean_show(root.name)
        try:
            rel = file.relative_to(root)
        except ValueError:
            return _clean_show(root.name)
        return _clean_show(rel.parts[0]) if len(rel.parts) > 1 else _clean_show(root.name)

    groups: dict[tuple[str | None, int | None], list[Path]] = defaultdict(list)
    for f in structured:
        season = _season_from_path(f, root)
        if season is None:
            season = picked_season  # season folder is the root itself
        groups[(show_for(f), season)].append(f)

    units: list[ImportUnit] = []
    for (show, season), unit_files in sorted(
        groups.items(),
        key=lambda kv: (str(kv[0][0]), kv[0][1] if kv[0][1] is not None else -1),
    ):
        ordered = sorted(unit_files)
        units.append(ImportUnit(show, season, ordered, sum(_safe_size(f) for f in ordered)))

    total_files = len(structured) + len(loose_files)
    total_bytes = sum(u.total_bytes for u in units) + sum(_safe_size(f) for f in loose_files)
    return ImportScan(
        root,
        units,
        loose_files,
        total_files,
        total_bytes,
        truncated,
        picked_is_show=picked_is_show,
        picked_is_season=picked_season is not None,
    )

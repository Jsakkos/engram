"""Migrate the subtitle SRT cache from name-keyed dirs to tmdb_id-keyed dirs.

PR #288 ("key precomputed subtitle corpus by tmdb_id", cache v3) moved the
on-disk SRT harvest cache from ``<cache>/data/<sanitized show name>/`` to
``<cache>/data/<tmdb_id>/``. The ``subtitle_coverage`` table was already keyed by
tmdb_id, so ``build_subtitle_cache.py``'s complete-on-disk resume fast path still
sees a "done" coverage record for a season but then looks for its SRTs under the
new ``data/<tmdb_id>/`` dir, finds nothing (they're still under ``data/<name>/``),
and re-harvests from scratch. This one-shot, idempotent migration relocates the
legacy dirs so resume works again.

It is scoped to ``<cache>/data/`` only — ``precomputed/`` is rebuilt every run and
re-downloaded at runtime, so it needs no migration. Coverage records are already
tmdb-keyed and are left untouched.

Resolution is offline-first: show names are matched against
``scripts/curated_shows.csv`` (the list the cache was built from), falling back to
``tmdb_client.fetch_show_id`` only for names not in the CSV. A purely-numeric dir
(``1396/``) is treated as already-migrated and skipped — UNLESS it is also a show
name in the CSV (``24`` is the show "24", not tmdb_id 24), in which case it is
reported as ambiguous and left in place; pass ``--treat-as-name 24`` to force it.

Usage (from backend/):
    uv run python scripts/migrate_subtitle_cache_keys.py                 # dry-run (default)
    uv run python scripts/migrate_subtitle_cache_keys.py --apply         # actually move files
    uv run python scripts/migrate_subtitle_cache_keys.py --treat-as-name 24 --apply
    uv run python scripts/migrate_subtitle_cache_keys.py --cache-dir /path/to/cache
"""

import argparse
import csv
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Idempotent — repeated importlib loads (e.g. one fixture per test file) would
# otherwise accumulate duplicate entries in sys.path on every exec_module call.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from loguru import logger

from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.tmdb_client import fetch_show_id

_DEFAULT_CURATED_CSV = Path(__file__).parent / "curated_shows.csv"


@dataclass
class Tally:
    """Running counters surfaced in the final summary."""

    migrated: int = 0  # legacy dir renamed into a brand-new id dir
    merged: int = 0  # legacy dir merged into a pre-existing id dir
    files_moved: int = 0  # individual files carried into an existing id dir
    files_kept_larger: int = 0  # collisions resolved toward the legacy file
    files_dropped_smaller: int = 0  # collisions resolved toward the existing file
    skipped_already_id: int = 0  # numeric dir already in the tmdb_id scheme
    skipped_backup: list[str] = field(default_factory=list)  # manual backup-looking dir
    ambiguous: list[str] = field(default_factory=list)  # numeric dir that is also a show name
    unresolved: list[str] = field(default_factory=list)  # name with no tmdb_id
    failed: list[str] = field(default_factory=list)  # resolved but the move raised OSError


# Suffixes that mark a deliberate manual backup / scratch copy (e.g.
# "Frasier.1993-bak"). These are never migrated, even when the TMDB fallback
# could resolve them, so a hand-made backup is never clobbered onto a real id dir.
_BACKUP_SUFFIXES = ("-bak", ".bak", "~", ".tmp", ".old")


def _looks_like_backup(name: str) -> bool:
    return name.casefold().endswith(_BACKUP_SUFFIXES)


def _normalize(name: str) -> str:
    """Normalize a show name for tolerant CSV matching.

    Folds case and strips trailing dots/spaces — the two ways a dir name diverges
    from the curated CSV title: harvesters/users vary the case ("ONE PIECE"), and
    Windows silently drops trailing dots from directory names ("S.W.A.T." → the
    on-disk "S.W.A.T"). This is exact-title matching, not fuzzy guessing.
    """
    return sanitize_filename(name).casefold().rstrip(" .")


def load_curated_map(csv_path) -> dict[str, str]:
    """Return ``{sanitize_filename(name): str(tmdb_id)}`` from ``curated_shows.csv``.

    Keyed by the sanitized name so lookups match the on-disk dir name (which the
    writer produced via ``sanitize_filename``). Rows without a numeric ``tmdb_id``
    are skipped. Missing/unreadable file → empty map (TMDB fallback still applies).
    """
    p = Path(csv_path)
    if not p.exists():
        logger.warning(f"Curated show list not found: {p} (TMDB fallback only)")
        return {}
    text = p.read_text(encoding="utf-8-sig")
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        tid = (row.get("tmdb_id") or "").strip()
        name = (row.get("name") or "").strip()
        if name and tid.isdigit():
            out[sanitize_filename(name)] = tid
    return out


def resolve_dir(
    dir_name: str,
    curated_map: dict[str, str],
    norm_map: dict[str, str],
    *,
    treat_as_name,
    fetch_id_fn,
) -> tuple[str | None, str]:
    """Classify a ``data/<dir_name>/`` directory.

    Returns ``(tmdb_id_str_or_None, classification)`` where classification is one
    of ``"csv"`` / ``"tmdb"`` (resolved → migrate to that id), ``"already_id"``
    (numeric dir already in the new scheme → skip), ``"backup"`` (a manual backup
    dir → leave in place), ``"ambiguous"`` (numeric dir that is also a curated show
    name → report, leave in place), or ``"unresolved"`` (no known id → report).

    ``norm_map`` is ``curated_map`` re-keyed by ``_normalize`` for tolerant
    (case / trailing-dot) matching; it is consulted only after an exact miss.
    """
    if _looks_like_backup(dir_name):
        return None, "backup"

    forced = dir_name in treat_as_name
    if dir_name.isdigit() and not forced:
        # Numeric AND a curated show name (e.g. "24") → genuinely ambiguous.
        # Numeric and not a show name (e.g. "1396") → already the tmdb_id scheme.
        return (None, "ambiguous") if dir_name in curated_map else (None, "already_id")

    tid = curated_map.get(sanitize_filename(dir_name))
    if tid:
        return str(tid), "csv"
    norm_hit = norm_map.get(_normalize(dir_name))
    if norm_hit:
        return str(norm_hit), "csv"

    fetched = fetch_id_fn(dir_name)
    if fetched:
        return str(fetched), "tmdb"
    return None, "unresolved"


def _relocate(legacy_dir: Path, target: Path, *, dry_run: bool, tally: Tally) -> None:
    """Move ``legacy_dir`` to ``target``, merging (union, keep larger) if it exists."""
    files = [f for f in sorted(legacy_dir.iterdir()) if f.is_file()]

    if not target.exists():
        logger.info(f"  migrate: {legacy_dir.name}/ -> {target.name}/ ({len(files)} files)")
        if not dry_run:
            legacy_dir.rename(target)
        tally.migrated += 1
        tally.files_moved += len(files)
        return

    logger.info(f"  merge:   {legacy_dir.name}/ -> {target.name}/ (target exists)")
    tally.merged += 1
    for f in files:
        dest = target / f.name
        if dest.exists():
            if f.stat().st_size > dest.stat().st_size:
                logger.info(f"    keep larger (legacy): {f.name}")
                if not dry_run:
                    os.replace(f, dest)
                tally.files_kept_larger += 1
            else:
                logger.info(f"    drop smaller (legacy): {f.name}")
                if not dry_run:
                    f.unlink()
                tally.files_dropped_smaller += 1
        else:
            # Same filesystem (both under data_dir) and dest doesn't exist, so a
            # plain rename is sufficient — no cross-device copy fallback needed.
            if not dry_run:
                f.rename(dest)
            tally.files_moved += 1

    if dry_run:
        return
    leftover = list(legacy_dir.iterdir())
    if not leftover:
        legacy_dir.rmdir()
    else:
        logger.warning(
            f"  {legacy_dir.name}/: {len(leftover)} unmoved entr(ies) remain; not removing dir"
        )


def migrate_cache(
    data_dir,
    curated_map: dict[str, str],
    *,
    dry_run: bool = True,
    treat_as_name=frozenset(),
    fetch_id_fn=fetch_show_id,
) -> Tally:
    """Migrate every legacy name-keyed dir under ``data_dir`` to the tmdb_id scheme.

    ``treat_as_name`` forces specific (numeric) dir names to be resolved as show
    names rather than skipped as already-id. ``fetch_id_fn`` is the TMDB fallback
    used only for names absent from ``curated_map`` (injected in tests).
    """
    data_dir = Path(data_dir)
    treat_as_name = set(treat_as_name)
    norm_map = {_normalize(k): v for k, v in curated_map.items()}
    tally = Tally()

    for show_dir in sorted(d for d in data_dir.iterdir() if d.is_dir()):
        name = show_dir.name
        tid, cls = resolve_dir(
            name, curated_map, norm_map, treat_as_name=treat_as_name, fetch_id_fn=fetch_id_fn
        )
        if cls == "already_id":
            tally.skipped_already_id += 1
            continue
        if cls == "backup":
            logger.info(f"  backup:  {name}/ looks like a manual backup; leaving in place")
            tally.skipped_backup.append(name)
            continue
        if cls == "ambiguous":
            logger.warning(
                f"  ambiguous: {name}/ is numeric but also a curated show name; "
                f"leaving in place (pass --treat-as-name {name} to force)"
            )
            tally.ambiguous.append(name)
            continue
        if cls == "unresolved":
            logger.warning(f"  unresolved: {name}/ — no tmdb_id found; leaving in place")
            tally.unresolved.append(name)
            continue

        target = data_dir / str(tid)
        if target == show_dir:  # defensive: name already equals its id
            tally.skipped_already_id += 1
            continue
        try:
            _relocate(show_dir, target, dry_run=dry_run, tally=tally)
        except OSError as e:
            # One bad move (locked SRT, disk full, cross-device edge) must not
            # abort the whole run. Record it and continue — the migration is
            # idempotent, so re-running with --apply finishes the rest.
            logger.error(f"  failed: {name}/ -> {tid}/ — {e}", exc_info=True)
            tally.failed.append(name)

    return tally


def _resolve_cache_data_dir(override: str | None) -> Path:
    """Pick ``<cache>/data``: ``--cache-dir`` wins, else ``AppConfig``."""
    if override:
        return Path(override).expanduser() / "data"
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    return Path(config.subtitles_cache_path).expanduser() / "data"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the subtitle SRT cache from name-keyed to tmdb_id-keyed dirs"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="",
        help="Override the subtitles cache root (default: AppConfig.subtitles_cache_path)",
    )
    parser.add_argument(
        "--curated-csv",
        type=str,
        default=str(_DEFAULT_CURATED_CSV),
        help="Path to the curated show list used for offline name->tmdb_id resolution",
    )
    parser.add_argument(
        "--treat-as-name",
        type=str,
        default="",
        help=(
            "Comma-separated dir names to resolve as show names rather than skip as "
            "already-id (e.g. '24' — the show, not tmdb_id 24)"
        ),
    )
    parser.add_argument(
        "--tmdb-fallback",
        action="store_true",
        help=(
            "Resolve names absent from the curated CSV via a TMDB search "
            "(tmdb_client.fetch_show_id). Off by default: the default run only migrates "
            "deterministic CSV matches and reports the rest, so a fuzzy search can't "
            "misfile an unrecognized dir."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move files (default: dry-run, reports the plan without changes)",
    )
    args = parser.parse_args()

    data_dir = _resolve_cache_data_dir(args.cache_dir or None)
    if not data_dir.exists():
        logger.error(f"Subtitle cache data dir not found: {data_dir}")
        return 1

    curated_map = load_curated_map(args.curated_csv)
    treat_as_name = {s.strip() for s in args.treat_as_name.split(",") if s.strip()}
    dry_run = not args.apply

    # Default to deterministic CSV matching only; the fuzzy TMDB search is opt-in
    # so an unrecognized dir is reported, never misfiled.
    fetch_id_fn = fetch_show_id if args.tmdb_fallback else (lambda _name: None)

    mode = "DRY RUN" if dry_run else "APPLY"
    logger.info(
        f"[{mode}] Migrating SRT cache under {data_dir} "
        f"({len(curated_map)} curated shows; tmdb_fallback={args.tmdb_fallback}; "
        f"treat-as-name={sorted(treat_as_name) or 'none'})"
    )

    tally = migrate_cache(
        data_dir,
        curated_map,
        dry_run=dry_run,
        treat_as_name=treat_as_name,
        fetch_id_fn=fetch_id_fn,
    )

    logger.info(
        f"Done [{mode}]. migrated={tally.migrated} merged={tally.merged} "
        f"files_moved={tally.files_moved} kept_larger={tally.files_kept_larger} "
        f"dropped_smaller={tally.files_dropped_smaller} "
        f"skipped_already_id={tally.skipped_already_id} "
        f"skipped_backup={len(tally.skipped_backup)} "
        f"ambiguous={len(tally.ambiguous)} unresolved={len(tally.unresolved)} "
        f"failed={len(tally.failed)}"
    )
    if tally.skipped_backup:
        logger.warning(f"Backup dirs (left in place): {sorted(tally.skipped_backup)}")
    if tally.failed:
        logger.error(f"Failed to move (re-run with --apply to retry): {sorted(tally.failed)}")
    if tally.ambiguous:
        logger.warning(f"Ambiguous (left in place): {sorted(tally.ambiguous)}")
    if tally.unresolved:
        logger.warning(f"Unresolved (left in place): {sorted(tally.unresolved)}")
        if not args.tmdb_fallback:
            logger.info("Re-run with --tmdb-fallback to resolve unrecognized names via TMDB.")
    if dry_run and (tally.migrated or tally.merged):
        logger.info("Dry run only — re-run with --apply to perform the migration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

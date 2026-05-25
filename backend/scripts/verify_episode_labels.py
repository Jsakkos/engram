"""Verify TV episode labels against Engram's audio matcher.

Point this at an existing library folder (a single ``Season NN`` folder or a whole
show folder) and it checks whether each ``.mkv`` is named with the correct episode
by transcribing a few audio chunks and matching them against reference subtitles —
the same matcher the live pipeline uses. Mislabeled / out-of-order files are
flagged, and can optionally be renamed (collision-safe, with an undo log).

Usage (from ``backend/``):

    uv run python scripts/verify_episode_labels.py "C:\\Media\\TV\\Show\\Season 03"
    uv run python scripts/verify_episode_labels.py "C:\\Media\\TV\\Show" --show "Show"
    uv run python scripts/verify_episode_labels.py "...\\Season 03" --apply
    uv run python scripts/verify_episode_labels.py --undo "...\\engram_label_undo_*.json"

Default is a dry run: it reports and proposes renames but changes nothing. Pass
``--apply`` to actually rename.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Allow ``import app...`` when run as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252; force UTF-8 so table glyphs (→, —, ✓) and
# show titles with accents don't raise UnicodeEncodeError when piped/redirected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        # Stream is already UTF-8, or is a wrapped/captured stream (e.g. pytest,
        # a pipe) that doesn't support reconfigure() — safe to leave as-is.
        pass

TEMP_SUFFIX = ".engram-tmp"
DEFAULT_MIN_CONFIDENCE = 0.7


class Status:
    OK = "OK"
    MISMATCH = "MISMATCH"
    LOW_CONF = "LOW_CONF"
    NO_MATCH = "NO_MATCH"
    UNPARSEABLE = "UNPARSEABLE"


_CLAIM_PATTERNS = (
    r"S(\d+)E(\d+)",
    r"(\d+)x(\d+)",
    r"Season\s*(\d+)\s*Episode\s*(\d+)",
)
_SEASON_DIR_RE = re.compile(r"^season\s*0*(\d+)$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Pure logic (unit-tested; no I/O, no matcher)
# --------------------------------------------------------------------------- #


def parse_claim(name: str) -> tuple[int, int] | None:
    """Parse the (season, episode) a filename *claims* to be."""
    for pattern in _CLAIM_PATTERNS:
        m = re.search(pattern, name, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def compute_target_name(name: str, season: int, episode: int) -> str:
    """Rewrite the episode code in ``name`` to ``season``/``episode``.

    Preserves the original style (``SxxEyy`` vs ``NxNN``) and zero-padding widths.
    """
    m = re.search(r"S(\d+)E(\d+)", name, re.IGNORECASE)
    if m:
        sw, ew = len(m.group(1)), len(m.group(2))
        repl = f"S{season:0{sw}d}E{episode:0{ew}d}"
        return name[: m.start()] + repl + name[m.end() :]

    m = re.search(r"(\d+)x(\d+)", name)
    if m:
        ew = len(m.group(2))
        repl = f"{season}x{episode:0{ew}d}"
        return name[: m.start()] + repl + name[m.end() :]

    m = re.search(r"(Season\s*)(\d+)(\s*Episode\s*)(\d+)", name, re.IGNORECASE)
    if m:
        repl = f"{m.group(1)}{season}{m.group(3)}{episode}"
        return name[: m.start()] + repl + name[m.end() :]

    raise ValueError(f"No episode code to rewrite in {name!r}")


def classify(
    claim: tuple[int, int] | None,
    predicted: tuple[int, int] | None,
    confidence: float,
    threshold: float,
) -> str:
    """Bucket one file by comparing its claimed label to the matcher's guess."""
    if predicted is None:
        return Status.NO_MATCH
    if claim is None:
        return Status.UNPARSEABLE
    if confidence < threshold:
        return Status.LOW_CONF
    return Status.OK if predicted == claim else Status.MISMATCH


@dataclass
class SeasonTarget:
    season: int | None
    directory: Path


@dataclass
class ScopePlan:
    mode: str  # "season" | "show"
    show_name: str
    targets: list[SeasonTarget]


def detect_scope(path: Path) -> ScopePlan:
    """Decide whether ``path`` is a single season folder or a whole show folder."""
    season_subdirs = [
        d for d in sorted(path.iterdir()) if d.is_dir() and _SEASON_DIR_RE.match(d.name)
    ]
    if season_subdirs:
        targets = [
            SeasonTarget(int(_SEASON_DIR_RE.match(d.name).group(1)), d) for d in season_subdirs
        ]
        return ScopePlan(mode="show", show_name=path.name, targets=targets)

    # No season subdirs: treat this folder as a single season.
    season = _season_from_dir_name(path.name)
    if season is None:
        season = _dominant_season([p.name for p in path.glob("*.mkv")])
    return ScopePlan(
        mode="season",
        show_name=path.parent.name,
        targets=[SeasonTarget(season, path)],
    )


def _season_from_dir_name(name: str) -> int | None:
    m = _SEASON_DIR_RE.match(name)
    return int(m.group(1)) if m else None


def _dominant_season(filenames: list[str]) -> int | None:
    counts: dict[int, int] = {}
    for fn in filenames:
        claim = parse_claim(fn)
        if claim:
            counts[claim[0]] = counts.get(claim[0], 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def expand_sidecars(mapping: dict[Path, Path], listing: list[Path]) -> dict[Path, Path]:
    """Extend an mkv rename mapping to carry along same-stem sidecar files."""
    result = dict(mapping)
    for src, dst in mapping.items():
        prefix = src.stem + "."
        for f in listing:
            if f == src or f in result:
                continue
            if f.name.startswith(prefix):
                new_name = dst.stem + f.name[len(src.stem) :]
                result[f] = f.with_name(new_name)
    return result


@dataclass
class RenamePlan:
    steps: list[tuple[Path, Path]]
    conflicts: list[Path]


def plan_two_phase(mapping: dict[Path, Path], existing: set[Path]) -> RenamePlan:
    """Order renames so cyclic swaps never clobber.

    Every participant is first moved to a unique temp name, then from the temp to
    its final name. A target that points at an existing file which is *not* itself
    being moved is a conflict (would destroy a non-participant) and aborts.
    """
    sources = set(mapping.keys())
    conflicts = [dst for src, dst in mapping.items() if dst in existing and dst not in sources]
    if conflicts:
        return RenamePlan(steps=[], conflicts=conflicts)

    used = set(existing)
    temps: dict[Path, Path] = {}
    for src in mapping:
        temp = src.with_name(src.name + TEMP_SUFFIX)
        counter = 1
        while temp in used:
            temp = src.with_name(f"{src.name}{TEMP_SUFFIX}.{counter}")
            counter += 1
        used.add(temp)
        temps[src] = temp

    steps = [(src, temps[src]) for src in mapping]
    steps += [(temps[src], dst) for src, dst in mapping.items()]
    return RenamePlan(steps=steps, conflicts=[])


# --------------------------------------------------------------------------- #
# Matcher orchestration (lazy app imports)
# --------------------------------------------------------------------------- #


@dataclass
class FileResult:
    path: Path
    claim: tuple[int, int] | None
    predicted: tuple[int, int] | None
    confidence: float
    status: str = ""
    runner_ups: list[dict] = field(default_factory=list)
    target_name: str | None = None


def _ensure_references(
    show_name: str, season: int, full_references: bool = False
) -> tuple[bool, str]:
    """Make sure reference subtitles/vectors exist for show+season.

    ``full_references`` bypasses the (possibly sparse) precomputed vector cache and
    downloads real subtitles for the whole season — needed for reliable
    verification when the precomputed cache covers only some episodes.
    """
    # Import is intentionally outside the try: a missing app package is a setup
    # error that should surface as a traceback, not a per-season "unverifiable".
    from app.matcher.testing_service import download_subtitles

    try:
        result = download_subtitles(show_name, season, use_precomputed=not full_references)
    except Exception as e:
        # Broad on purpose: a TMDB miss (ValueError), a provider network error, or
        # any other download failure should mark THIS season unverifiable and let
        # the run continue to other seasons (and still write the CSV) rather than
        # abort everything. Surface the exception type so unexpected failures are
        # still debuggable from the printed message.
        return False, f"{type(e).__name__}: {e}"

    episodes = result.get("episodes", [])
    ok = sum(1 for ep in episodes if ep["status"] in ("downloaded", "cached", "precomputed"))
    if ok == 0:
        return False, "no reference subtitles found"
    return True, f"{ok}/{len(episodes)} references ready"


async def _match_season(
    files: list[Path],
    show_name: str,
    season: int,
    threshold: float,
    num_points: int | None,
) -> list[FileResult]:
    from app.core.curator import EpisodeCurator

    curator = EpisodeCurator()
    results: list[FileResult] = []
    for f in sorted(files):
        claim = parse_claim(f.name)
        match = await curator.match_single_file(f, show_name, season, num_points=num_points)
        predicted = parse_claim(match.episode_code) if match.episode_code else None
        # match_single_file falls back to filename parsing when the matcher
        # produces nothing; treat that as "no real match" for verification.
        details = match.match_details or {}
        is_real_match = bool(details) and "vote_count" in details
        if not is_real_match and match.confidence <= 0.3:
            predicted = None
        results.append(
            FileResult(
                path=f,
                claim=claim,
                predicted=predicted,
                confidence=match.confidence,
                status=classify(claim, predicted, match.confidence, threshold),
                runner_ups=details.get("runner_ups", []) or [],
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Rename application + undo
# --------------------------------------------------------------------------- #


def build_apply_plan(
    results: list[FileResult], threshold: float
) -> tuple[RenamePlan | None, dict[Path, Path]]:
    """Build a collision-safe plan from MISMATCH results above the threshold."""
    net: dict[Path, Path] = {}
    for r in results:
        if r.status == Status.MISMATCH and r.confidence >= threshold and r.predicted:
            target = r.path.with_name(compute_target_name(r.path.name, *r.predicted))
            net[r.path] = target
            r.target_name = target.name
    if not net:
        return None, {}

    # net is non-empty here (guarded above) and, since build_apply_plan runs once
    # per SeasonTarget, every MISMATCH file shares the same season directory.
    directory = next(iter(net)).parent
    listing = [p for p in directory.iterdir() if p.is_file()]
    full = expand_sidecars(net, listing)
    plan = plan_two_phase(full, set(listing))
    return plan, net


def _write_undo_log(plan: RenamePlan, directory: Path) -> Path:
    """Persist the full rename plan so it can be replayed/reverted."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = directory / f"engram_label_undo_{ts}.json"
    log_path.write_text(
        json.dumps(
            {
                "created": ts,
                "steps": [[str(s), str(d)] for s, d in plan.steps],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return log_path


def execute_plan(plan: RenamePlan, directory: Path) -> Path:
    """Write the undo log FIRST, then run the rename steps; returns the log path.

    Logging before mutating is deliberate: if a ``rename`` fails midway (disk full,
    permission error, killed process), the log already on disk lets ``--undo``
    recover any files left stranded under ``.engram-tmp``.
    """
    log_path = _write_undo_log(plan, directory)
    for src, dst in plan.steps:
        src.rename(dst)
    return log_path


def undo_from_log(log_path: Path) -> int:
    """Reverse a previous --apply run. Returns the number of files moved back."""
    data = json.loads(log_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError(f"Not a valid undo log (missing 'steps'): {log_path}")
    steps = [(Path(s), Path(d)) for s, d in data["steps"]]
    moved = 0
    for src, dst in reversed(steps):
        if dst.exists():
            dst.rename(src)
            moved += 1
    return moved


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def _fmt_ep(ep: tuple[int, int] | None) -> str:
    return f"S{ep[0]:02d}E{ep[1]:02d}" if ep else "—"


def _fmt_runner_ups(runner_ups: list[dict]) -> str:
    parts = []
    for ru in runner_ups[:3]:
        code = ru.get("episode", "?")
        conf = ru.get("confidence")
        parts.append(f"{code}({conf:.2f})" if isinstance(conf, (int, float)) else str(code))
    return ", ".join(parts)


def render_results(show_name: str, season: int, results: list[FileResult]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        color = {
            Status.OK: "green",
            Status.MISMATCH: "bold red",
            Status.LOW_CONF: "yellow",
            Status.NO_MATCH: "dim",
            Status.UNPARSEABLE: "magenta",
        }
        table = Table(title=f"{show_name} — Season {season:02d}", title_style="bold")
        table.add_column("File")
        table.add_column("Claimed")
        table.add_column("Matched")
        table.add_column("Conf", justify="right")
        table.add_column("Status")
        table.add_column("Rename →")
        for r in results:
            rename = r.target_name or ""
            table.add_row(
                r.path.name,
                _fmt_ep(r.claim),
                _fmt_ep(r.predicted),
                f"{r.confidence:.0%}",
                f"[{color.get(r.status, 'white')}]{r.status}[/]",
                rename,
            )
        console.print(table)
    except ImportError:
        print(f"\n{show_name} — Season {season:02d}")
        for r in results:
            rename = f"  ->  {r.target_name}" if r.target_name else ""
            print(
                f"  {r.status:11} {_fmt_ep(r.claim):>7} -> {_fmt_ep(r.predicted):>7}"
                f"  {r.confidence:5.0%}  {r.path.name}{rename}"
            )


def _summary(results: list[FileResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def write_csv(csv_path: Path, all_results: list[tuple[int, FileResult]]) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "season",
                "file",
                "claimed",
                "matched",
                "confidence",
                "status",
                "proposed_rename",
                "runner_ups",
            ]
        )
        for season, r in all_results:
            writer.writerow(
                [
                    season,
                    str(r.path),
                    _fmt_ep(r.claim),
                    _fmt_ep(r.predicted),
                    f"{r.confidence:.4f}",
                    r.status,
                    r.target_name or "",
                    _fmt_runner_ups(r.runner_ups),
                ]
            )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def default_csv_path(plan: ScopePlan, override: str | None) -> Path:
    """Where to write the CSV: explicit override, else show root (show mode) or
    the season directory (season mode)."""
    if override:
        return Path(override)
    if plan.mode == "show":
        # targets are Season subdirs of the show root; write to the root itself.
        return plan.targets[0].directory.parent / "engram_label_check.csv"
    return plan.targets[0].directory / "engram_label_check.csv"


def _process(plan: ScopePlan, show_name: str, args: argparse.Namespace) -> None:
    all_results: list[tuple[int, FileResult]] = []

    for target in plan.targets:
        season = args.season if (plan.mode == "season" and args.season) else target.season
        if season is None:
            print(f"! Could not determine season for {target.directory} — pass --season N")
            continue

        files = sorted(target.directory.glob("*.mkv"))
        if not files:
            print(f"! No .mkv files in {target.directory}")
            continue

        print(f"\n=> {show_name} Season {season:02d}: {len(files)} file(s). Preparing references…")
        ok, note = _ensure_references(show_name, season, args.full_references)
        if not ok:
            print(f"! Reference setup failed ({note}). Marking season unverifiable.")
            results = [
                FileResult(f, parse_claim(f.name), None, 0.0, Status.NO_MATCH) for f in files
            ]
        else:
            print(f"   {note}. Matching (this transcribes audio — a few seconds per file)…")
            results = asyncio.run(
                _match_season(files, show_name, season, args.min_confidence, args.num_points)
            )

        plan_obj, net = build_apply_plan(results, args.min_confidence)
        # Display is best-effort; never let a rendering hiccup lose the CSV data.
        try:
            render_results(show_name, season, results)
        except Exception as e:
            print(f"   (table render skipped: {e})")

        counts = _summary(results)
        print("   " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

        if net:
            if plan_obj.conflicts:
                print(
                    "   ! Unsafe rename: would overwrite non-participant file(s): "
                    + ", ".join(p.name for p in plan_obj.conflicts)
                )
                print("     Skipping rename for this season; resolve manually.")
            elif args.apply:
                log = execute_plan(plan_obj, target.directory)
                print(f'   ✓ Renamed {len(net)} file(s). Undo: --undo "{log}"')
            else:
                print(f"   (dry run) Would rename {len(net)} file(s). Re-run with --apply.")

        all_results.extend((season, r) for r in results)

    if all_results:
        csv_path = default_csv_path(plan, args.csv)
        write_csv(csv_path, all_results)
        print(f"\nCSV written: {csv_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify TV episode labels with Engram's matcher.")
    parser.add_argument("path", nargs="?", help="season folder or show folder")
    parser.add_argument("--show", help="override inferred show name")
    parser.add_argument("--season", type=int, help="override inferred season (season-folder mode)")
    parser.add_argument("--apply", action="store_true", help="perform renames (default: dry run)")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help=f"confidence gate for auto-rename (default {DEFAULT_MIN_CONFIDENCE})",
    )
    parser.add_argument("--num-points", type=int, help="denser audio scan for accuracy")
    parser.add_argument(
        "--full-references",
        action="store_true",
        help="bypass the precomputed cache and download real subtitles for the whole "
        "season (uses OpenSubtitles quota; needed when the cache covers only some episodes)",
    )
    parser.add_argument("--csv", help="CSV output path (default: alongside target)")
    parser.add_argument("--undo", help="revert a previous --apply run from its undo log")
    args = parser.parse_args(argv)

    if args.undo:
        undo_path = Path(args.undo)
        if not undo_path.is_file():
            parser.error(f"undo log not found: {undo_path}")
        moved = undo_from_log(undo_path)
        print(f"Reverted {moved} file(s) from {args.undo}")
        return 0

    if not args.path:
        parser.error("path is required (or use --undo)")

    root = Path(args.path).expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    plan = detect_scope(root)
    show_name = args.show or plan.show_name
    print(
        f"Scope: {plan.mode}  Show: {show_name}  Seasons: "
        + ", ".join(str(t.season) for t in plan.targets)
    )
    _process(plan, show_name, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

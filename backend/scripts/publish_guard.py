"""Refuse to publish a subtitle cache that is materially smaller than the live one.

The nightly harvest publishes unattended. A regression that resolves zero shows,
or a corpus directory that failed to mount, still produces a structurally VALID
tarball: verification passes, the manifest is well-formed, the upload succeeds,
and every Engram install silently downgrades to a smaller cache. Only a size
comparison against what is already published catches that class of failure.

Shows and episodes are guarded independently because they fail independently: a
TMDB-resolution regression collapses the show count while barely moving the
episode total, and a truncated harvest does the reverse.

The guard runs unattended at 02:00 and its caller refuses to upload on ANY
non-zero exit, so every ambiguous state resolves toward blocking. In particular
"the guard could not reach GitHub" is exit 2, never a silent allow: an outage
must not turn into an unvalidated publish.

Usage (from backend/):
    uv run python scripts/publish_guard.py --candidate manifest.json
    uv run python scripts/publish_guard.py --candidate manifest.json --allow-shrink

Exit codes:
    0  Publishing is allowed.
    1  Publishing is blocked (shrink beyond tolerance, empty candidate, or an
       untrustworthy published baseline).
    2  The guard could not decide (unreadable or malformed manifest, gh failure,
       network failure, timeout, or any unexpected error).
"""

import argparse
import enum
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAG = "subtitle-cache-latest"
# Must match the repo the publishing wrapper's `gh release upload --repo`
# targets, or the guard validates against a baseline the wrapper never wrote.
DEFAULT_REPO = "Jsakkos/engram"
# 2% absorbs ordinary night-to-night churn: a subtitle provider timing out on a
# handful of episodes, or a show temporarily failing TMDB resolution. Anything
# larger is a defect, not weather.
DEFAULT_TOLERANCE = 0.02

# gh writes these to stderr when the release or the asset genuinely does not
# exist. Everything else (auth, DNS, 5xx, rate limit) means we could not look.
# A bare "not found" is deliberately excluded: gh also emits it for expired or
# under-scoped auth ("HTTP 404: Not Found (https://api.github.com/repos/...)",
# "gh: Not Found (HTTP 404)"), and reading that as ABSENT turns a broken
# credential into a silent, unvalidated publish.
_ABSENT_MARKERS = ("release not found", "no assets match")

GH_TIMEOUT_SECONDS = 60


class ManifestError(ValueError):
    """A manifest parsed as JSON but does not have the expected structure."""


class ShrinkVerdict(enum.Enum):
    """Why the guard reached its decision. Values are log-facing strings."""

    GROWTH = "growth"
    WITHIN_TOLERANCE = "within-tolerance"
    EPISODES_SHRANK = "episodes-shrank"
    SHOWS_SHRANK = "shows-shrank"
    NO_BASELINE = "no-baseline"
    EMPTY_CANDIDATE = "empty-candidate"
    BASELINE_UNUSABLE = "baseline-unusable"


# Verdicts --allow-shrink must never override. The flag exists for deliberate
# corpus pruning, which always leaves a non-empty cache and always compares
# against a trustworthy baseline.
NON_OVERRIDABLE = frozenset({ShrinkVerdict.EMPTY_CANDIDATE, ShrinkVerdict.BASELINE_UNUSABLE})


class BaselineStatus(enum.Enum):
    """Whether we know the size of the currently published cache."""

    RETRIEVED = "retrieved"
    ABSENT = "absent"  # No release / no manifest asset. First publish: allow.
    UNAVAILABLE = "unavailable"  # We could not look. Undecidable: exit 2.


@dataclass(frozen=True)
class BaselineOutcome:
    status: BaselineStatus
    totals: tuple[int, int] | None = None
    detail: str = ""


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    verdict: ShrinkVerdict
    reason: str


def manifest_totals(manifest: object) -> tuple[int, int]:
    """Return ``(show_count, episode_count)`` for a release manifest.

    Validates rather than coerces: a manifest whose ``shows`` is null, a list,
    or holds non-integer counts is damage, and quietly reading it as a small
    number would hand the guard a floor of zero.
    """
    if not isinstance(manifest, Mapping):
        raise ManifestError(f"manifest is {type(manifest).__name__}, expected an object")

    if "shows" not in manifest:
        raise ManifestError("manifest has no 'shows' key")
    shows = manifest["shows"]
    if not isinstance(shows, Mapping):
        raise ManifestError(f"manifest 'shows' is {type(shows).__name__}, expected an object")

    episodes = 0
    for key, entry in shows.items():
        if not isinstance(entry, Mapping):
            raise ManifestError(
                f"manifest show {key!r} is {type(entry).__name__}, expected an object"
            )
        counts = entry.get("episode_counts")
        if counts is None:
            continue
        if not isinstance(counts, Mapping):
            raise ManifestError(
                f"manifest show {key!r} has 'episode_counts' of "
                f"{type(counts).__name__}, expected an object"
            )
        for season, count in counts.items():
            # bool is an int subclass; a stray flag must not read as 1 episode.
            if isinstance(count, bool) or not isinstance(count, int):
                raise ManifestError(
                    f"manifest show {key!r} season {season!r} has a non-integer "
                    f"episode count {count!r}"
                )
            episodes += count
    return len(shows), episodes


def verdict_for(
    candidate: tuple[int, int],
    published: tuple[int, int] | None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> GuardResult:
    """Decide whether ``candidate`` may replace ``published``.

    ``tolerance`` is the fraction a count may fall by and still be accepted; a
    provider dropping a handful of episodes between runs is normal churn, a 20%
    collapse is a defect.
    """
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError(f"tolerance must be in [0, 1], got {tolerance}")

    cand_shows, cand_eps = candidate
    if cand_shows == 0 or cand_eps == 0:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.EMPTY_CANDIDATE,
            reason=f"candidate is empty ({cand_shows} shows, {cand_eps} episodes)",
        )

    if published is None:
        return GuardResult(
            allowed=True,
            verdict=ShrinkVerdict.NO_BASELINE,
            reason=(
                f"no published baseline to compare against; allowing "
                f"{cand_shows} shows / {cand_eps} episodes"
            ),
        )

    pub_shows, pub_eps = published
    if pub_shows == 0 or pub_eps == 0:
        # Every floor would be 0.0 and the guard would wave through any shrink
        # at all while printing a cheerful "growth". Block instead.
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.BASELINE_UNUSABLE,
            reason=(
                f"the published manifest totals {pub_shows} shows / {pub_eps} "
                f"episodes and cannot be trusted as a baseline; an operator "
                f"should inspect the release before publishing again"
            ),
        )

    floor_shows = pub_shows * (1.0 - tolerance)
    floor_eps = pub_eps * (1.0 - tolerance)
    tol_pct = f"{tolerance * 100:.1f}%"

    if cand_shows < floor_shows:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.SHOWS_SHRANK,
            reason=(
                f"show count fell from {pub_shows} to {cand_shows}, below the "
                f"{tol_pct} tolerance floor of {floor_shows:.1f}"
            ),
        )
    if cand_eps < floor_eps:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.EPISODES_SHRANK,
            reason=(
                f"episode count fell from {pub_eps} to {cand_eps}, below the "
                f"{tol_pct} tolerance floor of {floor_eps:.1f}"
            ),
        )

    if cand_shows >= pub_shows and cand_eps >= pub_eps:
        return GuardResult(
            allowed=True,
            verdict=ShrinkVerdict.GROWTH,
            reason=(f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes"),
        )
    return GuardResult(
        allowed=True,
        verdict=ShrinkVerdict.WITHIN_TOLERANCE,
        reason=(
            f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes "
            f"(inside the {tol_pct} tolerance)"
        ),
    )


def classify_gh_failure(stderr: str) -> BaselineStatus:
    """Map a failed ``gh release download`` to absent-vs-unavailable.

    "The release or its manifest asset does not exist" is a legitimate first
    publish. Auth failures, DNS failures, 5xx and rate limits mean we simply
    could not look, and those must never be read as "no baseline".
    """
    lowered = (stderr or "").lower()
    if any(marker in lowered for marker in _ABSENT_MARKERS):
        return BaselineStatus.ABSENT
    return BaselineStatus.UNAVAILABLE


def fetch_published_totals(tag: str, repo: str = DEFAULT_REPO) -> BaselineOutcome:
    """Download the live release manifest and total it.

    Returns an explicit outcome so the caller can tell "there is genuinely no
    baseline" (allow) from "we could not find out" (exit 2). ``repo`` must
    match the repo the publishing wrapper uploads to, or this validates
    against a baseline the wrapper never wrote.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    tag,
                    "--repo",
                    repo,
                    "--pattern",
                    "manifest.json",
                    "--dir",
                    tmp,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            status = classify_gh_failure(stderr)
            detail = f"gh release download exited {exc.returncode}: {stderr or '(no stderr)'}"
            return BaselineOutcome(status, None, detail)
        except FileNotFoundError:
            return BaselineOutcome(
                BaselineStatus.UNAVAILABLE,
                None,
                "the gh CLI is not installed or not on PATH",
            )
        except subprocess.TimeoutExpired:
            return BaselineOutcome(
                BaselineStatus.UNAVAILABLE,
                None,
                f"gh release download timed out after {GH_TIMEOUT_SECONDS}s",
            )
        except OSError as exc:
            return BaselineOutcome(BaselineStatus.UNAVAILABLE, None, f"could not run gh: {exc}")

        path = Path(tmp) / "manifest.json"
        if not path.is_file():
            return BaselineOutcome(
                BaselineStatus.ABSENT, None, "the release has no manifest.json asset"
            )
        try:
            totals = manifest_totals(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ManifestError) as exc:
            return BaselineOutcome(
                BaselineStatus.UNAVAILABLE,
                None,
                f"the published manifest could not be read: {exc}",
            )
        return BaselineOutcome(BaselineStatus.RETRIEVED, totals)


# 1.0 would zero every floor and wave through any shrink while still exiting
# 0; 0.5 is already a wildly permissive night-to-night tolerance, so the CLI
# entry point caps here well short of "disables the guard". verdict_for's own
# contract (tested separately) keeps accepting the full [0, 1] range for
# callers that aren't this unattended CLI.
_MAX_CLI_TOLERANCE = 0.5


def _tolerance_arg(raw: str) -> float:
    """argparse ``type=`` for --tolerance, so a bad value is a usage error."""
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number, got {raw!r}") from None
    if not 0.0 <= value <= _MAX_CLI_TOLERANCE:
        raise argparse.ArgumentTypeError(
            f"must be a fraction in [0, {_MAX_CLI_TOLERANCE}], got {value}"
        )
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guard against publishing a shrunken cache")
    parser.add_argument("--candidate", required=True, help="Path to the candidate manifest.json")
    parser.add_argument("--cache-tag", default=DEFAULT_TAG, help="Release tag to compare against")
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=(
            f"GitHub repo (owner/name) to read the baseline release from "
            f"(default: {DEFAULT_REPO}). Must match the repo the publishing "
            f"wrapper's `gh release upload --repo` targets, or the guard "
            f"validates against a baseline the wrapper never wrote."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=_tolerance_arg,
        default=DEFAULT_TOLERANCE,
        help=f"Fraction a count may fall and still publish (default: {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help=(
            "Publish despite a shrink verdict (deliberate corpus pruning). Does NOT "
            "override an empty candidate or an untrustworthy published baseline."
        ),
    )
    return parser


def _run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        candidate = manifest_totals(json.loads(Path(args.candidate).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"publish-guard: cannot read candidate manifest {args.candidate}: {exc}")
        return 2

    outcome = fetch_published_totals(args.cache_tag, args.repo)
    if outcome.status is BaselineStatus.UNAVAILABLE:
        print(
            f"publish-guard: undecidable: could not read the published baseline: {outcome.detail}"
        )
        return 2

    published = outcome.totals if outcome.status is BaselineStatus.RETRIEVED else None
    result = verdict_for(candidate, published, args.tolerance)
    print(f"publish-guard: {result.verdict.value}: {result.reason}")

    if result.allowed:
        return 0
    if args.allow_shrink and result.verdict not in NON_OVERRIDABLE:
        print("publish-guard: --allow-shrink set; publishing anyway")
        return 0
    if args.allow_shrink:
        print(f"publish-guard: --allow-shrink does not override {result.verdict.value}; blocking")
    return 1


def main() -> int:
    """Wrap the guard so no unexpected exception can escape as exit 1.

    A traceback would leave the shell with status 1, which the nightly wrapper
    reads as a clean, deliberate block rather than "the guard broke".
    """
    try:
        return _run()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate top-level backstop
        print(f"publish-guard: undecidable: unexpected {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

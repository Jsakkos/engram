# Subtitle Cache Server Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move nightly subtitle-cache harvesting off the laptop and onto the Ubuntu server (`jsakkos@192.168.1.122`), running unattended on a systemd user timer that publishes a complete, never-shrinking cache to the rolling `subtitle-cache-latest` release.

**Architecture:** Three repo-committed artifacts plus a server runbook. A Python `publish_guard.py` (unit-tested) refuses to publish a candidate cache that is materially smaller than the one already published. A `harvest.sh` wrapper sequences harvest → pack → guard → publish, branching on the build script's documented exit-code contract. A systemd user service + timer runs the wrapper daily with journald logging. The cutover moves the 1.7 GB SRT corpus and the coverage database to the server so it never re-harvests what the laptop already holds, then retires the laptop harvester because OpenSubtitles quota is per-account and only one machine may harvest.

**Tech Stack:** Python 3.12 (server) / 3.11 (repo target), `uv`, bash, systemd user units, `gh` CLI, `rsync` over SSH, pytest.

---

## Background

Full diagnosis and design: `docs/superpowers/specs/2026-08-31-subtitle-cache-expansion-design.md` (sections 3 and 4). Phase 1 (harvester repair) shipped in PRs #631, #635, #636 and the poisoned-coverage purge has been applied. This plan is Phase 3. Phase 2 (English-only curation) is independent and not required for this plan.

### Server facts (probed 2026-09-08, do not re-derive)

| Fact | Value |
|---|---|
| Host | `jsakkos@192.168.1.122`, hostname `pve-docker` |
| SSH | Passwordless key auth works from the laptop (`BatchMode=yes` succeeds) |
| Python | 3.12.3 at `/usr/bin/python3` |
| `uv` | **Absent**; Task 5 installs it |
| `gh` | `/usr/bin/gh`, authenticated as `Jsakkos`, scopes `gist, read:org, repo` (`repo` is sufficient for release uploads) |
| `rsync`, `git` | Present |
| Checkout | `~/engram` at `v0.8.1-1-g63a94a7`, remote `https://github.com/Jsakkos/engram.git` |
| `~/.engram` | Exists; `~/.engram/cache` does **not** |
| Disk | 348 GB free on `/` |
| Timezone | `Etc/UTC` |
| sudo | **Requires a password**, so every sudo step is a user step, not an agent step |
| systemd | `systemctl --user` works; `Linger=no`, so timers will NOT fire while logged out until lingering is enabled (needs sudo) |

### Current published cache (baseline for the shrink guard)

`https://github.com/Jsakkos/engram/releases/tag/subtitle-cache-latest`, assets last updated 2026-07-08:

- `cache_format_version` `3`, `content_version` `2026-07-07`
- **467 shows / 36,742 episodes**, tarball 304,296,796 bytes

Laptop corpus on disk today: 642 show dirs, 37,867 SRT files.

### The exit-code contract this plan depends on

`backend/scripts/build_subtitle_cache.py` documents (module docstring, lines 17-33):

- `0`: full corpus completed, tarball safe to publish
- `1`: nothing usable produced
- `2`: **halted on quota; the tarball is PARTIAL and must not be published**

A wrapper that ignores exit 2 replaces the full published cache with a truncated one. This plan never publishes the build script's own tarball. It publishes a **separately packed** artifact from `pack_subtitle_cache.py`, which walks everything on disk and therefore is complete even on a night the harvest halted early. The exit code still gates whether the harvest is considered healthy and is reported in the summary.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/scripts/publish_guard.py` | Compare a candidate release manifest against the currently published one; exit non-zero on a material shrink | Create |
| `backend/tests/unit/test_publish_guard.py` | Unit tests for the guard's pure comparison logic | Create |
| `deploy/subtitle-cache/harvest.sh` | Nightly wrapper: harvest → pack → guard → publish, with journald-friendly logging | Create |
| `deploy/subtitle-cache/engram-subtitle-cache.service` | systemd user service invoking the wrapper | Create |
| `deploy/subtitle-cache/engram-subtitle-cache.timer` | systemd user timer, daily with `Persistent=true` | Create |
| `deploy/subtitle-cache/engram-subtitle-cache.env.example` | Template for the `0600` secrets file the **user** fills in | Create |
| `docs/development/subtitle-cache-server.md` | Operator runbook: install, cutover, monitoring, recovery | Create |
| `docs/development/subtitle-cache.md` | Existing subtitle-cache doc | Modify: link the server runbook, mark the laptop cadence retired |
| `CHANGELOG.md` | Release notes | Modify: `[Unreleased]` entry |

---

## Task 1: Publish guard, pure comparison logic

**Files:**
- Create: `backend/scripts/publish_guard.py`
- Test: `backend/tests/unit/test_publish_guard.py`

The guard exists because a single bad night must not be able to shrink the published cache for every Engram install. A regression that resolves zero shows still produces a *valid* tarball; only a size comparison catches it.

- [ ] **Step 1: Add the module-loader fixture**

`backend/scripts/` is not an importable package under pytest. Every standalone script in this repo is loaded in tests through a session-scoped fixture built on `_load_script_module` (see `contrib`, `nsc`, `msc`, `ppc`, `psc` in `backend/tests/unit/conftest.py`). Follow that convention rather than a bare `from scripts... import`.

Add to `backend/tests/unit/conftest.py`, next to the other script fixtures:

```python
@pytest.fixture(scope="session")
def pg():
    """The publish_guard.py module, loaded once per pytest session."""
    return _load_script_module("publish_guard")
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/unit/test_publish_guard.py`:

```python
"""Unit tests for the nightly publish shrink guard.

The guard is pure arithmetic over two manifests, so these tests never touch the
network, the real ~/.engram/cache, or the live release.
"""

import pytest


def _manifest(shows: dict[str, dict]) -> dict:
    return {
        "cache_format_version": "3",
        "content_version": "2026-09-08",
        "shows": shows,
    }


def _show(name: str, counts: dict[str, int]) -> dict:
    return {"tmdb_id": 1, "name": name, "seasons": [int(s) for s in counts], "episode_counts": counts}


class TestManifestTotals:
    def test_counts_shows_and_episodes(self, pg):
        m = _manifest({"1": _show("A", {"1": 10, "2": 12}), "2": _show("B", {"1": 5})})
        assert pg.manifest_totals(m) == (2, 27)

    def test_empty_manifest_is_zero(self, pg):
        assert pg.manifest_totals(_manifest({})) == (0, 0)

    def test_missing_shows_key_is_zero(self, pg):
        assert pg.manifest_totals({"cache_format_version": "3"}) == (0, 0)


class TestVerdictForGrowth:
    def test_growth_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(600, 37000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.GROWTH

    def test_identical_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(467, 36742), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True


class TestVerdictForShrink:
    def test_shrink_within_tolerance_is_allowed(self, pg):
        # 36,742 * 0.98 = 36,007.16, so 36,100 is inside tolerance.
        v = pg.verdict_for(candidate=(467, 36100), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.WITHIN_TOLERANCE

    def test_shrink_beyond_tolerance_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(467, 30000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EPISODES_SHRANK
        assert "30000" in v.reason and "36742" in v.reason

    def test_show_count_drop_is_blocked_even_when_episodes_hold(self, pg):
        # A resolution regression can collapse many shows into few while the
        # episode total barely moves. Shows are guarded independently.
        v = pg.verdict_for(candidate=(300, 36700), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.SHOWS_SHRANK


class TestVerdictForNoBaseline:
    def test_no_published_baseline_is_allowed(self, pg):
        # First publish ever, or a release with no manifest asset yet.
        v = pg.verdict_for(candidate=(467, 36742), published=None, tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.NO_BASELINE

    def test_empty_candidate_is_always_blocked(self, pg):
        v = pg.verdict_for(candidate=(0, 0), published=None, tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EMPTY_CANDIDATE


class TestTolerance:
    @pytest.mark.parametrize("tolerance", [-0.01, 1.01])
    def test_out_of_range_tolerance_rejected(self, pg, tolerance):
        with pytest.raises(ValueError):
            pg.verdict_for(candidate=(1, 1), published=(1, 1), tolerance=tolerance)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run from `backend/`:

```bash
uv run pytest tests/unit/test_publish_guard.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'scripts.publish_guard'`.

- [ ] **Step 4: Implement the guard**

Create `backend/scripts/publish_guard.py`:

```python
"""Refuse to publish a subtitle cache that is materially smaller than the live one.

The nightly harvest publishes unattended. A regression that resolves zero shows,
or a corpus directory that failed to mount, still produces a structurally VALID
tarball -- verification passes, the manifest is well-formed, the upload succeeds,
and every Engram install silently downgrades to a smaller cache. Only a size
comparison against what is already published catches that class of failure.

Shows and episodes are guarded independently because they fail independently: a
TMDB-resolution regression collapses the show count while barely moving the
episode total, and a truncated harvest does the reverse.

Usage (from backend/):
    uv run python scripts/publish_guard.py --candidate manifest.json
    uv run python scripts/publish_guard.py --candidate manifest.json --allow-shrink

Exit codes:
    0  Publishing is allowed.
    1  Publishing is blocked (shrink beyond tolerance, or an empty candidate).
    2  The guard could not decide (unreadable candidate manifest).
"""

import argparse
import enum
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAG = "subtitle-cache-latest"
DEFAULT_TOLERANCE = 0.02


class ShrinkVerdict(enum.Enum):
    """Why the guard reached its decision. Values are log-facing strings."""

    GROWTH = "growth"
    WITHIN_TOLERANCE = "within-tolerance"
    EPISODES_SHRANK = "episodes-shrank"
    SHOWS_SHRANK = "shows-shrank"
    NO_BASELINE = "no-baseline"
    EMPTY_CANDIDATE = "empty-candidate"


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    verdict: ShrinkVerdict
    reason: str


def manifest_totals(manifest: dict) -> tuple[int, int]:
    """Return ``(show_count, episode_count)`` for a release manifest."""
    shows = manifest.get("shows") or {}
    episodes = 0
    for entry in shows.values():
        episodes += sum((entry.get("episode_counts") or {}).values())
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
    floor_shows = pub_shows * (1.0 - tolerance)
    floor_eps = pub_eps * (1.0 - tolerance)

    if cand_shows < floor_shows:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.SHOWS_SHRANK,
            reason=(
                f"show count fell from {pub_shows} to {cand_shows}, below the "
                f"{tolerance:.0%} tolerance floor of {floor_shows:.0f}"
            ),
        )
    if cand_eps < floor_eps:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.EPISODES_SHRANK,
            reason=(
                f"episode count fell from {pub_eps} to {cand_eps}, below the "
                f"{tolerance:.0%} tolerance floor of {floor_eps:.0f}"
            ),
        )

    if cand_shows >= pub_shows and cand_eps >= pub_eps:
        return GuardResult(
            allowed=True,
            verdict=ShrinkVerdict.GROWTH,
            reason=(
                f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes"
            ),
        )
    return GuardResult(
        allowed=True,
        verdict=ShrinkVerdict.WITHIN_TOLERANCE,
        reason=(
            f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes "
            f"(inside the {tolerance:.0%} tolerance)"
        ),
    )


def fetch_published_totals(tag: str) -> tuple[int, int] | None:
    """Download the live release manifest and total it. ``None`` if unavailable.

    A missing or unreadable published manifest is NOT an error: the very first
    publish has no baseline. Only the candidate side is required to be readable.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                ["gh", "release", "download", tag, "--pattern", "manifest.json", "--dir", tmp],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        path = Path(tmp) / "manifest.json"
        if not path.is_file():
            return None
        try:
            return manifest_totals(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard against publishing a shrunken cache")
    parser.add_argument("--candidate", required=True, help="Path to the candidate manifest.json")
    parser.add_argument("--cache-tag", default=DEFAULT_TAG, help="Release tag to compare against")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Fraction a count may fall and still publish (default: {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="Report the comparison but always exit 0 (deliberate corpus pruning)",
    )
    args = parser.parse_args()

    try:
        candidate = manifest_totals(json.loads(Path(args.candidate).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"publish-guard: cannot read candidate manifest {args.candidate}: {exc}")
        return 2

    published = fetch_published_totals(args.cache_tag)
    result = verdict_for(candidate, published, args.tolerance)
    print(f"publish-guard: {result.verdict.value}: {result.reason}")

    if result.allowed:
        return 0
    if args.allow_shrink:
        print("publish-guard: --allow-shrink set; publishing anyway")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run from `backend/`:

```bash
uv run pytest tests/unit/test_publish_guard.py -v
```

Expected: 12 passed.

If every test errors with `fixture 'pg' not found`, the conftest fixture from Step 1 was not saved. If the fixture loads but `pg.verdict_for` raises `AttributeError`, the script has a syntax error that `_load_script_module` swallowed into a partially-initialised module; run `uv run python scripts/publish_guard.py --help` to see the real traceback.

- [ ] **Step 6: Verify the CLI runs against the real published manifest**

Run from `backend/` (network access to GitHub required, no quota cost):

```bash
gh release download subtitle-cache-latest --pattern manifest.json --dir /tmp/pg-check
uv run python scripts/publish_guard.py --candidate /tmp/pg-check/manifest.json
```

Expected: `publish-guard: growth: 467 -> 467 shows, 36742 -> 36742 episodes` and exit 0 (the manifest compared against itself is trivially allowed). Confirm with `echo $?`.

- [ ] **Step 7: Lint and format**

Run from `backend/`:

```bash
uv run ruff format scripts/publish_guard.py tests/unit/test_publish_guard.py
uv run ruff check scripts/publish_guard.py tests/unit/test_publish_guard.py
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/publish_guard.py backend/tests/unit/test_publish_guard.py backend/tests/unit/conftest.py
git commit -m "feat(subtitle-cache): guard the rolling release against a shrinking cache"
```

---

## Task 2: The nightly wrapper script

**Files:**
- Create: `deploy/subtitle-cache/harvest.sh`

- [ ] **Step 1: Write the wrapper**

Create `deploy/subtitle-cache/harvest.sh`:

```bash
#!/usr/bin/env bash
# Nightly subtitle-cache harvest for the Engram server deployment.
#
# Sequence: harvest -> pack from disk -> shrink guard -> publish.
#
# WHY PACK SEPARATELY INSTEAD OF PUBLISHING THE BUILD SCRIPT'S TARBALL:
# build_subtitle_cache.py exits 2 when the OpenSubtitles budget guard halts it
# partway, and its tarball then holds ONLY the shows completed before the halt.
# Publishing that would truncate the live cache after a single quota-capped
# night. pack_subtitle_cache.py instead walks everything already on disk and
# downloads nothing, so its artifact is complete on every night -- including
# nights the harvest halted early. The harvest exit code is still reported and
# still decides whether the run is called healthy.
#
# Environment (supplied by the systemd EnvironmentFile, see the runbook):
#   TMDB_API_KEY, OPENSUBTITLES_API_KEY, OPENSUBTITLES_USERNAME,
#   OPENSUBTITLES_PASSWORD
# Optional overrides:
#   ENGRAM_REPO        default: $HOME/engram
#   ENGRAM_SHOW_LIST   default: <repo>/backend/scripts/curated_shows.csv
#   ENGRAM_MAX_DOWNLOADS default: 900
#   ENGRAM_CACHE_TAG   default: subtitle-cache-latest
#   ENGRAM_WORK_DIR    default: $HOME/.engram/harvest
set -o errexit
set -o nounset
set -o pipefail

REPO="${ENGRAM_REPO:-$HOME/engram}"
BACKEND="$REPO/backend"
SHOW_LIST="${ENGRAM_SHOW_LIST:-$BACKEND/scripts/curated_shows.csv}"
MAX_DOWNLOADS="${ENGRAM_MAX_DOWNLOADS:-900}"
CACHE_TAG="${ENGRAM_CACHE_TAG:-subtitle-cache-latest}"
WORK_DIR="${ENGRAM_WORK_DIR:-$HOME/.engram/harvest}"
TARBALL="$WORK_DIR/engram-subtitle-cache.tar.gz"
MANIFEST="$WORK_DIR/manifest.json"

log() { printf '%s harvest: %s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

for var in TMDB_API_KEY OPENSUBTITLES_API_KEY OPENSUBTITLES_USERNAME OPENSUBTITLES_PASSWORD; do
  if [ -z "${!var:-}" ]; then
    log "FATAL: $var is not set; check the EnvironmentFile"
    exit 1
  fi
done

if [ ! -d "$BACKEND" ]; then
  log "FATAL: backend dir not found at $BACKEND"
  exit 1
fi

mkdir -p "$WORK_DIR"
cd "$BACKEND"

# --- 1. Harvest -----------------------------------------------------------
# errexit is disabled around this call ONLY: exit 2 is an expected, non-fatal
# outcome (quota halt) and must not abort the run before packing.
log "starting harvest (max-downloads=$MAX_DOWNLOADS, show-list=$SHOW_LIST)"
set +o errexit
uv run python scripts/build_subtitle_cache.py \
  --show-list "$SHOW_LIST" \
  --max-downloads "$MAX_DOWNLOADS"
harvest_rc=$?
set -o errexit

case "$harvest_rc" in
  0) log "harvest completed the full corpus" ;;
  2) log "harvest halted on quota (exit 2); packing what is on disk anyway" ;;
  *)
    log "FATAL: harvest failed with exit $harvest_rc; not packing or publishing"
    exit "$harvest_rc"
    ;;
esac

# --- 2. Pack from disk ----------------------------------------------------
log "packing cache from disk"
uv run python scripts/pack_subtitle_cache.py --output "$TARBALL"

if [ ! -f "$TARBALL" ] || [ ! -f "$MANIFEST" ]; then
  log "FATAL: pack did not produce $TARBALL and $MANIFEST"
  exit 1
fi

# --- 3. Shrink guard ------------------------------------------------------
log "checking the candidate against the published cache"
if ! uv run python scripts/publish_guard.py --candidate "$MANIFEST" --cache-tag "$CACHE_TAG"; then
  log "FATAL: publish guard blocked the upload; the live release is unchanged"
  exit 1
fi

# --- 4. Publish -----------------------------------------------------------
log "publishing to release $CACHE_TAG"
gh release upload "$CACHE_TAG" "$TARBALL" "$MANIFEST" --clobber --repo Jsakkos/engram
log "published $(stat -c %s "$TARBALL") bytes; harvest exit was $harvest_rc"

# A quota halt is reported as a non-zero run so `systemctl --user status` shows
# it, even though the publish succeeded. The next night resumes from disk.
exit "$harvest_rc"
```

- [ ] **Step 2: Mark it executable in git**

Windows `chmod` does not set the git mode bit; set it explicitly or the server refuses to run it.

```bash
git add deploy/subtitle-cache/harvest.sh
git update-index --chmod=+x deploy/subtitle-cache/harvest.sh
git ls-files -s deploy/subtitle-cache/harvest.sh
```

Expected: the mode column reads `100755`, not `100644`.

- [ ] **Step 3: Shellcheck it**

There is no shellcheck binary or CI gate in this repo; run it through `uvx`:

```bash
uvx --from shellcheck-py shellcheck deploy/subtitle-cache/harvest.sh
```

Expected: no output (clean). `${!var}` indirect expansion is bash-specific and the shebang is `bash`, so SC2154-style warnings should not appear; fix anything that does.

- [ ] **Step 4: Syntax-check without executing**

```bash
bash -n deploy/subtitle-cache/harvest.sh
```

Expected: no output.

- [ ] **Step 5: Verify the missing-credential guard fires**

```bash
env -u TMDB_API_KEY OPENSUBTITLES_API_KEY=x OPENSUBTITLES_USERNAME=x OPENSUBTITLES_PASSWORD=x \
  bash deploy/subtitle-cache/harvest.sh; echo "exit=$?"
```

Expected: `FATAL: TMDB_API_KEY is not set; check the EnvironmentFile` and `exit=1`. Nothing is harvested, packed, or published.

- [ ] **Step 6: Commit**

```bash
git add deploy/subtitle-cache/harvest.sh
git commit -m "feat(subtitle-cache): nightly harvest wrapper for the server deployment"
```

---

## Task 3: systemd user units and the secrets template

**Files:**
- Create: `deploy/subtitle-cache/engram-subtitle-cache.service`
- Create: `deploy/subtitle-cache/engram-subtitle-cache.timer`
- Create: `deploy/subtitle-cache/engram-subtitle-cache.env.example`

- [ ] **Step 1: Write the service unit**

Create `deploy/subtitle-cache/engram-subtitle-cache.service`:

```ini
[Unit]
Description=Engram nightly subtitle-cache harvest and publish
Documentation=https://github.com/Jsakkos/engram/blob/main/docs/development/subtitle-cache-server.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# Written by the operator, mode 0600. Not in git: it carries the
# OpenSubtitles password and the TMDB read token.
EnvironmentFile=%h/.config/engram/subtitle-cache.env
ExecStart=%h/engram/deploy/subtitle-cache/harvest.sh
WorkingDirectory=%h/engram/backend
# uv installs to ~/.local/bin, which a non-login systemd unit does not inherit.
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
# A full corpus sweep from cold takes many hours; the quota guard bounds the
# download volume, not the wall clock. 10h is generous and still bounded.
TimeoutStartSec=10h
# Never let a nightly harvest starve interactive work on this box.
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Write the timer unit**

Create `deploy/subtitle-cache/engram-subtitle-cache.timer`:

```ini
[Unit]
Description=Run the Engram subtitle-cache harvest daily
Documentation=https://github.com/Jsakkos/engram/blob/main/docs/development/subtitle-cache-server.md

[Timer]
# The server runs on Etc/UTC. OpenSubtitles daily quota resets at 00:00 UTC,
# so starting at 02:00 UTC gives the reset room and keeps a full day's budget
# available to one run.
OnCalendar=*-*-* 02:00:00
# Catch up a run missed while the box was down, rather than skipping a day.
Persistent=true
# Stagger against any other 02:00 job on the host.
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write the secrets template**

Create `deploy/subtitle-cache/engram-subtitle-cache.env.example`:

```bash
# Copy to ~/.config/engram/subtitle-cache.env on the harvest server and fill in.
#
#   install -d -m 700 ~/.config/engram
#   install -m 600 /dev/null ~/.config/engram/subtitle-cache.env
#   ${EDITOR:-nano} ~/.config/engram/subtitle-cache.env
#
# systemd EnvironmentFile syntax: KEY=value, no `export`, no shell quoting or
# expansion. A value containing '#' or spaces is taken literally to end of line.
#
# This file is never committed and its contents are never pasted into a
# terminal transcript, an issue, or a chat session.

# TMDB v4 Read Access Token (the long eyJ... JWT, not the short v3 API key).
TMDB_API_KEY=

# OpenSubtitles.com API consumer key (identifies the app).
OPENSUBTITLES_API_KEY=

# OpenSubtitles.com VIP account login (carries the 1000/day download quota).
OPENSUBTITLES_USERNAME=
OPENSUBTITLES_PASSWORD=

# Optional overrides read by harvest.sh:
# ENGRAM_MAX_DOWNLOADS=900
# ENGRAM_CACHE_TAG=subtitle-cache-latest
```

- [ ] **Step 4: Validate the unit files parse**

`systemd-analyze verify` resolves `%h` and unit references, so run it on the server where the paths exist. From the laptop:

```bash
scp deploy/subtitle-cache/engram-subtitle-cache.service deploy/subtitle-cache/engram-subtitle-cache.timer jsakkos@192.168.1.122:/tmp/
ssh jsakkos@192.168.1.122 'systemd-analyze --user verify /tmp/engram-subtitle-cache.service /tmp/engram-subtitle-cache.timer'
```

Expected: warnings about the missing `EnvironmentFile` and `ExecStart` path are acceptable at this stage (Task 5 and Task 6 create them). Any *syntax* error ("Unknown lvalue", "Failed to parse") must be fixed now.

- [ ] **Step 5: Commit**

```bash
git add deploy/subtitle-cache/engram-subtitle-cache.service deploy/subtitle-cache/engram-subtitle-cache.timer deploy/subtitle-cache/engram-subtitle-cache.env.example
git commit -m "feat(subtitle-cache): systemd user timer units for the harvest server"
```

---

## Task 4: Operator runbook

**Files:**
- Create: `docs/development/subtitle-cache-server.md`
- Modify: `docs/development/subtitle-cache.md`

- [ ] **Step 1: Write the runbook**

Create `docs/development/subtitle-cache-server.md`:

````markdown
# Subtitle cache: server deployment

The nightly subtitle-cache harvest runs on the Ubuntu host `pve-docker`
(`jsakkos@192.168.1.122`) under a systemd **user** timer, and publishes to the
rolling `subtitle-cache-latest` GitHub release.

## Why exactly one harvester

The OpenSubtitles daily download quota is **per account**, not per machine, and
each machine keeps its own `subtitle_coverage` table. Two harvesters race for
the same 1,000/day bucket, and the loser records zero-coverage rows for seasons
the winner already holds, skip-listing content that exists. That is the same
defect the 2026-08-31 harvester repair fixed, reintroduced across machines. The
laptop keeps its corpus as a backup but must not run `build_subtitle_cache.py`
again.

## Install

```bash
# 1. uv (absent by default on this host)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Bring the checkout to current main
cd ~/engram && git fetch origin && git checkout main && git pull --ff-only
cd backend && uv sync --no-install-project

# 3. Secrets (YOU write this file; see the template in the repo)
install -d -m 700 ~/.config/engram
install -m 600 /dev/null ~/.config/engram/subtitle-cache.env
${EDITOR:-nano} ~/.config/engram/subtitle-cache.env

# 4. Units
install -d -m 755 ~/.config/systemd/user
install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.service ~/.config/systemd/user/
install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now engram-subtitle-cache.timer
```

**Lingering is required** and needs root:

```bash
sudo loginctl enable-linger jsakkos
```

Without it the user manager is torn down at logout and the timer never fires
while nobody is logged in. Verify with `loginctl show-user jsakkos -p Linger`,
which must print `Linger=yes`.

## Cutover from the laptop

Order matters. The coverage database is what stops the server re-harvesting
36,000 episodes it already has on disk, so it moves with the corpus.

```bash
# From the laptop (Git Bash), corpus first, then coverage:
rsync -av --partial --progress ~/.engram/cache/data/ jsakkos@192.168.1.122:~/.engram/cache/data/
rsync -av --partial --progress ~/.engram/cache/tmdb_cache.sqlite jsakkos@192.168.1.122:~/.engram/cache/
```

Then prove the server ships from disk at zero quota cost before enabling the
timer. See "First run" below.

## First run (supervised)

```bash
systemctl --user start engram-subtitle-cache.service
journalctl --user -u engram-subtitle-cache.service -f
```

What a healthy first run looks like:

- `OpenSubtitles API: ACTIVE` and a real remaining-quota number near 1000
- a long run of seasons shipping from disk without downloads
- `publish-guard: growth: ...` with a show/episode count at or above the
  published baseline
- the `gh release upload` step completing

## Monitoring

| Question | Command |
|---|---|
| Did last night run? | `systemctl --user status engram-subtitle-cache.service` |
| When does it run next? | `systemctl --user list-timers engram-subtitle-cache.timer` |
| What happened? | `journalctl --user -u engram-subtitle-cache.service --since yesterday` |
| Is the release fresh? | `gh release view subtitle-cache-latest --repo Jsakkos/engram` |

## Reading the exit codes

The service's exit status is the **harvest** exit code, so a publish can
succeed on a run systemd reports as failed. That is deliberate.

| Exit | Meaning | Published? | Action |
|---|---|---|---|
| 0 | Full corpus harvested | Yes | None |
| 2 | Quota guard halted the harvest partway | Yes, from disk | None; the next night resumes |
| 1 | Missing credentials, missing backend, pack failure, or the shrink guard blocked the upload | No | Read the journal |

## Recovery

**The shrink guard blocked the upload.** The live release is untouched, which
is the point. Compare by hand:

```bash
cd ~/engram/backend
uv run python scripts/publish_guard.py --candidate ~/.engram/harvest/manifest.json
```

If the shrink is deliberate (a corpus pruning), re-run the publish with
`--allow-shrink`, then upload manually with `gh release upload
subtitle-cache-latest ... --clobber`. If it is not deliberate, do not override
it. Find out why the corpus lost content first.

**Quota exhausted every night.** Check the true remaining quota; the login
response's `allowed_downloads` is the daily CAP, not the remainder. A run that
logs `OS quota left: 0` has nothing to do but wait ~24h.

**Rolling back a bad publish.** There is no history on a rolling release asset.
Re-pack from a known-good corpus and `--clobber` over it.
````

- [ ] **Step 2: Link the runbook from the existing doc**

In `docs/development/subtitle-cache.md`, find the section describing the local daily build cadence and add immediately after its heading:

```markdown
> **The daily build now runs on the server, not a laptop.** See
> [Subtitle cache: server deployment](subtitle-cache-server.md). OpenSubtitles
> quota is per-account, so exactly one machine may harvest; running the build
> locally while the server timer is enabled corrupts both machines' coverage
> records.
```

- [ ] **Step 3: Verify the docs build**

Run from the repo ROOT (not `backend/`):

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Expected: build succeeds. Roughly 18 pre-existing warnings are normal; do not add `--strict`. Confirm no new warning names `subtitle-cache-server.md`.

- [ ] **Step 4: Check for em dashes**

House style forbids them. The byte-based check avoids false positives on arrows and ellipses:

`grep -P` fails in this repo's Git Bash with "supports only unibyte and UTF-8 locales", so check with Python instead:

```bash
uv run python -c "import io,sys; bad=[(f,i) for f in sys.argv[1:] for i,l in enumerate(io.open(f,encoding='utf-8'),1) if chr(8212) in l or chr(8211) in l]; print(bad or 'clean')" docs/development/subtitle-cache-server.md docs/development/subtitle-cache.md
```

Expected: `clean`. House style forbids em and en dashes; use colons, commas, semicolons, or parentheses.

- [ ] **Step 5: Commit**

```bash
git add docs/development/subtitle-cache-server.md docs/development/subtitle-cache.md
git commit -m "docs(subtitle-cache): server deployment runbook"
```

---

## Task 5: Server bootstrap

These steps run over SSH against `jsakkos@192.168.1.122`. Nothing here consumes OpenSubtitles quota.

**Files:** none in the repo.

- [ ] **Step 1: Install uv**

```bash
ssh jsakkos@192.168.1.122 'curl -LsSf https://astral.sh/uv/install.sh | sh'
ssh jsakkos@192.168.1.122 '~/.local/bin/uv --version'
```

Expected: a version string, e.g. `uv 0.9.x`.

- [ ] **Step 2: Upgrade the checkout from v0.8.1 to main**

The checkout is ~60 commits of schema migrations behind. Confirm it is clean before moving it.

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram && git status --short && git stash list'
```

Expected: both empty. If not, stop and ask before discarding anything.

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram && git fetch origin && git checkout main && git pull --ff-only && git describe --tags'
```

Expected: a current tag (`v0.35.0` or later).

- [ ] **Step 3: Sync dependencies**

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && ~/.local/bin/uv sync --no-install-project'
```

Expected: uv resolves and installs; no error. This is Python 3.12.3 against a 3.11 target, which the project supports.

- [ ] **Step 4: Prove the scripts import and their CLIs are wired**

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && ~/.local/bin/uv run python scripts/build_subtitle_cache.py --help | head -5'
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && ~/.local/bin/uv run python scripts/pack_subtitle_cache.py --help | head -5'
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && ~/.local/bin/uv run python scripts/publish_guard.py --help | head -5'
```

Expected: three usage blocks, no traceback. A `--max-downloads` line must appear in the first (it proves the repaired build script, not a stale one, is checked out).

- [ ] **Step 5: Confirm gh can reach the release**

```bash
ssh jsakkos@192.168.1.122 'gh release view subtitle-cache-latest --repo Jsakkos/engram --json tagName,assets --jq "{tag:.tagName, assets:[.assets[].name]}"'
```

Expected: the tag and both asset names. This confirms the `repo` scope suffices for the read side; the write side is exercised in Task 7.

---

## Task 6: Secrets and units on the server

**The operator writes the credentials file. Credentials are not to be copied, echoed, or handled on the operator's behalf.**

- [ ] **Step 1: Copy the template to the server**

```bash
scp deploy/subtitle-cache/engram-subtitle-cache.env.example jsakkos@192.168.1.122:/tmp/
ssh jsakkos@192.168.1.122 'install -d -m 700 ~/.config/engram && install -m 600 /tmp/engram-subtitle-cache.env.example ~/.config/engram/subtitle-cache.env && rm /tmp/engram-subtitle-cache.env.example'
```

- [ ] **Step 2: USER STEP, fill in the four credentials**

The operator, at their own terminal:

```bash
ssh jsakkos@192.168.1.122
${EDITOR:-nano} ~/.config/engram/subtitle-cache.env
```

The four values (`TMDB_API_KEY`, `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD`) match what the laptop uses. `TMDB_API_KEY` is the long `eyJ...` v4 Read Access Token, not the short v3 key.

- [ ] **Step 3: Verify shape without revealing values**

```bash
ssh jsakkos@192.168.1.122 'stat -c "%a %n" ~/.config/engram/subtitle-cache.env; awk -F= "/^[A-Z]/ {printf \"%s len=%d\n\", \$1, length(\$2)}" ~/.config/engram/subtitle-cache.env'
```

Expected: mode `600`, and four non-zero lengths. `TMDB_API_KEY len=` should be around 200+ (a v4 token); a length near 32 means the wrong TMDB key was pasted.

- [ ] **Step 4: Install the units**

```bash
ssh jsakkos@192.168.1.122 'install -d -m 755 ~/.config/systemd/user && install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.service ~/.config/systemd/user/ && install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.timer ~/.config/systemd/user/ && systemctl --user daemon-reload'
ssh jsakkos@192.168.1.122 'systemd-analyze --user verify ~/.config/systemd/user/engram-subtitle-cache.service'
```

Expected: no output from `verify` (all referenced paths now exist).

- [ ] **Step 5: USER STEP, enable lingering (requires sudo password)**

The operator, at their own terminal:

```bash
ssh -t jsakkos@192.168.1.122 'sudo loginctl enable-linger jsakkos'
```

Then confirm:

```bash
ssh jsakkos@192.168.1.122 'loginctl show-user jsakkos -p Linger'
```

Expected: `Linger=yes`. Without this the timer will not fire while nobody is logged in, and the deployment silently does nothing.

- [ ] **Step 6: Confirm the harvest script is executable on the server**

```bash
ssh jsakkos@192.168.1.122 'test -x ~/engram/deploy/subtitle-cache/harvest.sh && echo EXECUTABLE || echo NOT_EXECUTABLE'
```

Expected: `EXECUTABLE`. If not, the git mode bit from Task 2 Step 2 did not land; fix it in the repo and `git pull` on the server rather than `chmod`-ing in place, or the next pull reverts it.

---

## Task 7: Cutover

This is the step that must not be run twice or half-run: from here, the server owns the corpus and the laptop must stop harvesting.

- [ ] **Step 1: Confirm no laptop harvest is running**

On the laptop:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime, Path
```

Expected: no `build_subtitle_cache.py` process. Wait for one to finish rather than killing it mid-write; the coverage DB is being written.

- [ ] **Step 2: rsync the SRT corpus**

From the laptop (Git Bash). 1.7 GB, 37,867 files, so expect this to take a while; `--partial` makes it resumable.

```bash
rsync -av --partial --info=progress2 ~/.engram/cache/data/ jsakkos@192.168.1.122:~/.engram/cache/data/
```

Expected: transfer completes. Verify the file count matches:

```bash
ssh jsakkos@192.168.1.122 'find ~/.engram/cache/data -name "*.srt" | wc -l'
```

Expected: `37867` (or higher if the laptop harvested more in the interim). A materially lower number means an interrupted transfer; re-run the rsync.

- [ ] **Step 3: rsync the coverage database**

This carries `subtitle_coverage`. Without it the server re-measures every season and burns weeks of quota re-downloading what it already has on disk.

```bash
rsync -av --partial ~/.engram/cache/tmdb_cache.sqlite jsakkos@192.168.1.122:~/.engram/cache/
ssh jsakkos@192.168.1.122 'python3 -c "import sqlite3; c=sqlite3.connect(\"/home/jsakkos/.engram/cache/tmdb_cache.sqlite\"); print(\"coverage rows:\", c.execute(\"select count(*) from subtitle_coverage\").fetchone()[0])"'
```

Expected: `coverage rows: 2392` (or the laptop's current count; check it first with the same query locally, they must match).

- [ ] **Step 4: Prove a dry pack ships from disk at zero quota cost**

Pack only, no harvest, no publish. This exercises the corpus, the TMDB resolution path, and the artifact verifier without touching OpenSubtitles.

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && set -a && . ~/.config/engram/subtitle-cache.env && set +a && ~/.local/bin/uv run python scripts/pack_subtitle_cache.py --output ~/.engram/harvest/engram-subtitle-cache.tar.gz'
```

Expected, in the tail of the output: `Verifying artifact...` followed by a `Packed NNN shows, NNNNN episodes` line. The show count should be in the neighbourhood of 640 and the episode count at or above 36,742 (the published baseline). A count far below that means the rsync did not land everything, so stop and re-check Step 2.

- [ ] **Step 5: Run the shrink guard against that artifact**

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram/backend && ~/.local/bin/uv run python scripts/publish_guard.py --candidate ~/.engram/harvest/manifest.json; echo "exit=$?"'
```

Expected: `publish-guard: growth: 467 -> NNN shows, 36742 -> NNNNN episodes` and `exit=0`. **If this prints a blocked verdict, do not continue to Step 6**: the server's corpus is smaller than what is already published, which means the cutover is incomplete.

- [ ] **Step 6: First supervised service run**

```bash
ssh jsakkos@192.168.1.122 'systemctl --user start engram-subtitle-cache.service'
ssh jsakkos@192.168.1.122 'journalctl --user -u engram-subtitle-cache.service -n 200 --no-pager'
```

Expected in the journal: the harvest start line, `OpenSubtitles API: ACTIVE` with a real remaining-quota number, the pack, a `publish-guard: growth:` line, and `publishing to release subtitle-cache-latest`. This is the step that proves the `gh` token can write to releases.

- [ ] **Step 7: Confirm the published release actually moved**

From the laptop:

```bash
gh api repos/Jsakkos/engram/releases/tags/subtitle-cache-latest --jq '.assets[]|"\(.name) \(.size) updated=\(.updated_at)"'
```

Expected: today's date in `updated_at` on both assets, and a tarball size at or above 304,296,796 bytes.

- [ ] **Step 8: Enable the timer**

```bash
ssh jsakkos@192.168.1.122 'systemctl --user enable --now engram-subtitle-cache.timer && systemctl --user list-timers engram-subtitle-cache.timer --no-pager'
```

Expected: a `NEXT` column showing tomorrow at ~02:00 UTC.

- [ ] **Step 9: Retire the laptop harvester**

The laptop keeps its corpus as a backup. It must not harvest again while the server timer is live: two harvesters race for one quota bucket and poison each other's coverage records.

On the laptop, confirm nothing schedules it:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like "*engram*" -or $_.TaskName -like "*subtitle*" }
```

Expected: no matching task (the cadence was manual). If one exists, disable it.

---

## Verification

- [ ] **Full backend unit suite**

Run from `backend/`:

```bash
uv run pytest tests/unit/ -q
```

Expected: all pass. This tier takes several minutes; do not run it inside a subagent with a short timeout.

- [ ] **Lint and format the whole backend**

```bash
uv run ruff format --check .
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Two nights of unattended operation**

The real acceptance test is that the timer fires without a human present.

```bash
ssh jsakkos@192.168.1.122 'journalctl --user -u engram-subtitle-cache.service --since "2 days ago" | grep -E "harvest:|publish-guard:"'
```

Expected: two runs, each ending in either a publish or a clearly-explained guard block. An exit-2 night is a healthy outcome, not a failure.

- [ ] **The published cache is fresh and has not shrunk**

```bash
gh api repos/Jsakkos/engram/releases/tags/subtitle-cache-latest --jq '.assets[]|"\(.name) \(.size) updated=\(.updated_at)"'
```

Expected: `updated_at` within the last 48 hours, tarball at or above the 304,296,796-byte baseline.

- [ ] **CHANGELOG entry**

Add to the `[Unreleased]` section of `CHANGELOG.md`:

```markdown
### Added

- Subtitle cache harvesting now runs unattended on a server via a systemd user
  timer, with a shrink guard that refuses to replace the published cache with a
  materially smaller one. See `docs/development/subtitle-cache-server.md`.
```

Then commit:

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for the subtitle-cache server deployment"
```

---

## Risks

- **The server is now a single point of failure for the published cache.** It is a VM on a box that also runs Docker workloads. Mitigation: the laptop keeps the corpus, and the runbook's recovery section covers re-packing from it. Not mitigated: nobody is alerted when the timer silently stops. Monitoring is a `list-timers` check by hand.
- **Lingering is the quiet failure mode.** If `enable-linger` is skipped, everything above appears to succeed and the timer simply never fires while logged out. Task 6 Step 5 is the only guard against it.
- **The `gh` token is a user token with `repo` scope**, so a compromised server can write to every repo the account can. Acceptable for a home LAN box; worth revisiting if the host's exposure changes.
- **The v0.8.1 → main upgrade crosses ~60 commits of schema migrations.** The cache builder does not depend on the deployment's application database, and that deployment is not in active use, but the upgrade should be observed rather than assumed. Schema reconciler faults were seen on the dev DB during the Phase 1 diagnostic (`no such column: app_config.always_review`, then `table fingerprint_contributions already exists`), and the server will traverse the same path.
- **Re-measuring the purged coverage window costs quota.** Roughly 978 season rows returned to the unmeasured pool. At 900/day that is several days of harvesting before the corpus stops growing. Expected, one-time.

## Out of scope

- Phase 2 (English-only curation of `curated_shows.csv`): independent, gets its own plan.
- Alerting on a stalled timer.
- Sharding the tarball if it grows past a few hundred MB more. Noted in the design, not designed.

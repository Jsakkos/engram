#!/usr/bin/env bash
# Nightly subtitle-cache harvest for the Engram server deployment.
#
# Sequence: lock -> preflight -> harvest -> pack from disk -> shrink guard ->
# publish with retries -> verify the published assets.
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
# EXIT CODES (the systemd unit must distinguish them):
#   0   published; the harvest completed the full corpus
#   10  published successfully, but the harvest halted on quota. With
#       ENGRAM_MAX_DOWNLOADS well under the account's daily cap this is the
#       NORMAL nightly result, not a failure. The accompanying systemd service
#       unit is expected to carry `SuccessExitStatus=10` so a healthy
#       quota-halted night does not show up as a failed unit and train the
#       operator to ignore real failures.
#   20  the shrink guard blocked the upload; the live release is unchanged
#   21  the shrink guard could not decide; nothing was published
#   1   anything else: preflight failure, missing backend, lock held, harvest
#       failure, pack failure, or an upload/verification failure
#   143 terminated by SIGTERM (systemd TimeoutStartSec, or a manual stop)
#
# Environment (supplied by the systemd EnvironmentFile, see the runbook):
#   TMDB_API_KEY, OPENSUBTITLES_API_KEY, OPENSUBTITLES_USERNAME,
#   OPENSUBTITLES_PASSWORD
# Optional overrides:
#   ENGRAM_REPO        default: $HOME/engram
#   ENGRAM_REPO_SLUG   default: Jsakkos/engram (GitHub owner/name, must match
#                      the repo the publish guard reads its baseline from and
#                      the repo the tarball is uploaded to)
#   ENGRAM_SHOW_LIST   default: <repo>/backend/scripts/curated_shows.csv
#   ENGRAM_MAX_DOWNLOADS default: 900
#   ENGRAM_CACHE_TAG   default: subtitle-cache-latest
#   ENGRAM_WORK_DIR    default: $HOME/.engram/harvest
#   ENGRAM_MIN_FREE_KB default: 1048576 (1 GiB, roughly twice the artifact)
#   ENGRAM_UPLOAD_ATTEMPTS default: 3
#   ENGRAM_UPLOAD_BACKOFF  default: 30 (seconds, multiplied by attempt number)
set -o errexit
set -o nounset
set -o pipefail

EXIT_QUOTA_HALT=10
EXIT_GUARD_BLOCKED=20
EXIT_GUARD_UNDECIDED=21

REPO="${ENGRAM_REPO:-${HOME:?HOME must be set}/engram}"
GH_REPO="${ENGRAM_REPO_SLUG:-Jsakkos/engram}"
BACKEND="$REPO/backend"
SHOW_LIST="${ENGRAM_SHOW_LIST:-$BACKEND/scripts/curated_shows.csv}"
MAX_DOWNLOADS="${ENGRAM_MAX_DOWNLOADS:-900}"
CACHE_TAG="${ENGRAM_CACHE_TAG:-subtitle-cache-latest}"
WORK_DIR="${ENGRAM_WORK_DIR:-${HOME:?HOME must be set}/.engram/harvest}"
MIN_FREE_KB="${ENGRAM_MIN_FREE_KB:-1048576}"
UPLOAD_ATTEMPTS="${ENGRAM_UPLOAD_ATTEMPTS:-3}"
UPLOAD_BACKOFF="${ENGRAM_UPLOAD_BACKOFF:-30}"

log() { printf '%s harvest: %s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# Every errexit abort would otherwise die with bare shell noise and no
# `harvest:` marker, so `journalctl | grep harvest:` showed a start banner and
# then nothing at all.
# shellcheck disable=SC2329  # invoked indirectly, from the ERR trap below.
err_report() {
  log "FATAL: unexpected failure at line $2 (exit $1); nothing was published"
  exit "$1"
}
trap 'err_report "$?" "$LINENO"' ERR

# The metered and networked calls below disable errexit deliberately so their
# exit codes can be classified. The ERR trap has to come down with them, or it
# fires on the very failures those blocks exist to handle. Both halves are
# written out inline at every site on purpose: bash saves and restores the ERR
# trap around a function call (errtrace is off), so a `trap - ERR` issued from
# inside a helper function is silently undone the moment the helper returns.

trap 'log "terminated by signal; nothing was packed or published"; exit 143' TERM

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "FATAL: required tool '$1' not found on PATH; $2"
    exit 1
  fi
}

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
# Resolve once. pack_subtitle_cache.py derives its manifest path from
# Path(args.output).resolve(), so behind a symlinked work dir an unresolved
# $MANIFEST here points at a different file than the packer writes, and the
# mismatch surfaces only after a full night of harvesting.
WORK_DIR="$(realpath "$WORK_DIR")"
TARBALL="$WORK_DIR/engram-subtitle-cache.tar.gz"
MANIFEST="$WORK_DIR/manifest.json"
LOCK_FILE="$WORK_DIR/.harvest.lock"

# --- 0. Single-instance lock ----------------------------------------------
# Type=oneshot only dedupes the unit against itself. It does not stop a manual
# run (the documented debugging path) landing on top of the timer's run, and
# both share $WORK_DIR: one run streaming $TARBALL to gh while the other's
# packer rewrites that same path publishes a torn tarball next to a manifest
# describing a different build, with both runs reporting success. They would
# also race the same metered quota.
require_cmd flock "install util-linux"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "FATAL: another harvest is already running (lock held); exiting without touching the quota"
  exit 1
fi

# --- 0b. Preflight ---------------------------------------------------------
# A missing or unauthenticated gh used to cost a full metered harvest before
# anything noticed, so every publishing prerequisite is proven up front.
# flock was already proven above, at the lock itself.
require_cmd uv "install uv to ~/.local/bin and check the unit's PATH="
require_cmd gh "install the GitHub CLI"

trap - ERR
set +o errexit
gh auth status --hostname github.com >/dev/null 2>&1
auth_rc=$?
set -o errexit
trap 'err_report "$?" "$LINENO"' ERR
if [ "$auth_rc" -ne 0 ]; then
  log "FATAL: gh is not authenticated for github.com (exit $auth_rc); run 'gh auth login' as this user"
  exit 1
fi

if [ ! -f "$SHOW_LIST" ]; then
  log "FATAL: show list not found at $SHOW_LIST"
  exit 1
fi

free_kb="$(df -Pk "$WORK_DIR" | awk 'NR == 2 { print $4 }')"
if [ -z "$free_kb" ]; then
  log "FATAL: could not read free space for $WORK_DIR"
  exit 1
fi
if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
  log "FATAL: only ${free_kb}KB free on $WORK_DIR's filesystem, need ${MIN_FREE_KB}KB; a full disk otherwise surfaces as a tarfile traceback after the quota is spent"
  exit 1
fi
log "preflight ok (uv, gh, flock, show list, ${free_kb}KB free)"

cd "$BACKEND"

# --- 1. Harvest -----------------------------------------------------------
# errexit is disabled around this call ONLY: exit 2 is an expected, non-fatal
# outcome (quota halt) and must not abort the run before packing.
log "starting harvest (max-downloads=$MAX_DOWNLOADS, show-list=$SHOW_LIST)"
trap - ERR
set +o errexit
uv run python scripts/build_subtitle_cache.py \
  --show-list "$SHOW_LIST" \
  --max-downloads "$MAX_DOWNLOADS"
harvest_rc=$?
set -o errexit
trap 'err_report "$?" "$LINENO"' ERR

case "$harvest_rc" in
  0) log "harvest completed the full corpus" ;;
  2) log "harvest halted on quota (exit 2); packing what is on disk anyway" ;;
  *)
    log "FATAL: harvest failed with exit $harvest_rc; not packing or publishing"
    exit 1
    ;;
esac

# --- 2. Pack from disk ----------------------------------------------------
# $WORK_DIR is never cleaned, so without this removal the freshness check below
# is satisfied by last night's leftovers and cannot tell "the pack wrote
# nothing" from "yesterday's files are still sitting here".
log "packing cache from disk"
rm -f "$TARBALL" "$MANIFEST"
uv run python scripts/pack_subtitle_cache.py --output "$TARBALL"

if [ ! -f "$TARBALL" ] || [ ! -f "$MANIFEST" ]; then
  log "FATAL: pack did not produce $TARBALL and $MANIFEST"
  exit 1
fi

# --- 3. Shrink guard ------------------------------------------------------
# Any non-zero exit means do not publish. Exit 1 is a deliberate block (the
# candidate shrank); exit 2 means the guard could not decide (a gh failure,
# a broken manifest, or an untrustworthy published baseline) and is just as
# disqualifying. Capture the exit code so the log distinguishes the two.
log "checking the candidate against the published cache"
trap - ERR
set +o errexit
uv run python scripts/publish_guard.py --candidate "$MANIFEST" --cache-tag "$CACHE_TAG" --repo "$GH_REPO"
guard_rc=$?
set -o errexit
trap 'err_report "$?" "$LINENO"' ERR

if [ "$guard_rc" -ne 0 ]; then
  case "$guard_rc" in
    1)
      log "FATAL: guard blocked the upload (exit 1); the live release is unchanged"
      exit "$EXIT_GUARD_BLOCKED"
      ;;
    2)
      log "FATAL: guard could not decide (exit 2); the live release is unchanged"
      exit "$EXIT_GUARD_UNDECIDED"
      ;;
    *)
      log "FATAL: guard exited $guard_rc; the live release is unchanged"
      exit 1
      ;;
  esac
fi

# --- 4. Publish -----------------------------------------------------------
# `gh --clobber` DELETES the conflicting asset and then uploads the new one, so
# a failure between the two asset uploads leaves the release either with no
# tarball at all (every install's download 404s) or with a fresh tarball beside
# last night's manifest.json, whose tarball_sha256 no longer matches. The
# client compares those and silently discards the download, so installs quietly
# stop updating with only a debug-level client log. Hence: retry, then verify.
upload_ok=0
attempt=1
while [ "$attempt" -le "$UPLOAD_ATTEMPTS" ]; do
  log "publishing to release $CACHE_TAG (attempt $attempt/$UPLOAD_ATTEMPTS)"
  trap - ERR
  set +o errexit
  gh release upload "$CACHE_TAG" "$TARBALL" "$MANIFEST" --clobber --repo "$GH_REPO"
  upload_rc=$?
  set -o errexit
  trap 'err_report "$?" "$LINENO"' ERR

  if [ "$upload_rc" -eq 0 ]; then
    upload_ok=1
    break
  fi
  log "upload attempt $attempt failed (exit $upload_rc)"
  if [ "$attempt" -lt "$UPLOAD_ATTEMPTS" ]; then
    backoff=$((attempt * UPLOAD_BACKOFF))
    log "retrying in ${backoff}s"
    sleep "$backoff"
  fi
  attempt=$((attempt + 1))
done

if [ "$upload_ok" -ne 1 ]; then
  log "FATAL: upload failed after $UPLOAD_ATTEMPTS attempts; release $CACHE_TAG may now be INCONSISTENT (missing asset, or tarball and manifest.json from different builds); re-upload $TARBALL and $MANIFEST by hand"
  exit 1
fi

# --- 5. Verify what is actually on the release ----------------------------
tarball_name="$(basename "$TARBALL")"
manifest_name="$(basename "$MANIFEST")"
trap - ERR
set +o errexit
asset_lines="$(gh release view "$CACHE_TAG" --repo "$GH_REPO" --json assets --jq '.assets[] | "\(.name) \(.size)"')"
verify_rc=$?
set -o errexit
trap 'err_report "$?" "$LINENO"' ERR

if [ "$verify_rc" -ne 0 ]; then
  log "FATAL: could not verify release $CACHE_TAG after upload (gh exit $verify_rc); release $CACHE_TAG may now be INCONSISTENT (missing asset, or tarball and manifest.json from different builds); re-upload $TARBALL and $MANIFEST by hand"
  exit 1
fi

tarball_asset="$(printf '%s\n' "$asset_lines" | awk -v n="$tarball_name" '$1 == n { print; exit }')"
manifest_asset="$(printf '%s\n' "$asset_lines" | awk -v n="$manifest_name" '$1 == n { print; exit }')"
if [ -z "$tarball_asset" ] || [ -z "$manifest_asset" ]; then
  log "FATAL: post-upload check did not find both assets on $CACHE_TAG (saw: ${asset_lines//$'\n'/, }); release $CACHE_TAG may now be INCONSISTENT (missing asset, or tarball and manifest.json from different builds); re-upload $TARBALL and $MANIFEST by hand"
  exit 1
fi

log "verified release assets: $tarball_asset bytes, $manifest_asset bytes"
log "published $(stat -c %s "$TARBALL") local bytes; harvest exit was $harvest_rc"

if [ "$harvest_rc" -eq 2 ]; then
  log "UPLOAD SUCCEEDED. The harvest stopped early only because it hit its download budget, which is the normal nightly outcome; the published cache is complete. DO NOT re-run the harvest today: a second run burns the remaining quota and can race the timer's run."
  exit "$EXIT_QUOTA_HALT"
fi

exit 0

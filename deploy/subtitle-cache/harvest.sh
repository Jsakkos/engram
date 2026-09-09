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
#   ENGRAM_REPO_SLUG   default: Jsakkos/engram (GitHub owner/name, must match
#                      the repo the publish guard reads its baseline from and
#                      the repo the tarball is uploaded to)
#   ENGRAM_SHOW_LIST   default: <repo>/backend/scripts/curated_shows.csv
#   ENGRAM_MAX_DOWNLOADS default: 900
#   ENGRAM_CACHE_TAG   default: subtitle-cache-latest
#   ENGRAM_WORK_DIR    default: $HOME/.engram/harvest
set -o errexit
set -o nounset
set -o pipefail

REPO="${ENGRAM_REPO:-$HOME/engram}"
GH_REPO="${ENGRAM_REPO_SLUG:-Jsakkos/engram}"
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
# Any non-zero exit means do not publish. Exit 1 is a deliberate block (the
# candidate shrank); exit 2 means the guard could not decide (a gh failure,
# a broken manifest, or an untrustworthy published baseline) and is just as
# disqualifying. Capture the exit code so the log distinguishes the two.
log "checking the candidate against the published cache"
set +o errexit
uv run python scripts/publish_guard.py --candidate "$MANIFEST" --cache-tag "$CACHE_TAG" --repo "$GH_REPO"
guard_rc=$?
set -o errexit

if [ "$guard_rc" -ne 0 ]; then
  case "$guard_rc" in
    1) log "FATAL: guard blocked the upload (exit 1); the live release is unchanged" ;;
    2) log "FATAL: guard could not decide (exit 2); the live release is unchanged" ;;
    *) log "FATAL: guard exited $guard_rc; the live release is unchanged" ;;
  esac
  exit "$guard_rc"
fi

# --- 4. Publish -----------------------------------------------------------
log "publishing to release $CACHE_TAG"
gh release upload "$CACHE_TAG" "$TARBALL" "$MANIFEST" --clobber --repo "$GH_REPO"
log "published $(stat -c %s "$TARBALL") bytes; harvest exit was $harvest_rc"

# A quota halt is reported as a non-zero run so `systemctl --user status` shows
# it, even though the publish succeeded. The next night resumes from disk.
exit "$harvest_rc"

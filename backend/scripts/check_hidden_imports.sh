#!/usr/bin/env bash
#
# check_hidden_imports.sh - fail when PyInstaller reports an unresolved hidden
# import that was not already known to be benign.
#
# Usage:
#   backend/scripts/check_hidden_imports.sh <pyinstaller-build.log> [baseline]
#
# Baseline defaults to pyinstaller-known-warnings.txt next to this script's
# parent (backend/). Writes found.txt / known.txt beside the log for artifacts.
#
# Why a baseline diff and not a pattern match: an unresolved hidden import is
# only a WARNING to PyInstaller, because hooks declare imports speculatively
# across library versions, so several are permanently expected. The scipy 1.17.1
# breakage was a warning APPEARING, not a specific warning existing.
# scipy._lib.array_api_compat.numpy.fft resolved silently until scipy moved the
# vendored package; PyInstaller's own hook still names that dead path, so the
# warning is now permanent and says nothing about whether the bundle is correct.
# What is actionable is "a warning showed up that was not there before", which
# only a baseline can express.
#
# Exit codes: 0 pass, 1 new unresolved imports, 2 usage error.

set -uo pipefail

LOG="${1:-}"
if [ -z "$LOG" ]; then
  echo "usage: $0 <pyinstaller-build.log> [baseline]" >&2
  exit 2
fi
if [ ! -f "$LOG" ]; then
  echo "build log not found: $LOG" >&2
  exit 1
fi

BASELINE="${2:-$(dirname "$0")/../pyinstaller-known-warnings.txt}"
if [ ! -f "$BASELINE" ]; then
  echo "baseline not found: $BASELINE" >&2
  exit 1
fi

dir=$(dirname "$LOG")
found="$dir/found.txt"
known="$dir/known.txt"

# Both quote styles appear: WARNING lines use double quotes, the occasional
# ERROR line uses single quotes.
grep -oE "Hidden import [\"'][^\"']+[\"'] not found" "$LOG" \
  | sed -E "s/.*[\"']([^\"']+)[\"'].*/\1/" \
  | sort -u > "$found"
grep -vE '^[[:space:]]*(#|$)' "$BASELINE" | sort -u > "$known"

echo "===== unresolved hidden imports ($(wc -l < "$found" | tr -d ' ')) ====="
cat "$found"
echo "=============================================="

# Stale baseline entries are informational: a hook dropping an import it used to
# declare is not a problem, it just means the baseline can be tidied.
stale=$(comm -13 "$found" "$known")
if [ -n "$stale" ]; then
  echo "NOTE: baseline entries no longer reported (safe to prune from $BASELINE):"
  printf '%s\n' "$stale" | sed 's/^/  /'
fi

new=$(comm -23 "$found" "$known")
if [ -n "$new" ]; then
  echo
  echo "ERROR: hidden imports that used to resolve are now unresolved:"
  printf '%s\n' "$new" | sed 's/^/  /'
  echo
  echo "A dependency has probably moved or removed a module that a PyInstaller"
  echo "hook still names. The bundle will BUILD anyway and can die at runtime,"
  echo "which is exactly how v0.29.0-v0.31.0 shipped a binary that never started."
  echo
  echo "Decide whether the frozen app needs that module. If it does, declare the"
  echo "real path in backend/engram.spec. If it is genuinely optional, add it to"
  echo "$BASELINE with a note explaining why."
  exit 1
fi

echo "OK: no new unresolved hidden imports."
